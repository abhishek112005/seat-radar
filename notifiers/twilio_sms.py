"""
Twilio SMS notifications.
"""
import asyncio
import logging
from typing import Optional

from twilio.rest import Client


logger = logging.getLogger(__name__)


class TwilioSMSNotifier:
    """Send SMS notifications through Twilio."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_number: str):
        self.to_number = to_number
        self.from_number = from_number
        self.client = Client(account_sid, auth_token)

    async def send(self, message: str) -> bool:
        """Send an SMS notification."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._send_blocking, message)
            if result and getattr(result, "sid", None):
                logger.info(f"SMS sent to {self.to_number} (SID: {result.sid})")
                return True
            logger.error("Failed to send SMS")
            return False
        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")
            return False

    def _send_blocking(self, message: str) -> Optional[object]:
        """Send the message using the Twilio client."""
        try:
            return self.client.messages.create(
                to=self.to_number,
                from_=self.from_number,
                body=message[:320],
            )
        except Exception as e:
            logger.error(f"Twilio SMS API error: {e}")
            return None
