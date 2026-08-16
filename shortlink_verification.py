"""Optional shortlink verification module for Movies Magic Club.

Keeps monetization/verification logic separate from the Telegram movie indexer.
Configure through environment variables; no shortlink provider credentials are
hard-coded here.

Flow:
    /verify <target>
        -> create one-time token
        -> build verification URL
        -> shorten it with the configured provider
        -> user completes the shortlink
        -> /verify/callback/<token> marks the token used
        -> bot grants a temporary verification window

This module intentionally supports generic JSON/text shortener APIs. Adapt
SHORTLINK_REQUEST_STYLE if your provider uses a different API contract.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass(frozen=True)
class VerificationConfig:
    enabled: bool = os.getenv("VERIFICATION_ENABLED", "false").lower() == "true"
    shortlink_api_url: str = os.getenv("SHORTLINK_API_URL", "").strip()
    shortlink_api_key: str = os.getenv("SHORTLINK_API_KEY", "").strip()
    shortlink_domain: str = os.getenv("SHORTLINK_DOMAIN", "").strip()
    verification_base_url: str = os.getenv("VERIFICATION_BASE_URL", "").rstrip("/")
    free_limit: int = int(os.getenv("VERIFICATION_FREE_LIMIT", "3"))
    valid_minutes: int = int(os.getenv("VERIFICATION_VALID_MINUTES", "60"))
    token_ttl_minutes: int = int(os.getenv("VERIFICATION_TOKEN_TTL_MINUTES", "30"))
    request_timeout: int = int(os.getenv("SHORTLINK_TIMEOUT_SECONDS", "15"))


CONFIG = VerificationConfig()


def new_token() -> str:
    return secrets.token_urlsafe(24)


def build_verification_url(token: str) -> str:
    if not CONFIG.verification_base_url:
        raise RuntimeError("VERIFICATION_BASE_URL is not configured")
    return f"{CONFIG.verification_base_url}/verify/callback/{token}"


async def create_shortlink(destination: str) -> str:
    """Create a monetized shortlink using the configured provider.

    Expected provider responses can be JSON with one of: shortlink, short_url,
    shortenedUrl, shortUrl, url, link; or a plain-text URL response.
    """
    if not CONFIG.shortlink_api_url or not CONFIG.shortlink_api_key:
        raise RuntimeError("SHORTLINK_API_URL/SHORTLINK_API_KEY are not configured")

    params = {"api": CONFIG.shortlink_api_key, "url": destination}
    if CONFIG.shortlink_domain:
        params["domain"] = CONFIG.shortlink_domain

    timeout = aiohttp.ClientTimeout(total=CONFIG.request_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(CONFIG.shortlink_api_url, params=params) as response:
            text = (await response.text()).strip()
            if response.status >= 400:
                raise RuntimeError(f"Shortlink provider returned HTTP {response.status}")
            try:
                payload: Any = await response.json(content_type=None)
            except Exception:
                payload = text

    if isinstance(payload, dict):
        for key in ("shortlink", "short_url", "shortenedUrl", "shortUrl", "url", "link"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    if isinstance(payload, str) and payload.startswith(("http://", "https://")):
        return payload
    raise RuntimeError("Shortlink provider returned no usable URL")


class VerificationStore:
    """Mongo-backed token store.

    Pass the existing Database instance from bot.py. The store uses the same
    Mongo database and therefore does not introduce a second database layer.
    """

    def __init__(self, db):
        self.db = db

    async def setup(self) -> None:
        await self.db.db["verification_tokens"].create_index("token", unique=True)
        await self.db.db["verification_tokens"].create_index("expires_at", expireAfterSeconds=0)
        await self.db.db["verification_state"].create_index("user_id", unique=True)

    async def create(self, user_id: int, destination: str) -> tuple[str, str]:
        token = new_token()
        expires_at = time.time() + CONFIG.token_ttl_minutes * 60
        await self.db.db["verification_tokens"].insert_one({
            "token": token,
            "user_id": user_id,
            "destination": destination,
            "expires_at": expires_at,
            "created_at": time.time(),
        })
        verify_url = build_verification_url(token)
        return token, await create_shortlink(verify_url)

    async def consume(self, token: str, user_id: int) -> Optional[dict]:
        doc = await self.db.db["verification_tokens"].find_one_and_delete({
            "token": token,
            "user_id": user_id,
        })
        if not doc or float(doc.get("expires_at", 0)) < time.time():
            return None
        await self.db.db["verification_state"].update_one(
            {"user_id": user_id},
            {"$set": {"verified_until": time.time() + CONFIG.valid_minutes * 60}},
            upsert=True,
        )
        return doc

    async def is_verified(self, user_id: int) -> bool:
        doc = await self.db.db["verification_state"].find_one({"user_id": user_id})
        return bool(doc and float(doc.get("verified_until", 0)) > time.time())


async def maybe_create_verification(store: VerificationStore, user_id: int, destination: str) -> Optional[str]:
    """Return a shortlink when verification is required, otherwise None."""
    if not CONFIG.enabled or await store.is_verified(user_id):
        return None
    return (await store.create(user_id, destination))[1]
