"""
CallMeBot SMS notifications via free API.
https://www.callmebot.com/
"""
import logging
import aiohttp
from typing import Dict, Any


logger = logging.getLogger(__name__)


class CallMeBotSMSNotifier:
    """
    Send SMS notifications via CallMeBot free API.
    Requires phone number with country code (e.g., +919876543210).
    Limited to ~100 SMS per day with free tier.
    """
    
    API_URL = "https://api.callmebot.com/text.php"
    
    def __init__(self, phone: str):
        """
        Initialize with phone number.
        
        Args:
            phone: Phone number with country code (e.g., "+919876543210")
        """
        self.phone = phone
    
    async def send(self, message: str) -> bool:
        """
        Send SMS message.
        
        Args:
            message: Message text (will be truncated to 160 chars by SMS standard)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Truncate message to SMS limit
            sms_message = message[:160]
            
            params = {
                "phone": self.phone,
                "text": sms_message,
                "apikey": "0"  # CallMeBot free API key
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.API_URL, params=params, timeout=aiohttp.ClientTimeout(10)) as resp:
                    if resp.status == 200:
                        logger.info(f"SMS sent to {self.phone}")
                        return True
                    else:
                        logger.error(f"SMS API returned status {resp.status}: {await resp.text()}")
                        return False
        
        except asyncio.TimeoutError:
            logger.error("SMS request timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")
            return False


import asyncio
