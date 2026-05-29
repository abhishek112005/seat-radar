"""
Continuous monitoring service for SeatRadar v2.

New in v2:
  • Uses Database (SQLite) instead of JSON-file WatchStore
  • Feature 6  — health watchdog: restarts monitor loop if heartbeat stalls > 60 s
  • Feature 7  — adaptive polling: 2 s / 30 s / 5 min based on expected_open_time
  • Feature 9  — broadcasts every check result via WebSocket callback
  • Feature 4  — auto-pauses watch on CAPTCHA, auto-resumes after 10 min
  • Feature 12 — structured JSON logging via python-json-logger
  • PlaywrightRunner (Windows ProactorEventLoop fix) retained
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pythonjsonlogger import jsonlogger

from db.database import Database
from models import ScrapeSnapshot, WatchConfig, utcnow_iso
from notifiers.dispatcher import NotificationDispatcher
from scraper.bookmyshow import BookMyShowScraper, CaptchaDetected


# ── structured logging (Feature 12) ──────────────────────────────────────────

def setup_logging(log_dir: str = "logs") -> None:
    """Configure JSON + console logging with per-subsystem log files."""
    Path(log_dir).mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    json_fmt = jsonlogger.JsonFormatter(fmt)

    root = logging.getLogger()
    if root.handlers:        # already configured (e.g. called twice)
        return
    root.setLevel(logging.DEBUG)

    # Console handler — human-readable
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(console)

    def _file(name: str, filename: str) -> None:
        h = logging.FileHandler(f"{log_dir}/{filename}")
        h.setLevel(logging.DEBUG)
        h.setFormatter(json_fmt)
        logging.getLogger(name).addHandler(h)

    _file("scraper",   "scraper.log")
    _file("notifiers", "notifier.log")

    # App-level JSON log
    app_h = logging.FileHandler(f"{log_dir}/app.log")
    app_h.setLevel(logging.DEBUG)
    app_h.setFormatter(json_fmt)
    root.addHandler(app_h)


logger = logging.getLogger(__name__)


# ── PlaywrightRunner (Feature: Windows ProactorEventLoop fix) ─────────────────

class PlaywrightRunner:
    """Runs Playwright coroutines in a dedicated ProactorEventLoop thread."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = (
            asyncio.ProactorEventLoop() if sys.platform == "win32"
            else asyncio.new_event_loop()
        )
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="playwright-loop"
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_forever(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def run(self, coro) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.get_running_loop().run_in_executor(None, future.result)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


# ── Notification message builders ─────────────────────────────────────────────

def build_watch_message(watch: WatchConfig, heading: str, details: List[str]) -> str:
    parts = [f"SeatRadar: {heading}", watch.display_name()]
    if watch.cinema_hall:
        parts.append(f"Hall: {watch.cinema_hall}")
    if watch.show_time:
        parts.append(f"Time: {watch.show_time}")
    parts.extend(details)
    return ". ".join(p for p in parts if p)


def compute_new_event_keys(
    watch: WatchConfig, snapshot: ScrapeSnapshot, alerted: List[str]
) -> Dict[str, str]:
    already = set(alerted)
    events: Dict[str, str] = {}
    pref_summary = ", ".join(watch.preferred_sections) if watch.preferred_sections else "your preferred section"

    if watch.notify_on_initial_status:
        key = "initial_status"
        if key not in already:
            if not snapshot.booking_open:
                events[key] = build_watch_message(watch, "watch started", [
                    f"Preferred section: {pref_summary}",
                    "Tickets are not open yet",
                    "You will be notified once they open",
                ])
            elif snapshot.booking_open and not snapshot.matched_sections:
                events[key] = build_watch_message(watch, "watch started", [
                    f"Preferred section: {pref_summary}",
                    "Booking is open now",
                    "You will be notified when the preferred section becomes available",
                ])

    if watch.notify_on_booking_open and snapshot.booking_open:
        key = "booking_open"
        if key not in already:
            events[key] = build_watch_message(watch, "tickets are open", [
                f"Preferred section: {pref_summary}",
                "The booking flow is now live",
            ])

    for section in snapshot.matched_sections:
        key = f"section:{section.lower()}"
        if key not in already:
            events[key] = build_watch_message(watch, "preferred section available", [
                f"Section: {section}", "Seats are available now",
            ])

    for seat_id in snapshot.matched_exact_seats:
        key = f"seat:{seat_id.lower()}"
        if key not in already:
            events[key] = build_watch_message(watch, "preferred seat available", [
                f"Seat: {seat_id}",
            ])

    if watch.desired_ticket_count > 1 and snapshot.contiguous_seat_groups:
        group = snapshot.contiguous_seat_groups[0]
        key = f"group:{'-'.join(group).lower()}"
        if key not in already:
            events[key] = build_watch_message(watch, "contiguous seats available", [
                f"Group: {', '.join(group)}",
            ])

    return events


# ── Monitor service ────────────────────────────────────────────────────────────

class WatchMonitorService:
    """
    Continuous monitoring of all active watches.

    Parameters
    ----------
    app_config  : merged config dict from config/settings.py
    db          : open Database instance
    broadcast   : optional async callable(dict) — called after every check
                  (used by the WebSocket ConnectionManager)
    telegram_bot: optional TelegramBot instance for Telegram alerts
    """

    def __init__(
        self,
        app_config: Dict[str, Any],
        db: Database,
        broadcast: Optional[Callable] = None,
        telegram_bot=None,
    ) -> None:
        self.app_config = app_config
        self.db = db
        self._broadcast = broadcast
        self.telegram_bot = telegram_bot
        self.scraper: Optional[BookMyShowScraper] = None
        self.dispatcher = NotificationDispatcher(app_config)
        self._playwright_runner = PlaywrightRunner()
        self._task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._scrape_lock = asyncio.Lock()
        self._last_heartbeat: float = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task:
            return
        self._last_heartbeat = time.monotonic()
        self._task = asyncio.create_task(self._run_loop(), name="seatradar-monitor")
        self._watchdog_task = asyncio.create_task(self._watchdog(), name="seatradar-watchdog")

    async def stop(self) -> None:
        self._stop.set()
        for task in [self._task, self._watchdog_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.scraper:
            await self._playwright_runner.run(self.scraper.close())
            self.scraper = None
        self._playwright_runner.stop()

    # ── scraper management ────────────────────────────────────────────────────

    async def _ensure_scraper(self) -> None:
        if self.scraper is None:
            self.scraper = BookMyShowScraper(
                headless=self.app_config.get("headless", True),
                use_proxy=self.app_config.get("use_proxy", False),
            )
            await self._playwright_runner.run(self.scraper.init())

    async def _restart_scraper(self) -> None:
        if self.scraper:
            try:
                await self._playwright_runner.run(self.scraper.close())
            except Exception:
                pass
        self.scraper = BookMyShowScraper(
            headless=self.app_config.get("headless", True),
            use_proxy=self.app_config.get("use_proxy", False),
        )
        await self._playwright_runner.run(self.scraper.init())

    # ── main monitor loop ─────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._last_heartbeat = time.monotonic()
            try:
                watches = await self.db.list_watches()
                for watch in watches:
                    if not watch.active:
                        continue
                    if self._should_check(watch):
                        await self.check_watch(watch.id)
            except Exception as exc:
                logger.error("Monitor loop error: %s", exc)
            await asyncio.sleep(5)

    # ── watchdog (Feature 6) ──────────────────────────────────────────────────

    async def _watchdog(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(30)
            if self._last_heartbeat == 0:
                continue
            elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > 60:
                logger.warning(
                    "Monitor heartbeat stale (%.0fs) — restarting loop", elapsed
                )
                if self._task and not self._task.done():
                    self._task.cancel()
                    try:
                        await self._task
                    except asyncio.CancelledError:
                        pass
                self._last_heartbeat = time.monotonic()
                self._task = asyncio.create_task(
                    self._run_loop(), name="seatradar-monitor"
                )
                await self.dispatcher.send_message(
                    "SeatRadar: monitor loop was restarted after a heartbeat timeout.",
                    methods=["sms"],
                )

    # ── adaptive polling (Feature 7) ──────────────────────────────────────────

    def _adaptive_interval(self, watch: WatchConfig) -> int:
        if not watch.expected_open_time:
            return watch.check_interval_sec
        try:
            h, m = map(int, watch.expected_open_time.split(":"))
            now = datetime.utcnow()
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            delta_min = abs((target - now).total_seconds() / 60)
            if delta_min <= 5:
                return 2
            if delta_min <= 30:
                return 30
            return 300
        except Exception:
            return watch.check_interval_sec

    def _should_check(self, watch: WatchConfig) -> bool:
        if not watch.last_checked_at:
            return True
        try:
            last = watch.last_checked_at.replace("Z", "")
            delta = datetime.utcnow() - datetime.fromisoformat(last)
            return delta.total_seconds() >= self._adaptive_interval(watch)
        except Exception:
            return True

    # ── single watch check ────────────────────────────────────────────────────

    async def check_watch(self, watch_id: str) -> Optional[ScrapeSnapshot]:
        watch = await self.db.get_watch(watch_id)
        if not watch:
            return None

        async with self._scrape_lock:
            await self._ensure_scraper()
            watch.last_checked_at = utcnow_iso()
            watch.last_error = ""
            t0 = time.monotonic()

            try:
                snapshot = await self._scrape_with_recovery(watch)
                duration_ms = int((time.monotonic() - t0) * 1000)

                watch.last_snapshot = snapshot.to_dict()
                watch.last_status = "booking-open" if snapshot.booking_open else "waiting"
                watch.last_message = self._describe(snapshot)

                alerted = await self.db.list_alerts(watch.id)
                new_events = compute_new_event_keys(watch, snapshot, alerted)
                if new_events:
                    success = await self._dispatch_alerts(watch, new_events)
                    if success:
                        await self.db.record_alerts(watch.id, list(new_events.keys()))
                        if watch.stop_after_first_alert:
                            watch.active = False
                    watch.last_status = "alerted" if success else "notification-failed"

                await self.db.save_watch(watch)
                await self.db.log_check(
                    watch.id,
                    seats_found=len(snapshot.matched_sections) + len(snapshot.matched_exact_seats),
                    duration_ms=duration_ms,
                    snapshot=snapshot.to_dict(),
                )
                # WebSocket broadcast (Feature 9)
                await self._emit({
                    "timestamp": utcnow_iso(),
                    "watch_id": watch.id,
                    "watch_name": watch.display_name(),
                    "status": watch.last_status,
                    "message": watch.last_message,
                    "seats_found": len(snapshot.matched_sections) + len(snapshot.matched_exact_seats),
                    "duration_ms": duration_ms,
                    "error": "",
                })
                return snapshot

            except CaptchaDetected as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                watch.last_status = "captcha-paused"
                watch.last_error = str(exc)
                watch.last_message = "CAPTCHA detected — paused for 10 minutes"
                watch.active = False
                await self.db.save_watch(watch)
                await self.db.log_check(watch.id, duration_ms=duration_ms, error=str(exc))
                logger.warning("CAPTCHA on watch %s — auto-pausing", watch.display_name())
                await self.dispatcher.send_message(
                    f"SeatRadar: CAPTCHA detected on {watch.display_name()}. "
                    "Watch paused. Auto-resuming in 10 minutes.",
                    methods=watch.notify_methods,
                )
                await self._emit({
                    "timestamp": utcnow_iso(), "watch_id": watch.id,
                    "watch_name": watch.display_name(),
                    "status": "captcha-paused", "message": str(exc),
                    "seats_found": 0, "duration_ms": duration_ms, "error": str(exc),
                })
                asyncio.create_task(self._auto_resume(watch.id, 600))
                return None

            except Exception as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                watch.last_status = "error"
                watch.last_error = str(exc)
                watch.last_message = str(exc)
                await self.db.save_watch(watch)
                await self.db.log_check(watch.id, duration_ms=duration_ms, error=str(exc))
                logger.error("Watch %s failed: %s", watch.display_name(), exc)
                await self._emit({
                    "timestamp": utcnow_iso(), "watch_id": watch.id,
                    "watch_name": watch.display_name(),
                    "status": "error", "message": str(exc),
                    "seats_found": 0, "duration_ms": duration_ms, "error": str(exc),
                })
                return None

    # ── CAPTCHA auto-resume (Feature 4) ───────────────────────────────────────

    async def _auto_resume(self, watch_id: str, delay: int) -> None:
        await asyncio.sleep(delay)
        watch = await self.db.get_watch(watch_id)
        if watch and not watch.active and "captcha" in watch.last_status:
            watch.active = True
            watch.last_status = "idle"
            await self.db.save_watch(watch)
            logger.info("Auto-resumed watch %s after CAPTCHA pause", watch.display_name())

    # ── scrape with recovery ──────────────────────────────────────────────────

    async def _scrape_with_recovery(self, watch: WatchConfig) -> ScrapeSnapshot:
        assert self.scraper is not None
        try:
            snapshot = await self._playwright_runner.run(
                self.scraper.scrape_with_backoff(watch)
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "has been closed" in msg or "target page, context or browser" in msg:
                logger.warning("Browser closed during scrape — restarting Playwright")
                await self._restart_scraper()
                snapshot = await self._playwright_runner.run(
                    self.scraper.scrape_with_backoff(watch)
                )
            else:
                raise

        # Double-check: if booking looked closed, retry once after 2 s
        if not snapshot.booking_open:
            logger.info(
                "Booking looks closed for %s — retrying once", watch.display_name()
            )
            await asyncio.sleep(2)
            retry = await self._playwright_runner.run(self.scraper.scrape_watch(watch))
            if retry.booking_open or retry.matched_sections or retry.matched_exact_seats:
                snapshot = retry

        return snapshot

    # ── alert dispatching ─────────────────────────────────────────────────────

    async def _dispatch_alerts(
        self, watch: WatchConfig, events: Dict[str, str]
    ) -> bool:
        messages = list(events.values())

        user = await self.db.get_user(watch.user_id)
        to_number = (user.to_number if user else "") or ""

        if to_number:
            success = await self.dispatcher.send_message_batch_to(
                messages, to_number, watch.notify_methods
            )
        else:
            success = await self.dispatcher.send_message_batch(messages, watch.notify_methods)

        # Telegram alert (Feature 10)
        chat_id = watch.telegram_chat_id or (user.telegram_chat_id if user else "")
        if chat_id and self.telegram_bot:
            for msg in messages:
                await self.telegram_bot.send_alert(chat_id, msg)
            success = True

        return success

    # ── WebSocket emit (Feature 9) ────────────────────────────────────────────

    async def _emit(self, data: Dict[str, Any]) -> None:
        if self._broadcast:
            try:
                await self._broadcast(data)
            except Exception as exc:
                logger.debug("WebSocket broadcast error: %s", exc)

    # ── snapshot description ──────────────────────────────────────────────────

    def _describe(self, s: ScrapeSnapshot) -> str:
        if s.matched_exact_seats:
            return f"Matched seats: {', '.join(s.matched_exact_seats)}"
        if s.matched_sections:
            return f"Matched sections: {', '.join(s.matched_sections)}"
        if s.booking_open:
            if s.available_sections:
                return f"Booking open. Available: {', '.join(s.available_sections)}"
            return "Booking is open"
        if s.sold_out:
            return "Layout appears sold out"
        return "Waiting for booking or preferred seats"
