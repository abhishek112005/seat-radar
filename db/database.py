"""
SQLite persistence layer using aiosqlite.
Replaces the JSON-file WatchStore with a proper database.
Tables: users, sessions, pending_otps, watches, check_logs, alerts
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from models import UserAccount, WatchConfig, utcnow_iso

logger = logging.getLogger(__name__)

DB_PATH = Path("data/seatradar.db")


class Database:
    """Async SQLite wrapper.  One shared connection, WAL mode, serialised writes."""

    def __init__(self, path: str = str(DB_PATH)) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._migrate_users()
        await self._db.commit()
        logger.info("Database connected: %s", self.path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id               TEXT PRIMARY KEY,
                phone_number     TEXT UNIQUE NOT NULL,
                is_verified      INTEGER DEFAULT 0,
                created_at       TEXT NOT NULL,
                telegram_chat_id TEXT DEFAULT '',
                email            TEXT DEFAULT '',
                google_sub       TEXT DEFAULT '',
                avatar_url       TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_otps (
                user_id    TEXT PRIMARY KEY,
                code       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watches (
                id                      TEXT PRIMARY KEY,
                user_id                 TEXT NOT NULL,
                name                    TEXT DEFAULT '',
                movie_name              TEXT DEFAULT '',
                cinema_hall             TEXT DEFAULT '',
                show_time               TEXT DEFAULT '',
                show_url                TEXT NOT NULL,
                preferred_sections      TEXT DEFAULT '[]',
                exact_seats             TEXT DEFAULT '[]',
                desired_ticket_count    INTEGER DEFAULT 1,
                check_interval_sec      INTEGER DEFAULT 60,
                notify_methods          TEXT DEFAULT '["sms","call"]',
                notify_on_initial_status INTEGER DEFAULT 1,
                notify_on_booking_open   INTEGER DEFAULT 1,
                stop_after_first_alert   INTEGER DEFAULT 0,
                active                   INTEGER DEFAULT 1,
                last_checked_at          TEXT DEFAULT '',
                last_status              TEXT DEFAULT 'idle',
                last_error               TEXT DEFAULT '',
                last_message             TEXT DEFAULT '',
                last_snapshot            TEXT DEFAULT '{}',
                expected_open_time       TEXT DEFAULT '',
                telegram_chat_id         TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS check_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id      TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                seats_found   INTEGER DEFAULT 0,
                duration_ms   INTEGER DEFAULT 0,
                error         TEXT DEFAULT '',
                snapshot_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id   TEXT NOT NULL,
                event_key  TEXT NOT NULL,
                alerted_at TEXT NOT NULL,
                UNIQUE(watch_id, event_key)
            );
        """)

    async def _migrate_users(self) -> None:
        for col, defn in [
            ("email", "TEXT DEFAULT ''"),
            ("google_sub", "TEXT DEFAULT ''"),
            ("avatar_url", "TEXT DEFAULT ''"),
            ("to_number", "TEXT DEFAULT ''"),
        ]:
            try:
                await self._db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except Exception:
                pass

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _one(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params: tuple = ()) -> List[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cur:
            return await cur.fetchall()

    # ── users ─────────────────────────────────────────────────────────────────

    async def get_or_create_user(self, phone_number: str) -> UserAccount:
        row = await self._one("SELECT * FROM users WHERE phone_number=?", (phone_number,))
        if row:
            return self._user(row)
        user = UserAccount(phone_number=phone_number)
        async with self._write_lock:
            await self._db.execute(
                "INSERT OR IGNORE INTO users (id,phone_number,is_verified,created_at) VALUES (?,?,?,?)",
                (user.id, user.phone_number, 0, user.created_at),
            )
            await self._db.commit()
        return user

    async def get_user(self, user_id: str) -> Optional[UserAccount]:
        row = await self._one("SELECT * FROM users WHERE id=?", (user_id,))
        return self._user(row) if row else None

    async def get_user_by_session(self, token: str) -> Optional[UserAccount]:
        row = await self._one(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=?",
            (token,),
        )
        return self._user(row) if row else None

    async def get_user_by_telegram(self, chat_id: str) -> Optional[UserAccount]:
        row = await self._one("SELECT * FROM users WHERE telegram_chat_id=?", (chat_id,))
        return self._user(row) if row else None

    async def mark_user_verified(self, user_id: str) -> None:
        async with self._write_lock:
            await self._db.execute("UPDATE users SET is_verified=1 WHERE id=?", (user_id,))
            await self._db.commit()

    async def get_or_create_user_by_google(
        self, google_sub: str, email: str, avatar_url: str = ""
    ) -> UserAccount:
        row = await self._one("SELECT * FROM users WHERE google_sub=?", (google_sub,))
        if row:
            user = self._user(row)
            if avatar_url and user.avatar_url != avatar_url:
                async with self._write_lock:
                    await self._db.execute(
                        "UPDATE users SET avatar_url=? WHERE id=?", (avatar_url, user.id)
                    )
                    await self._db.commit()
                user.avatar_url = avatar_url
            return user
        row = await self._one("SELECT * FROM users WHERE email=?", (email,))
        if row:
            user = self._user(row)
            async with self._write_lock:
                await self._db.execute(
                    "UPDATE users SET google_sub=?, avatar_url=?, is_verified=1 WHERE id=?",
                    (google_sub, avatar_url, user.id),
                )
                await self._db.commit()
            user.google_sub = google_sub
            user.avatar_url = avatar_url
            user.is_verified = True
            return user
        user = UserAccount(
            email=email, google_sub=google_sub, avatar_url=avatar_url, is_verified=True
        )
        async with self._write_lock:
            await self._db.execute(
                "INSERT OR IGNORE INTO users "
                "(id,phone_number,email,google_sub,avatar_url,is_verified,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (user.id, "", email, google_sub, avatar_url, 1, user.created_at),
            )
            await self._db.commit()
        return user

    async def update_user_to_number(self, user_id: str, to_number: str) -> None:
        async with self._write_lock:
            await self._db.execute(
                "UPDATE users SET to_number=? WHERE id=?", (to_number.strip(), user_id)
            )
            await self._db.commit()

    async def set_user_telegram(self, user_id: str, chat_id: str) -> None:
        async with self._write_lock:
            await self._db.execute(
                "UPDATE users SET telegram_chat_id=? WHERE id=?", (chat_id, user_id)
            )
            await self._db.commit()

    # ── sessions ──────────────────────────────────────────────────────────────

    async def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(24)
        async with self._write_lock:
            await self._db.execute(
                "INSERT INTO sessions (token,user_id,created_at) VALUES (?,?,?)",
                (token, user_id, utcnow_iso()),
            )
            await self._db.commit()
        return token

    async def delete_session(self, token: str) -> None:
        async with self._write_lock:
            await self._db.execute("DELETE FROM sessions WHERE token=?", (token,))
            await self._db.commit()

    # ── OTPs ──────────────────────────────────────────────────────────────────

    async def save_pending_otp(self, user_id: str, code: str) -> None:
        async with self._write_lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO pending_otps (user_id,code,created_at) VALUES (?,?,?)",
                (user_id, code, utcnow_iso()),
            )
            await self._db.commit()

    async def get_pending_otp(self, user_id: str) -> str:
        row = await self._one("SELECT code FROM pending_otps WHERE user_id=?", (user_id,))
        return row["code"] if row else ""

    async def verify_pending_otp(self, user_id: str, code: str) -> bool:
        row = await self._one("SELECT code FROM pending_otps WHERE user_id=?", (user_id,))
        if row and row["code"] == code:
            async with self._write_lock:
                await self._db.execute("DELETE FROM pending_otps WHERE user_id=?", (user_id,))
                await self._db.commit()
            return True
        return False

    # ── watches ───────────────────────────────────────────────────────────────

    async def list_watches(self, user_id: Optional[str] = None) -> List[WatchConfig]:
        if user_id:
            rows = await self._all("SELECT * FROM watches WHERE user_id=?", (user_id,))
        else:
            rows = await self._all("SELECT * FROM watches")
        return [self._watch(r) for r in rows]

    async def get_watch(self, watch_id: str, user_id: Optional[str] = None) -> Optional[WatchConfig]:
        if user_id:
            row = await self._one(
                "SELECT * FROM watches WHERE id=? AND user_id=?", (watch_id, user_id)
            )
        else:
            row = await self._one("SELECT * FROM watches WHERE id=?", (watch_id,))
        return self._watch(row) if row else None

    async def save_watch(self, watch: WatchConfig) -> WatchConfig:
        vals = self._watch_vals(watch)
        async with self._write_lock:
            existing = await self._one("SELECT id FROM watches WHERE id=?", (watch.id,))
            if existing:
                await self._db.execute(
                    """UPDATE watches SET
                        user_id=?,name=?,movie_name=?,cinema_hall=?,show_time=?,show_url=?,
                        preferred_sections=?,exact_seats=?,desired_ticket_count=?,
                        check_interval_sec=?,notify_methods=?,notify_on_initial_status=?,
                        notify_on_booking_open=?,stop_after_first_alert=?,active=?,
                        last_checked_at=?,last_status=?,last_error=?,last_message=?,
                        last_snapshot=?,expected_open_time=?,telegram_chat_id=?
                    WHERE id=?""",
                    vals + (watch.id,),
                )
            else:
                await self._db.execute(
                    """INSERT INTO watches (
                        id,user_id,name,movie_name,cinema_hall,show_time,show_url,
                        preferred_sections,exact_seats,desired_ticket_count,
                        check_interval_sec,notify_methods,notify_on_initial_status,
                        notify_on_booking_open,stop_after_first_alert,active,
                        last_checked_at,last_status,last_error,last_message,
                        last_snapshot,expected_open_time,telegram_chat_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (watch.id,) + vals,
                )
            await self._db.commit()
        return watch

    async def delete_watch(self, watch_id: str, user_id: Optional[str] = None) -> None:
        async with self._write_lock:
            if user_id:
                await self._db.execute(
                    "DELETE FROM watches WHERE id=? AND user_id=?", (watch_id, user_id)
                )
            else:
                await self._db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            await self._db.execute("DELETE FROM alerts WHERE watch_id=?", (watch_id,))
            await self._db.execute("DELETE FROM check_logs WHERE watch_id=?", (watch_id,))
            await self._db.commit()

    # ── alerts ────────────────────────────────────────────────────────────────

    async def list_alerts(self, watch_id: str) -> List[str]:
        rows = await self._all("SELECT event_key FROM alerts WHERE watch_id=?", (watch_id,))
        return [r["event_key"] for r in rows]

    async def record_alerts(self, watch_id: str, event_keys: List[str]) -> None:
        now = utcnow_iso()
        async with self._write_lock:
            for key in event_keys:
                await self._db.execute(
                    "INSERT OR IGNORE INTO alerts (watch_id,event_key,alerted_at) VALUES (?,?,?)",
                    (watch_id, key, now),
                )
            await self._db.commit()

    # ── check logs ────────────────────────────────────────────────────────────

    async def log_check(
        self,
        watch_id: str,
        seats_found: int = 0,
        duration_ms: int = 0,
        error: str = "",
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._write_lock:
            await self._db.execute(
                """INSERT INTO check_logs
                   (watch_id,timestamp,seats_found,duration_ms,error,snapshot_json)
                   VALUES (?,?,?,?,?,?)""",
                (watch_id, utcnow_iso(), seats_found, duration_ms, error,
                 json.dumps(snapshot or {})),
            )
            await self._db.commit()

    async def get_recent_logs(self, watch_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = await self._all(
            "SELECT * FROM check_logs WHERE watch_id=? ORDER BY timestamp DESC LIMIT ?",
            (watch_id, limit),
        )
        return [dict(r) for r in rows]

    # ── row converters ────────────────────────────────────────────────────────

    def _user(self, row: aiosqlite.Row) -> UserAccount:
        d = dict(row)
        return UserAccount(
            id=d["id"],
            phone_number=d.get("phone_number") or "",
            email=d.get("email") or "",
            google_sub=d.get("google_sub") or "",
            avatar_url=d.get("avatar_url") or "",
            to_number=d.get("to_number") or "",
            is_verified=bool(d["is_verified"]),
            created_at=d["created_at"],
            telegram_chat_id=d.get("telegram_chat_id") or "",
        )

    def _watch(self, row: aiosqlite.Row) -> WatchConfig:
        return WatchConfig(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"] or "",
            movie_name=row["movie_name"] or "",
            cinema_hall=row["cinema_hall"] or "",
            show_time=row["show_time"] or "",
            show_url=row["show_url"],
            preferred_sections=json.loads(row["preferred_sections"] or "[]"),
            exact_seats=json.loads(row["exact_seats"] or "[]"),
            desired_ticket_count=row["desired_ticket_count"],
            check_interval_sec=row["check_interval_sec"],
            notify_methods=json.loads(row["notify_methods"] or '["sms","call"]'),
            notify_on_initial_status=bool(row["notify_on_initial_status"]),
            notify_on_booking_open=bool(row["notify_on_booking_open"]),
            stop_after_first_alert=bool(row["stop_after_first_alert"]),
            active=bool(row["active"]),
            last_checked_at=row["last_checked_at"] or "",
            last_status=row["last_status"] or "idle",
            last_error=row["last_error"] or "",
            last_message=row["last_message"] or "",
            last_snapshot=json.loads(row["last_snapshot"] or "{}"),
            expected_open_time=row["expected_open_time"] or "",
            telegram_chat_id=row["telegram_chat_id"] or "",
        )

    def _watch_vals(self, w: WatchConfig) -> tuple:
        return (
            w.user_id, w.name, w.movie_name, w.cinema_hall, w.show_time, w.show_url,
            json.dumps(w.preferred_sections), json.dumps(w.exact_seats),
            w.desired_ticket_count, w.check_interval_sec, json.dumps(w.notify_methods),
            int(w.notify_on_initial_status), int(w.notify_on_booking_open),
            int(w.stop_after_first_alert), int(w.active),
            w.last_checked_at, w.last_status, w.last_error, w.last_message,
            json.dumps(w.last_snapshot), w.expected_open_time, w.telegram_chat_id,
        )
