"""
Test Twilio notifications without scraping.
"""
import asyncio
import logging
import sys

from config.settings import load_config, validate_config
from notifiers.dispatcher import NotificationDispatcher


def setup_logging():
    """Configure basic logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def test_notifications():
    """Place a test SMS and/or call using the configured Twilio notifier."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("SeatRadar Notification Test")
    logger.info("=" * 60)

    try:
        config = await load_config("config.json")
        await validate_config(config)
    except FileNotFoundError:
        logger.error("config.json not found")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    dispatcher = NotificationDispatcher(config)
    messages = [
        "SeatRadar test: booking-open SMS/call path is working.",
        "SeatRadar test: Gold and A2 alerts are configured.",
    ]

    logger.info(f"Testing notification methods: {config.get('notify_methods', [])}")
    success = await dispatcher.send_message_batch(messages, config.get("notify_methods", []))

    if success:
        logger.info("Notification test successful")
        logger.info("Check your phone for the incoming Twilio SMS and/or voice call")
        sys.exit(0)

    logger.error("Notification test failed")
    logger.info("Check the logs above for errors")
    logger.info("Verify your Twilio credentials in config.json")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_notifications())
