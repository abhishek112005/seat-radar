"""
Twilio WhatsApp notifications.
"""
import asyncio
import logging
from typing import Optional

from twilio.rest import Client


logger = logging.getLogger(__name__)


class TwilioWhatsAppNotifier:
    """Send WhatsApp notifications through Twilio."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_number: str):
        self.client = Client(account_sid, auth_token)
        self.from_number = from_number
        self.to_number = to_number

    async def send(self, message: str) -> bool:
        """Send a WhatsApp message."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._send_blocking, message)
            if result and getattr(result, "sid", None):
                logger.info(f"WhatsApp sent to {self.to_number} (SID: {result.sid})")
                return True
            logger.error("Failed to send WhatsApp message")
            return False
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            return False

    def _send_blocking(self, message: str) -> Optional[object]:
        """Send the WhatsApp message using the Twilio client."""
        try:
            from_number = self.from_number
            to_number = self.to_number
            if not from_number.startswith("whatsapp:"):
                from_number = f"whatsapp:{from_number}"
            if not to_number.startswith("whatsapp:"):
                to_number = f"whatsapp:{to_number}"
            return self.client.messages.create(
                to=to_number,
                from_=from_number,
                body=message[:1000],
            )
        except Exception as e:
            logger.error(f"Twilio WhatsApp API error: {e}")
            return None
