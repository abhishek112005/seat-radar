"""
BookMyShow scraper — Playwright + layout heuristics.

New in v2:
  • Feature 3  — exponential back-off on scrape failure (2s→4s→8s→16s)
  • Feature 4  — CAPTCHA detection → raises CaptchaDetected
  • Feature 5  — cookie persistence (data/session_cookies.json)
  • Feature 8  — proxy rotation via free-proxy pool
  • Feature 2  — self-healing selector fallback via scraper/self_healing.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright_stealth import Stealth

from models import ScrapeSnapshot, WatchConfig, normalize_seat_id
from scraper.self_healing import SelfHealingSelector

_stealth = Stealth(navigator_languages_override=("en-IN", "en"), navigator_platform_override="Win32")

logger = logging.getLogger("scraper.bookmyshow")

COOKIES_FILE = Path("data/session_cookies.json")

CAPTCHA_SIGNALS = [
    "captcha",
    "verify you are human",
    "i am not a robot",
    "unusual traffic",
    "suspicious activity",
    "access denied",
    "403 forbidden",
    "cloudflare",
    "just a moment",
]


class CaptchaDetected(RuntimeError):
    """Raised when a CAPTCHA or bot-detection page is encountered."""


# ── Proxy pool ────────────────────────────────────────────────────────────────

class _ProxyPool:
    """Thin wrapper around free-proxy that keeps 10 live proxies."""

    def __init__(self) -> None:
        self._pool: List[str] = []
        self._failed: Set[str] = set()

    async def get(self) -> Optional[str]:
        available = [p for p in self._pool if p not in self._failed]
        if not available:
            await self._refresh()
            available = [p for p in self._pool if p not in self._failed]
        return available[0] if available else None

    async def mark_failed(self, proxy: str) -> None:
        self._failed.add(proxy)

    async def _refresh(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            proxies = await loop.run_in_executor(None, self._fetch_sync)
            self._pool = proxies
            self._failed.clear()
            logger.info("Proxy pool refreshed: %d proxies", len(self._pool))
        except Exception as exc:
            logger.warning("Proxy pool refresh failed: %s", exc)

    @staticmethod
    def _fetch_sync() -> List[str]:
        try:
            from fp.fp import FreeProxy
        except ImportError:
            return []
        proxies: List[str] = []
        for _ in range(12):
            try:
                p = FreeProxy(rand=True, timeout=1, https=True).get()
                if p and p not in proxies:
                    proxies.append(p)
                if len(proxies) >= 10:
                    break
            except Exception:
                pass
        return proxies


_proxy_pool = _ProxyPool()


# ── Colour helpers ────────────────────────────────────────────────────────────

def _rgb(color: str) -> tuple:
    m = re.search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", color or "")
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def _green(c: str) -> bool:
    r, g, b = _rgb(c)
    return g > r + 20 and g > b + 20


def _grey(c: str) -> bool:
    r, g, b = _rgb(c)
    return abs(r - g) < 18 and abs(g - b) < 18 and max(r, g, b) > 120


# ── Scraper ───────────────────────────────────────────────────────────────────

class BookMyShowScraper:
    """Scrapes BookMyShow seat-layout pages and detects availability."""

    def __init__(self, headless: bool = True, use_proxy: bool = False) -> None:
        self.headless = headless
        self.use_proxy = use_proxy
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._healer = SelfHealingSelector()
        self._current_proxy: Optional[str] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def init(self) -> None:
        proxy_cfg = None
        if self.use_proxy:
            p = await _proxy_pool.get()
            if p:
                self._current_proxy = p
                proxy_cfg = {"server": p}
                logger.info("Using proxy: %s", p)

        self.playwright = await async_playwright().start()
        launch_kwargs: dict = {"headless": self.headless, "proxy": proxy_cfg}
        try:
            self.browser = await self.playwright.chromium.launch(channel="chrome", **launch_kwargs)
            logger.info("Launched real Chrome")
        except Exception:
            self.browser = await self.playwright.chromium.launch(**launch_kwargs)
            logger.info("Launched bundled Chromium (Chrome not found)")
        self.context = await self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"},
        )
        _stealth.hook_playwright_context(self.playwright)
        await self.context.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,otf}",
            lambda route: route.abort(),
        )
        await self._load_cookies()
        logger.info("Playwright browser initialised (proxy=%s)", self._current_proxy or "none")

    async def close(self) -> None:
        await self._save_cookies()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Playwright browser closed")

    # ── cookie persistence (Feature 5) ────────────────────────────────────────

    async def _save_cookies(self) -> None:
        if not self.context:
            return
        try:
            cookies = await self.context.cookies()
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("Cookie save failed: %s", exc)

    async def _load_cookies(self) -> None:
        if not self.context or not COOKIES_FILE.exists():
            return
        try:
            cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
            if cookies:
                await self.context.add_cookies(cookies)
                logger.debug("Loaded %d session cookies", len(cookies))
        except Exception as exc:
            logger.debug("Cookie load failed: %s", exc)

    # ── CAPTCHA detection (Feature 4) ─────────────────────────────────────────

    async def _check_captcha(self, page: Page) -> None:
        text = (await self.get_page_text(page)).lower()
        if any(sig in text for sig in CAPTCHA_SIGNALS):
            raise CaptchaDetected("CAPTCHA/bot-detection page detected")

    # ── exponential back-off scrape (Feature 3) ───────────────────────────────

    async def scrape_with_backoff(
        self, watch: WatchConfig, max_retries: int = 4
    ) -> ScrapeSnapshot:
        """Scrape with 2→4→8→16 s back-off.  Raises after *max_retries* failures."""
        delay = 2
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await self.scrape_watch(watch)
            except CaptchaDetected:
                raise  # propagate immediately — no point retrying
            except Exception as exc:
                last_exc = exc
                if attempt == max_retries:
                    break
                logger.warning(
                    "Scrape attempt %d/%d failed (%s). Retrying in %ds",
                    attempt + 1, max_retries + 1, exc, delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
                # If proxy failed, rotate to a fresh one
                if self.use_proxy and self._current_proxy:
                    await _proxy_pool.mark_failed(self._current_proxy)
                    self._current_proxy = await _proxy_pool.get()
        raise RuntimeError(
            f"All {max_retries + 1} scrape attempts failed. Last: {last_exc}"
        )

    # ── navigation ────────────────────────────────────────────────────────────

    async def get_page_text(self, page: Page) -> str:
        return (await page.locator("body").text_content()) or ""

    async def navigate_to_booking(self, url: str) -> Optional[Page]:
        page = await self.context.new_page()
        page.set_default_timeout(20000)
        try:
            logger.info("Navigating to %s", url)
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)
            await self._check_captcha(page)

            for sel in [
                'button:has-text("Book Now")',
                'button:has-text("book now")',
                'a:has-text("Book Now")',
                'button:has-text("Select Seats")',
                "button.booking-btn",
                "a.booking-btn",
            ]:
                try:
                    loc = page.locator(sel)
                    if await loc.count():
                        await loc.first.click()
                        await asyncio.sleep(2)
                        break
                except Exception:
                    pass
            return page
        except CaptchaDetected:
            await page.close()
            raise
        except Exception as exc:
            logger.error("Navigation failed for %s: %s", url, exc)
            await page.close()
            return None

    # ── element extraction ────────────────────────────────────────────────────

    def extract_labels_from_text(self, text: str) -> Set[str]:
        labels: Set[str] = set()
        if not text:
            return labels
        norm = re.sub(r"\s+", " ", text)
        for pat in [
            r"(?:₹|Rs\.?)\s*\d+\s+([A-Z][A-Z .&-]{2,40})",
            r"\b(GOLD)\b", r"\b(PLATINUM)\b", r"\b(SILVER)\b",
            r"\b(DIRECTOR CHOICE)\b", r"\b(PREMIUM)\b", r"\b(BALCONY)\b",
        ]:
            for m in re.findall(pat, norm, re.IGNORECASE):
                cleaned = re.sub(r"\s+", " ", m).strip(" .:-")
                if cleaned:
                    labels.add(cleaned.upper())
        return labels

    async def extract_layout_elements(self, page: Page) -> List[Dict]:
        script = """() => {
          const els = Array.from(document.querySelectorAll('body *'));
          return els.map(el => {
            const text = (el.innerText || el.textContent || '').trim();
            if (!text || text.length > 80) return null;
            const rect = el.getBoundingClientRect();
            const st   = window.getComputedStyle(el);
            if (rect.width < 4 || rect.height < 4) return null;
            if (st.visibility === 'hidden' || st.display === 'none') return null;
            return {
              text, x: rect.x, y: rect.y, width: rect.width, height: rect.height,
              color: st.color, backgroundColor: st.backgroundColor,
              borderColor: st.borderColor,
              className: typeof el.className === 'string' ? el.className : '',
              ariaLabel: el.getAttribute('aria-label') || '',
              role: el.getAttribute('role') || '', tagName: el.tagName
            };
          }).filter(Boolean);
        }"""
        return await page.evaluate(script)

    def extract_sections(self, elements: List[Dict], page_text: str) -> List[str]:
        labels = self.extract_labels_from_text(page_text)
        for el in elements:
            t = str(el.get("text", "")).strip()
            if re.fullmatch(r"(?:₹|Rs\.?)?\s*\d+\s+[A-Za-z][A-Za-z .&-]{2,30}", t):
                cleaned = re.sub(r"^(?:₹|Rs\.?)?\s*\d+\s+", "", t).strip(" .:-")
                labels.add(cleaned.upper())
            elif t.upper() in {"GOLD","PLATINUM","SILVER","DIRECTOR CHOICE","PREMIUM","BALCONY"}:
                labels.add(t.upper())
        return sorted(labels)

    def extract_section_positions(self, elements: List[Dict], page_text: str) -> List[Dict]:
        known = set(self.extract_labels_from_text(page_text))
        rows: List[Dict] = []
        for el in elements:
            t = str(el.get("text", "")).strip()
            cleaned = ""
            if re.fullmatch(r"(?:₹|Rs\.?)?\s*\d+\s+[A-Za-z][A-Za-z .&-]{2,30}", t):
                cleaned = re.sub(r"^(?:₹|Rs\.?)?\s*\d+\s+", "", t).strip(" .:-").upper()
            elif t.upper() in {"GOLD","PLATINUM","SILVER","DIRECTOR CHOICE","PREMIUM","BALCONY"}:
                cleaned = t.upper()
            if cleaned:
                known.add(cleaned)
                rows.append({"name": cleaned, "y": float(el.get("y", 0))})
        dedup: Dict[str, float] = {}
        for item in sorted(rows, key=lambda r: r["y"]):
            if item["name"] not in dedup:
                dedup[item["name"]] = item["y"]
        return [{"name": n, "y": y} for n, y in sorted(dedup.items(), key=lambda p: p[1])]

    def extract_available_seats(self, elements: List[Dict]) -> List[Dict]:
        row_labels, seat_cells = [], []
        for el in elements:
            t = str(el.get("text", "")).strip()
            if re.fullmatch(r"[A-Z]", t):
                if float(el.get("x", 9999)) < 220 and 10 <= float(el.get("height", 0)) <= 40:
                    row_labels.append(el)
            elif re.fullmatch(r"\d{1,2}", t):
                w, h = float(el.get("width", 0)), float(el.get("height", 0))
                if 12 <= w <= 50 and 12 <= h <= 50:
                    seat_cells.append(el)

        row_labels.sort(key=lambda e: float(e["y"]))
        available: List[Dict] = []
        for seat in seat_cells:
            cls = str(seat.get("className", "")).lower()
            aria = str(seat.get("ariaLabel", "")).lower()
            color = str(seat.get("color", ""))
            border = str(seat.get("borderColor", ""))
            bg = str(seat.get("backgroundColor", ""))
            is_sold = any(tok in cls or tok in aria for tok in ["sold", "disabled", "booked"])
            if not is_sold and _grey(bg) and not _green(border) and not _green(color):
                is_sold = True
            is_avail = any(tok in cls or tok in aria for tok in ["avail", "seat", "select"])
            is_avail = is_avail or _green(border) or _green(color)
            if is_sold or not is_avail:
                continue
            row = self._nearest_row(seat, row_labels)
            if not row:
                continue
            available.append({
                "seat_id": normalize_seat_id(f"{row}{seat['text']}"),
                "x": float(seat.get("x", 0)),
                "y": float(seat.get("y", 0)),
            })
        dedup = {item["seat_id"]: item for item in available}
        return [dedup[k] for k in sorted(dedup, key=self._seat_key)]

    def map_available_sections(
        self, section_positions: List[Dict], available_seats: List[Dict]
    ) -> List[str]:
        if not section_positions or not available_seats:
            return []
        ordered = sorted(section_positions, key=lambda i: float(i["y"]))
        found: Set[str] = set()
        for seat in available_seats:
            sy = float(seat["y"])
            for idx, sec in enumerate(ordered):
                end_y = float(ordered[idx + 1]["y"]) if idx + 1 < len(ordered) else float(sec["y"]) + 260
                if float(sec["y"]) <= sy < end_y:
                    found.add(str(sec["name"]))
                    break
        return sorted(found)

    # ── self-healing fallback (Feature 2) ─────────────────────────────────────

    async def _try_self_heal(self, page: Page) -> List[Dict]:
        """If standard extraction finds nothing, attempt semantic element recovery."""
        logger.info("Standard seat extraction empty — attempting self-healing")
        try:
            html = await page.content()
            results = self._healer.find_seats(html)
            if results:
                logger.info("Self-healing found %d candidate elements", len(results))
            return results
        except Exception as exc:
            logger.warning("Self-healing failed: %s", exc)
            return []

    # ── scrape entry point ────────────────────────────────────────────────────

    async def scrape_watch(self, watch: WatchConfig) -> ScrapeSnapshot:
        page = await self.navigate_to_booking(watch.show_url)
        if page is None:
            raise ValueError("Could not load the target page")
        try:
            page_text = await self.get_page_text(page)
            elements = await self.extract_layout_elements(page)
            detected = self.extract_sections(elements, page_text)
            sec_pos = self.extract_section_positions(elements, page_text)
            seat_entries = self.extract_available_seats(elements)

            # Self-heal if nothing found and page looks non-empty
            if not seat_entries and not detected and len(page_text) > 500:
                await self._try_self_heal(page)

            avail_seats = [e["seat_id"] for e in seat_entries]
            avail_sections = self.map_available_sections(sec_pos, seat_entries)
            booking_open = await self._booking_open(page, detected, avail_seats)
            sold_out = await self._sold_out(page, avail_seats)

            pref_lower = [s.lower() for s in watch.preferred_sections]
            matched_sections = sorted({
                s for s in avail_sections
                if any(p in s.lower() for p in pref_lower)
            })
            pref_seats = {normalize_seat_id(s) for s in watch.exact_seats}
            matched_seats = sorted(
                {s for s in avail_seats if s in pref_seats}, key=self._seat_key
            )
            groups = self.contiguous_groups(avail_seats, watch.desired_ticket_count)

            notes = []
            if booking_open:
                notes.append("Booking flow appears open")
            if sold_out:
                notes.append("Layout appears sold out")
            if groups:
                notes.append(f"Contiguous groups found for {watch.desired_ticket_count} seats")

            return ScrapeSnapshot(
                booking_open=booking_open,
                sold_out=sold_out,
                page_title=(await page.title()).strip(),
                available_sections=avail_sections,
                detected_sections=detected,
                available_exact_seats=avail_seats,
                matched_sections=matched_sections,
                matched_exact_seats=matched_seats,
                contiguous_seat_groups=groups,
                notes=notes,
                raw_labels=sorted(self.extract_labels_from_text(page_text)),
            )
        finally:
            await page.close()

    # ── backwards-compat CLI helper ───────────────────────────────────────────

    async def scrape_and_check(
        self, url: str, preferred_seats: List[str]
    ) -> tuple:
        watch = WatchConfig.from_dict({
            "name": "CLI Check", "show_url": url,
            "preferred_sections": preferred_seats,
            "notify_methods": ["sms", "call"],
        })
        snap = await self.scrape_watch(watch)
        matches = set(snap.matched_sections) | set(snap.matched_exact_seats)
        return bool(matches), matches

    # ── utilities ─────────────────────────────────────────────────────────────

    async def _booking_open(
        self, page: Page, sections: List[str], exact_seats: List[str]
    ) -> bool:
        if exact_seats or sections:
            return True
        text = (await self.get_page_text(page)).lower()
        indicators = [
            "select seats", "tickets", "how many seats", "available",
            "gold", "platinum", "director choice", "seat layout", "bestseller",
        ]
        return any(i in text for i in indicators)

    async def _sold_out(self, page: Page, avail: List[str]) -> bool:
        if avail:
            return False
        text = (await self.get_page_text(page)).lower()
        return any(s in text for s in [
            "all shows sold out", "no seats available", "show is sold out",
            "currently unavailable", "housefull",
        ])

    def _nearest_row(self, seat: Dict, rows: List[Dict]) -> str:
        sy = float(seat.get("y", 0))
        for r in rows:
            if abs(float(r.get("y", 0)) - sy) <= 18:
                return str(r.get("text", "")).strip().upper()
        nearest = min(rows, key=lambda r: abs(float(r.get("y", 0)) - sy), default=None)
        if nearest and abs(float(nearest.get("y", 0)) - sy) <= 26:
            return str(nearest.get("text", "")).strip().upper()
        return ""

    def _seat_key(self, sid: str) -> tuple:
        return (sid[:1], int(sid[1:] or 0))

    def contiguous_groups(self, seat_ids: List[str], n: int) -> List[List[str]]:
        if n <= 1 or not seat_ids:
            return []
        rows: Dict[str, List[int]] = {}
        for sid in seat_ids:
            rows.setdefault(sid[0], []).append(int(sid[1:]))
        groups: List[List[str]] = []
        for row, nums in rows.items():
            nums = sorted(set(nums))
            cur = [nums[0]]
            for num in nums[1:]:
                if num == cur[-1] + 1:
                    cur.append(num)
                else:
                    if len(cur) >= n:
                        groups.append([f"{row}{s}" for s in cur])
                    cur = [num]
            if len(cur) >= n:
                groups.append([f"{row}{s}" for s in cur])
        return groups
