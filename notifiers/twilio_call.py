"""
Twilio voice call notifications using TwiML.
Free $15 trial credit is sufficient for testing (costs ~$0.05-0.10 per call).
"""
import asyncio
import logging
from typing import Optional
from twilio.rest import Client


logger = logging.getLogger(__name__)


class TwilioCallNotifier:
    """
    Send voice call notifications via Twilio.
    Uses TwiML to read message text-to-speech.
    Requires Twilio account with credits.
    """
    
    def __init__(self, account_sid: str, auth_token: str, 
                 from_number: str, to_number: str):
        """
        Initialize with Twilio credentials.
        
        Args:
            account_sid: Twilio account SID
            auth_token: Twilio auth token
            from_number: Twilio phone number or alphanumeric sender ID
            to_number: Destination phone number with country code
        """
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.to_number = to_number
        self.client = Client(account_sid, auth_token)
    
    async def send(self, message: str) -> bool:
        """
        Send voice call with text-to-speech message.
        
        Args:
            message: Message to be read aloud
        
        Returns:
            True if successful, False otherwise
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Run blocking Twilio call in thread pool
            call = await loop.run_in_executor(
                None,
                self._make_call,
                message
            )
            
            if call.sid:
                logger.info(f"Voice call initiated to {self.to_number} (SID: {call.sid})")
                return True
            else:
                logger.error("Failed to initiate voice call")
                return False
        
        except Exception as e:
            logger.error(f"Failed to send voice call: {e}")
            return False
    
    def _make_call(self, message: str) -> Optional[object]:
        """
        Blocking method to make Twilio call with TwiML.
        Called in thread pool by send().
        """
        try:
            # TwiML: Read message using text-to-speech
            twiml = f'<Response><Say>{message}</Say></Response>'
            
            call = self.client.calls.create(
                to=self.to_number,
                from_=self.from_number,
                twiml=twiml
            )
            
            return call
        
        except Exception as e:
            logger.error(f"Twilio API error: {e}")
            return None
