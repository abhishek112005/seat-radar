"""
CallMeBot WhatsApp notifications via free API.
https://www.callmebot.com/
"""
import asyncio
import logging
import aiohttp
from typing import Dict, Any


logger = logging.getLogger(__name__)


class CallMeBotWhatsAppNotifier:
    """
    Send WhatsApp notifications via CallMeBot free API.
    Requires WhatsApp number with country code (e.g., +919876543210).
    Limited to ~100 messages per day with free tier.
    """
    
    API_URL = "https://api.callmebot.com/whatsapp.php"
    
    def __init__(self, whatsapp: str):
        """
        Initialize with WhatsApp number.
        
        Args:
            whatsapp: WhatsApp number with country code (e.g., "+919876543210")
        """
        self.whatsapp = whatsapp
    
    async def send(self, message: str) -> bool:
        """
        Send WhatsApp message.
        
        Args:
            message: Message text
        
        Returns:
            True if successful, False otherwise
        """
        try:
            params = {
                "phone": self.whatsapp,
                "text": message,
                "apikey": "0"  # CallMeBot free API key
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.API_URL, params=params, timeout=aiohttp.ClientTimeout(10)) as resp:
                    if resp.status == 200:
                        logger.info(f"WhatsApp message sent to {self.whatsapp}")
                        return True
                    else:
                        logger.error(f"WhatsApp API returned status {resp.status}: {await resp.text()}")
                        return False
        
        except asyncio.TimeoutError:
            logger.error("WhatsApp request timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            return False
