"""
Core data models for SeatRadar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields as dc_fields
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4


def utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def split_csv(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_seat_id(value: str) -> str:
    compact = "".join(ch for ch in value.upper() if ch.isalnum())
    if len(compact) < 2:
        return compact
    row = compact[0]
    number = compact[1:].lstrip("0") or "0"
    return f"{row}{number}"


@dataclass
class UserAccount:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    phone_number: str = ""
    email: str = ""
    google_sub: str = ""
    avatar_url: str = ""
    is_verified: bool = False
    created_at: str = field(default_factory=utcnow_iso)
    telegram_chat_id: str = ""

    def display_name(self) -> str:
        return self.email or self.phone_number or self.id

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserAccount":
        known = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WatchConfig:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    user_id: str = ""
    name: str = ""
    movie_name: str = ""
    cinema_hall: str = ""
    show_time: str = ""
    show_url: str = ""
    preferred_sections: List[str] = field(default_factory=list)
    exact_seats: List[str] = field(default_factory=list)
    desired_ticket_count: int = 1
    check_interval_sec: int = 60
    notify_methods: List[str] = field(default_factory=lambda: ["sms", "call"])
    notify_on_initial_status: bool = True
    notify_on_booking_open: bool = True
    stop_after_first_alert: bool = False
    active: bool = True
    last_checked_at: str = ""
    last_status: str = "idle"
    last_error: str = ""
    last_message: str = ""
    last_snapshot: Dict[str, Any] = field(default_factory=dict)
    # Feature 7 — adaptive polling: expected booking-open time as "HH:MM" (UTC)
    expected_open_time: str = ""
    # Feature 10 — per-watch Telegram chat ID (overrides global)
    telegram_chat_id: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchConfig":
        payload = dict(data)
        payload["preferred_sections"] = [
            item.strip() for item in payload.get("preferred_sections", []) if str(item).strip()
        ]
        payload["exact_seats"] = [
            normalize_seat_id(str(item)) for item in payload.get("exact_seats", []) if str(item).strip()
        ]
        payload["notify_methods"] = [
            item.strip() for item in payload.get("notify_methods", []) if str(item).strip()
        ] or ["sms", "call"]
        payload["desired_ticket_count"] = max(1, int(payload.get("desired_ticket_count", 1)))
        payload["check_interval_sec"] = max(15, int(payload.get("check_interval_sec", 60)))
        # Drop unknown keys so dataclass __init__ doesn't complain
        known = {f.name for f in dc_fields(cls)}
        payload = {k: v for k, v in payload.items() if k in known}
        return cls(**payload)

    @classmethod
    def from_form(cls, form: Dict[str, Any]) -> "WatchConfig":
        return cls.from_dict(
            {
                "name": form.get("name", "").strip(),
                "movie_name": form.get("movie_name", "").strip(),
                "cinema_hall": form.get("cinema_hall", "").strip(),
                "show_time": form.get("show_time", "").strip(),
                "show_url": form.get("show_url", "").strip(),
                "preferred_sections": split_csv(form.get("preferred_sections", "")),
                "exact_seats": split_csv(form.get("exact_seats", "")),
                "desired_ticket_count": form.get("desired_ticket_count", 1),
                "check_interval_sec": form.get("check_interval_sec", 60),
                "notify_methods": (
                    form.getlist("notify_methods")
                    if hasattr(form, "getlist")
                    else form.get("notify_methods", [])
                ),
                "notify_on_initial_status": bool(form.get("notify_on_initial_status")),
                "notify_on_booking_open": bool(form.get("notify_on_booking_open")),
                "stop_after_first_alert": bool(form.get("stop_after_first_alert")),
                "expected_open_time": form.get("expected_open_time", "").strip(),
                "telegram_chat_id": form.get("telegram_chat_id", "").strip(),
                "active": True,
            }
        )

    def display_name(self) -> str:
        for value in [self.name, self.movie_name, self.show_time, self.show_url]:
            if value:
                return value
        return self.id

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScrapeSnapshot:
    booking_open: bool
    sold_out: bool
    page_title: str = ""
    available_sections: List[str] = field(default_factory=list)
    detected_sections: List[str] = field(default_factory=list)
    available_exact_seats: List[str] = field(default_factory=list)
    matched_sections: List[str] = field(default_factory=list)
    matched_exact_seats: List[str] = field(default_factory=list)
    contiguous_seat_groups: List[List[str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    raw_labels: List[str] = field(default_factory=list)
    captcha_detected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
