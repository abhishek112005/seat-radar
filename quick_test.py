#!/usr/bin/env python3
"""
Quick Test Suite - Validates the SeatRadar application in one command.
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    END = "\033[0m"


def print_step(num, title):
    print(f"\n{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BLUE}Step {num}: {title}{Colors.END}")
    print(f"{Colors.BLUE}{'=' * 60}{Colors.END}")


def print_pass(msg):
    print(f"{Colors.GREEN}[PASS] {msg}{Colors.END}")


def print_fail(msg):
    print(f"{Colors.RED}[FAIL] {msg}{Colors.END}")


def print_warn(msg):
    print(f"{Colors.YELLOW}[WARN] {msg}{Colors.END}")


def print_info(msg):
    print(f"[INFO] {msg}")


async def test_imports():
    print_step(1, "Testing Imports")
    test_cases = [
        "config.settings",
        "scraper.bookmyshow",
        "notifiers.dispatcher",
        "notifiers.twilio_call",
        "notifiers.twilio_sms",
        "notifiers.twilio_whatsapp",
        "monitor",
        "auth",
        "app",
    ]
    passed = 0
    for module in test_cases:
        try:
            __import__(module)
            print_pass(f"{module} imported")
            passed += 1
        except Exception as e:
            print_fail(f"{module} failed: {e}")
    return passed == len(test_cases)


async def test_config():
    print_step(2, "Testing Configuration")
    config_file = Path("config.json")
    example_file = Path("config.json.example")

    if not example_file.exists():
        print_fail("config.json.example not found")
        return False

    try:
        with open(example_file, "r", encoding="utf-8") as f:
            example = json.load(f)
        print_pass("config.json.example is valid JSON")
    except json.JSONDecodeError as e:
        print_fail(f"config.json.example is invalid JSON: {e}")
        return False

    required_fields = [
        "show_name",
        "show_url",
        "preferred_sections",
        "exact_seats",
        "desired_ticket_count",
        "notify_methods",
        "notifiers",
    ]
    missing = [field for field in required_fields if field not in example]
    if missing:
        print_fail(f"Missing fields in example: {missing}")
        return False

    print_pass("All required config fields present in example")
    if config_file.exists():
        try:
            from config.settings import load_config, validate_config

            config = await load_config("config.json")
            await validate_config(config)
            print_pass("config.json loads and validates successfully")
        except Exception as e:
            print_fail(f"config.json validation failed: {e}")
            return False
    else:
        print_info("config.json not yet created (using example)")
    return True


async def test_scraper():
    print_step(3, "Testing Scraper Initialization")
    try:
        from scraper.bookmyshow import BookMyShowScraper

        scraper = BookMyShowScraper(headless=True)
        await scraper.init()
        print_pass("Playwright browser initialized")
        await scraper.close()
        print_pass("Playwright browser closed cleanly")
        return True
    except Exception as e:
        print_fail(f"Scraper initialization failed: {e}")
        print_warn("Ensure Playwright Chromium is installed: playwright install chromium")
        return False


async def test_notifier_init():
    print_step(4, "Testing Notifiers")
    try:
        from notifiers.dispatcher import NotificationDispatcher

        config = {
            "notify_methods": ["sms", "whatsapp", "call"],
            "notifiers": {
                "twilio": {
                    "account_sid": "x",
                    "auth_token": "y",
                    "from_number": "+10000000000",
                    "whatsapp_from_number": "whatsapp:+14155238886",
                    "to_number": "+10000000001",
                }
            },
        }
        dispatcher = NotificationDispatcher(config)
        print_pass(f"NotificationDispatcher initialized with: {sorted(dispatcher.notifiers.keys())}")
        return True
    except Exception as e:
        print_fail(f"Notifier initialization failed: {e}")
        return False


async def test_store():
    print_step(5, "Testing Watch Store")
    try:
        from monitor import WatchStore
        from models import WatchConfig

        store = WatchStore("data/test_watchlists.json")
        watch = WatchConfig(name="Quick Test", show_url="https://example.com", preferred_sections=["Gold"])
        await store.save_watch(watch)
        loaded = await store.get_watch(watch.id)
        print_pass(f"Watch persisted and reloaded: {loaded.display_name() if loaded else 'missing'}")
        await store.delete_watch(watch.id)
        return loaded is not None
    except Exception as e:
        print_fail(f"Watch store test failed: {e}")
        return False


async def test_files():
    print_step(6, "Verifying File Structure")
    required_files = [
        "watcher.py",
        "run_once.py",
        "test_notifications.py",
        "requirements.txt",
        "config.json.example",
        "README.md",
        "app.py",
        "monitor.py",
        "models.py",
        "config/settings.py",
        "scraper/bookmyshow.py",
        "notifiers/dispatcher.py",
        "notifiers/twilio_call.py",
        "notifiers/twilio_sms.py",
        "notifiers/twilio_whatsapp.py",
        "auth.py",
        "templates/login.html",
        "templates/verify.html",
        "templates/dashboard.html",
        "static/app.css",
    ]
    ok = True
    for filepath in required_files:
        if Path(filepath).exists():
            print_pass(filepath)
        else:
            print_fail(f"{filepath} - MISSING")
            ok = False
    return ok


async def test_requirements():
    print_step(7, "Checking Dependencies")
    imports = [
        ("playwright", "playwright"),
        ("twilio", "twilio"),
        ("fastapi", "fastapi"),
        ("jinja2", "jinja2"),
    ]
    for name, label in imports:
        try:
            __import__(name)
            print_pass(f"{label} installed")
        except ImportError:
            print_fail(f"{label} not installed - run: pip install -r requirements.txt")
            return False
    return True


def print_summary(results):
    print(f"\n{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BLUE}TEST SUMMARY{Colors.END}")
    print(f"{Colors.BLUE}{'=' * 60}{Colors.END}")
    total = len(results)
    passed = sum(1 for _, result in results if result)
    print(f"Total: {total}")
    print(f"{Colors.GREEN}Passed: {passed}{Colors.END}")
    print(f"{Colors.RED}Failed: {total - passed}{Colors.END}")
    return 0 if passed == total else 1


async def main():
    print(f"\n{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BLUE}SeatRadar Quick Test Suite{Colors.END}")
    print(f"{Colors.BLUE}Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.END}")
    print(f"{Colors.BLUE}{'=' * 60}{Colors.END}")

    results = [
        ("Imports", await test_imports()),
        ("Configuration", await test_config()),
        ("Scraper", await test_scraper()),
        ("Notifiers", await test_notifier_init()),
        ("Store", await test_store()),
        ("Files", await test_files()),
        ("Dependencies", await test_requirements()),
    ]
    sys.exit(print_summary(results))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Fatal error: {e}{Colors.END}")
        sys.exit(1)
