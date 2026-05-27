"""
Google OAuth2 authentication helpers.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


class GoogleAuthService:
    def __init__(self, config: Dict[str, Any]) -> None:
        google_cfg = config.get("google_oauth", {})
        self.client_id = google_cfg.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID", "")
        self.client_secret = google_cfg.get("client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET", "")
        self.redirect_uri = (
            google_cfg.get("redirect_uri")
            or os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback")
        )

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Optional[str]:
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(GOOGLE_TOKEN_URL, data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                })
                resp.raise_for_status()
                return resp.json().get("access_token")
            except Exception as exc:
                logger.error("Token exchange failed: %s", exc)
                return None

    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.error("User info fetch failed: %s", exc)
                return None
