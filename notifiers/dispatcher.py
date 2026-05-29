"""
Notification dispatcher for SeatRadar.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from notifiers.twilio_call import TwilioCallNotifier
from notifiers.twilio_sms import TwilioSMSNotifier
from notifiers.twilio_whatsapp import TwilioWhatsAppNotifier


logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Routes notifications to the configured Twilio notifiers."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.default_methods = config.get("notify_methods", [])
        self.notifiers = {}
        self._twilio: Dict[str, str] = {}
        self._init_notifiers()

    def _init_notifiers(self):
        twilio = self.config.get("notifiers", {}).get("twilio", {})
        if not all([twilio.get("account_sid"), twilio.get("auth_token"), twilio.get("from_number")]):
            return

        self._twilio = {
            "account_sid": twilio["account_sid"],
            "auth_token": twilio["auth_token"],
            "from_number": twilio["from_number"],
            "whatsapp_from": twilio.get("whatsapp_from_number") or twilio["from_number"],
        }

        default_to = twilio.get("to_number", "")
        if default_to:
            self.notifiers = self._build_notifiers(default_to)
            logger.info("Twilio SMS/WhatsApp/call notifiers initialized")

    def _build_notifiers(self, to_number: str) -> dict:
        t = self._twilio
        return {
            "sms": TwilioSMSNotifier(t["account_sid"], t["auth_token"], t["from_number"], to_number),
            "whatsapp": TwilioWhatsAppNotifier(t["account_sid"], t["auth_token"], t["whatsapp_from"], to_number),
            "call": TwilioCallNotifier(t["account_sid"], t["auth_token"], t["from_number"], to_number),
        }

    async def send_message_to(
        self, message: str, to_number: str, methods: Optional[Iterable[str]] = None
    ) -> bool:
        """Send to a specific number, overriding the default to_number."""
        if not to_number or not self._twilio:
            return await self.send_message(message, methods)
        notifiers = self._build_notifiers(to_number)
        chosen = list(methods or self.default_methods)
        results = {}
        for method in chosen:
            notifier = notifiers.get(method)
            if not notifier:
                results[method] = False
                continue
            try:
                results[method] = await notifier.send(message)
            except Exception as exc:
                logger.error("Error sending %s to %s: %s", method, to_number, exc)
                results[method] = False
        return any(results.values())

    async def send_message_batch_to(
        self, messages: List[str], to_number: str, methods: Optional[Iterable[str]] = None
    ) -> bool:
        overall = False
        for message in messages:
            overall = await self.send_message_to(message, to_number, methods) or overall
        return overall

    async def send_message(self, message: str, methods: Optional[Iterable[str]] = None) -> bool:
        """Send one message through the selected notification methods."""
        chosen_methods = list(methods or self.default_methods)
        if not chosen_methods:
            logger.warning("No notification methods selected")
            return False

        results = {}
        for method in chosen_methods:
            notifier = self.notifiers.get(method)
            if not notifier:
                logger.warning(f"Notifier '{method}' is not configured")
                results[method] = False
                continue
            try:
                success = await notifier.send(message)
                results[method] = success
                logger.info(f"Notification via {method}: {'sent' if success else 'failed'}")
            except Exception as e:
                logger.error(f"Error sending {method} notification: {e}")
                results[method] = False
        return any(results.values())

    async def send_message_batch(self, messages: List[str], methods: Optional[Iterable[str]] = None) -> bool:
        """Send multiple messages sequentially through the selected methods."""
        overall = False
        for message in messages:
            success = await self.send_message(message, methods)
            overall = overall or success
        return overall

    async def send_alert(self, show_name: str, seats: List[str], methods: Optional[Iterable[str]] = None) -> bool:
        """Backwards-compatible helper used by the CLI scripts."""
        seat_summary = ", ".join(seats)
        return await self.send_message(
            f"SeatRadar alert. {show_name}. Available seats or sections: {seat_summary}",
            methods,
        )
