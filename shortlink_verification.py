"""Standalone shortlink verification for Movies Magic Club.

Uses Telegram deep-links as the post-shortlink callback, so no separate web
server is required. Tokens are one-time and bound to the Telegram user.
"""
from __future__ import annotations

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
    bot_username: str = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    free_limit: int = int(os.getenv("VERIFICATION_FREE_LIMIT", "3"))
    valid_minutes: int = int(os.getenv("VERIFICATION_VALID_MINUTES", "60"))
    token_ttl_minutes: int = int(os.getenv("VERIFICATION_TOKEN_TTL_MINUTES", "30"))
    request_timeout: int = int(os.getenv("SHORTLINK_TIMEOUT_SECONDS", "15"))


CONFIG = VerificationConfig()


def new_token() -> str:
    return secrets.token_urlsafe(24)


def build_callback_url(token: str) -> str:
    if not CONFIG.bot_username:
        raise RuntimeError("BOT_USERNAME is not configured")
    return f"https://t.me/{CONFIG.bot_username}?start=verify_{token}"


async def create_shortlink(destination: str) -> str:
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
    """Mongo-backed verification state and one-time file-delivery tokens."""
    def __init__(self, db):
        self.db = db

    async def setup(self) -> None:
        await self.db.db["verification_tokens"].create_index("token", unique=True)
        await self.db.db["verification_tokens"].create_index("expires_at", expireAfterSeconds=0)
        await self.db.db["verification_state"].create_index("user_id", unique=True)

    async def is_verified(self, user_id: int) -> bool:
        doc = await self.db.db["verification_state"].find_one({"user_id": user_id})
        return bool(doc and float(doc.get("verified_until", 0)) > time.time())

    async def should_require(self, user_id: int) -> bool:
        if not CONFIG.enabled or await self.is_verified(user_id):
            return False
        doc = await self.db.db["verification_state"].find_one({"user_id": user_id})
        return int((doc or {}).get("free_used", 0)) >= max(CONFIG.free_limit, 0)

    async def record_free_delivery(self, user_id: int) -> None:
        if not CONFIG.enabled:
            return
        await self.db.db["verification_state"].update_one(
            {"user_id": user_id},
            {"$inc": {"free_used": 1}, "$set": {"updated_at": time.time()}},
            upsert=True,
        )

    async def create(self, user_id: int, file_id: str) -> tuple[str, str]:
        token = new_token()
        await self.db.db["verification_tokens"].insert_one({
            "token": token,
            "user_id": user_id,
            "file_id": file_id,
            "expires_at": time.time() + max(CONFIG.token_ttl_minutes, 1) * 60,
            "created_at": time.time(),
        })
        return token, await create_shortlink(build_callback_url(token))

    async def consume(self, token: str, user_id: int) -> Optional[dict]:
        doc = await self.db.db["verification_tokens"].find_one_and_delete({
            "token": token,
            "user_id": user_id,
        })
        if not doc or float(doc.get("expires_at", 0)) < time.time():
            return None
        await self.db.db["verification_state"].update_one(
            {"user_id": user_id},
            {"$set": {
                "verified_until": time.time() + max(CONFIG.valid_minutes, 0) * 60,
                "updated_at": time.time(),
            }},
            upsert=True,
        )
        return doc


async def maybe_create_verification(store: VerificationStore, user_id: int, file_id: str) -> Optional[str]:
    """Return a shortlink when verification is required; otherwise allow delivery."""
    if not await store.should_require(user_id):
        await store.record_free_delivery(user_id)
        return None
    return (await store.create(user_id, file_id))[1]
