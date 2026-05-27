"""
Configuration loader — merges config.json with environment variable overrides.
Environment variables take priority over config.json values (Railway-friendly).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from models import WatchConfig


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DEFAULT_CONFIG: Dict[str, Any] = {
    "show_name": "My Event",
    "show_url": "https://in.bookmyshow.com/events/...",
    "preferred_sections": ["Gold", "Platinum"],
    "preferred_seats": ["Gold", "Platinum"],
    "exact_seats": [],
    "desired_ticket_count": 1,
    "check_interval_sec": 60,
    "stop_after_first_alert": False,
    "headless": True,
    "use_proxy": False,
    "notify_methods": ["sms", "call"],
    "notify_on_initial_status": True,
    "notify_on_booking_open": True,
    "web_host": "0.0.0.0",
    "web_port": 8000,
    "telegram_bot_token": "",
    "notifiers": {
        "twilio": {
            "account_sid": "",
            "auth_token": "",
            "from_number": "",
            "whatsapp_from_number": "",
            "to_number": "",
            "verify_service_sid": "",
        }
    },
}


async def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """Load config.json, merge with defaults, then override with env vars."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(
            f"config.json not found at {config_path}\n"
            "Create one using config.json.example as a template."
        )

    with open(config_file, "r", encoding="utf-8") as f:
        user_config = json.load(f)

    config = DEFAULT_CONFIG.copy()
    config.update(user_config)

    # Deep-merge notifiers.twilio
    if "notifiers" in user_config:
        config["notifiers"] = {**DEFAULT_CONFIG["notifiers"], **user_config["notifiers"]}
        if "twilio" in user_config["notifiers"]:
            config["notifiers"]["twilio"] = {
                **DEFAULT_CONFIG["notifiers"]["twilio"],
                **user_config["notifiers"]["twilio"],
            }

    # preferred_sections fallback
    if not config.get("preferred_sections") and config.get("preferred_seats"):
        config["preferred_sections"] = list(config.get("preferred_seats", []))

    # ── Environment variable overrides (Railway / CI-friendly) ────────────────
    tw = config["notifiers"]["twilio"]

    _ov(tw, "account_sid",       "TWILIO_ACCOUNT_SID")
    _ov(tw, "auth_token",        "TWILIO_AUTH_TOKEN")
    _ov(tw, "from_number",       "TWILIO_FROM_NUMBER")
    _ov(tw, "whatsapp_from_number", "TWILIO_WHATSAPP_FROM")
    _ov(tw, "to_number",         "TWILIO_TO_NUMBER")
    _ov(tw, "verify_service_sid","TWILIO_VERIFY_SID")
    _ov(config, "telegram_bot_token", "TELEGRAM_BOT_TOKEN")

    port = _env("PORT")
    if port:
        config["web_port"] = int(port)

    return config


def _ov(d: Dict, key: str, env_var: str) -> None:
    """Override dict[key] with env_var if env_var is set."""
    val = _env(env_var)
    if val:
        d[key] = val


async def validate_config(config: Dict[str, Any]) -> bool:
    if not config.get("show_name"):
        raise ValueError("show_name is required")
    if not config.get("show_url"):
        raise ValueError("show_url is required")

    preferred = config.get("preferred_sections") or config.get("preferred_seats")
    if not preferred and not config.get("exact_seats") and not config.get("notify_on_booking_open", True):
        raise ValueError(
            "Provide preferred_sections, exact_seats, or enable notify_on_booking_open"
        )

    methods = config.get("notify_methods", [])
    if not isinstance(methods, list):
        raise ValueError("notify_methods must be a list")
    bad = [m for m in methods if m not in {"call", "sms", "whatsapp"}]
    if bad:
        raise ValueError(f"Unsupported notify_methods: {bad}")
    if not methods:
        raise ValueError("notify_methods must include at least one method")

    tw = config["notifiers"]["twilio"]
    if not all([tw.get("account_sid"), tw.get("auth_token"),
                tw.get("from_number"), tw.get("to_number")]):
        raise ValueError(
            "Twilio credentials (account_sid, auth_token, from_number, to_number) required"
        )
    return True


def build_watch_from_config(config: Dict[str, Any]) -> WatchConfig:
    return WatchConfig.from_dict({
        "name":                   config.get("show_name", ""),
        "movie_name":             config.get("show_name", ""),
        "cinema_hall":            config.get("cinema_hall", ""),
        "show_time":              config.get("show_time", ""),
        "show_url":               config.get("show_url", ""),
        "preferred_sections":     config.get("preferred_sections") or config.get("preferred_seats", []),
        "exact_seats":            config.get("exact_seats", []),
        "desired_ticket_count":   config.get("desired_ticket_count", 1),
        "check_interval_sec":     config.get("check_interval_sec", 60),
        "notify_methods":         config.get("notify_methods", ["sms", "call"]),
        "notify_on_initial_status": config.get("notify_on_initial_status", True),
        "notify_on_booking_open": config.get("notify_on_booking_open", True),
        "stop_after_first_alert": config.get("stop_after_first_alert", False),
        "expected_open_time":     config.get("expected_open_time", ""),
        "active": True,
    })


def get_state_file_path(show_name: str) -> Path:
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in show_name)
    return logs_dir / f"{safe}_alerted_seats.json"
