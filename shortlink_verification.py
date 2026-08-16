"""Standalone shortlink verification for Movies Magic Club.

Uses Telegram deep-links as the post-shortlink callback. Shortlink creation has
bounded retries for temporary provider failures and does not create a new
verification token until a shortlink has been successfully created.
"""
from __future__ import annotations

import asyncio
import os
import random
import secrets
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
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
    max_attempts: int = int(os.getenv("SHORTLINK_MAX_ATTEMPTS", "3"))
    backoff_seconds: float = float(os.getenv("SHORTLINK_BACKOFF_SECONDS", "1"))
    max_backoff_seconds: float = float(os.getenv("SHORTLINK_MAX_BACKOFF_SECONDS", "10"))


CONFIG = VerificationConfig()


class ShortlinkError(RuntimeError):
    """Raised after all safe shortlink attempts have been exhausted."""


def new_token() -> str:
    return secrets.token_urlsafe(24)


def build_callback_url(token: str) -> str:
    if not CONFIG.bot_username:
        raise RuntimeError("BOT_USERNAME is not configured")
    return f"https://t.me/{CONFIG.bot_username}?start=verify_{token}"


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except Exception:
            return None


def _backoff(attempt: int) -> float:
    base = min(
        CONFIG.max_backoff_seconds,
        CONFIG.backoff_seconds * (2 ** max(attempt - 1, 0)),
    )
    return min(CONFIG.max_backoff_seconds, base + random.uniform(0, min(0.25, base * 0.25)))


async def create_shortlink(destination: str) -> str:
    """Create a shortlink with bounded retries for transient failures.

    A single destination is retried; no verification token is created here.
    HTTP 429 honors Retry-After when supplied. 5xx, timeouts and connection
    errors are retried. Client-side 4xx errors are treated as permanent.
    """
    if not CONFIG.shortlink_api_url or not CONFIG.shortlink_api_key:
        raise ShortlinkError("Shortlink provider is not configured")

    params = {"api": CONFIG.shortlink_api_key, "url": destination}
    if CONFIG.shortlink_domain:
        params["domain"] = CONFIG.shortlink_domain

    attempts = max(CONFIG.max_attempts, 1)
    timeout = aiohttp.ClientTimeout(total=max(CONFIG.request_timeout, 1))
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(CONFIG.shortlink_api_url, params=params) as response:
                    text = (await response.text()).strip()
                    if response.status == 429 or 500 <= response.status <= 599:
                        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                        if attempt < attempts:
                            await asyncio.sleep(
                                min(CONFIG.max_backoff_seconds, retry_after)
                                if retry_after is not None
                                else _backoff(attempt)
                            )
                            continue
                        last_error = ShortlinkError(
                            f"Shortlink provider temporary failure (HTTP {response.status})"
                        )
                        break
                    if response.status >= 400:
                        raise ShortlinkError(
                            f"Shortlink provider rejected request (HTTP {response.status})"
                        )
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
            raise ShortlinkError("Shortlink provider returned no usable URL")

        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(_backoff(attempt))
                continue
            break
        except ShortlinkError as exc:
            last_error = exc
            break

    raise ShortlinkError(
        f"Shortlink service is temporarily unavailable after {attempts} attempt(s). "
        "Please try again later."
    ) from last_error


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
        # Generate a token locally, but persist it only after the provider
        # successfully returns a shortlink. Failed API attempts therefore do
        # not leave orphaned/usable verification tokens in MongoDB.
        token = new_token()
        callback_url = build_callback_url(token)
        shortlink = await create_shortlink(callback_url)
        await self.db.db["verification_tokens"].insert_one({
            "token": token,
            "user_id": user_id,
            "file_id": file_id,
            "expires_at": time.time() + max(CONFIG.token_ttl_minutes, 1) * 60,
            "created_at": time.time(),
        })
        return token, shortlink

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
