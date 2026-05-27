"""
Main watcher script - continuous monitoring loop.
Checks one config-driven watch at regular intervals and sends alerts.
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


def setup_logging(log_dir: str = "logs"):
    """Configure logging to console and daily log file."""
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    now = datetime.now()
    log_file = log_path / f"seatradar_{now.strftime('%Y%m%d')}.log"

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

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    logger.info(f"Logging initialized (file: {log_file})")
    return logger


async def main():
    """Main watcher loop."""
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("SeatRadar Watcher Started")
    logger.info("=" * 60)

    try:
        config = await load_config("config.json")
        await validate_config(config)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    watch = build_watch_from_config(config)
    alerted_event_keys = set()
    scraper = BookMyShowScraper(headless=config.get("headless", True))
    dispatcher = NotificationDispatcher(config)
    await scraper.init()

    try:
        iteration = 0
        while True:
            iteration += 1
            logger.info(f"\n--- Check #{iteration} ---")

            snapshot = await scraper.scrape_watch(watch)
            new_events = compute_new_event_keys(watch, snapshot, list(alerted_event_keys))
            if new_events:
                logger.info(f"Detected new events: {list(new_events.keys())}")
                success = await dispatcher.send_message_batch(list(new_events.values()), watch.notify_methods)
                if success:
                    alerted_event_keys.update(new_events.keys())
                    if watch.stop_after_first_alert:
                        logger.info("stop_after_first_alert is True, exiting loop")
                        break
            else:
                logger.info("No new preferred events detected")

            logger.info(f"Waiting {watch.check_interval_sec}s until next check...")
            await asyncio.sleep(watch.check_interval_sec)

    except KeyboardInterrupt:
        logger.info("\nWatcher interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await scraper.close()
        logger.info("SeatRadar Watcher Stopped")


if __name__ == "__main__":
    asyncio.run(main())
