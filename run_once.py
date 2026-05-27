"""
Run a single check without the continuous loop.
Useful for CI/GitHub Actions integration.
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from config.settings import build_watch_from_config, load_config, validate_config
from monitor import compute_new_event_keys
from notifiers.dispatcher import NotificationDispatcher
from scraper.bookmyshow import BookMyShowScraper


def setup_logging():
    """Configure logging for a single run."""
    log_path = Path("logs")
    log_path.mkdir(exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    now = datetime.now()
    log_file = log_path / f"seatradar_{now.strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)


async def main():
    """Run a single availability check."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("SeatRadar Single Check")
    logger.info("=" * 60)

    try:
        config = await load_config("config.json")
        await validate_config(config)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    watch = build_watch_from_config(config)
    scraper = BookMyShowScraper(headless=config.get("headless", True))
    dispatcher = NotificationDispatcher(config)
    await scraper.init()

    try:
        logger.info(f"Checking {watch.display_name()}")
        snapshot = await scraper.scrape_watch(watch)
        logger.info(
            "Snapshot: booking_open=%s matched_sections=%s matched_exact=%s contiguous_groups=%s",
            snapshot.booking_open,
            snapshot.matched_sections,
            snapshot.matched_exact_seats,
            snapshot.contiguous_seat_groups[:3],
        )

        new_events = compute_new_event_keys(watch, snapshot, [])
        messages = list(new_events.values())

        if not messages:
            logger.info("No preferred seats available yet")
            sys.exit(1)

        success = await dispatcher.send_message_batch(messages, watch.notify_methods)
        if success:
            logger.info("Alert sent successfully")
            sys.exit(0)

        logger.warning("Alert failed to send")
        sys.exit(2)

    except ValueError as e:
        logger.error(f"Check failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
