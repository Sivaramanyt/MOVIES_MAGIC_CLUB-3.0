import asyncio
import random
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from config import TMDB_API_KEY, TMDB_IMAGE_BASE, TMDB_LANGUAGE


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"
    MAX_RETRIES = 4
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 30.0
    REQUEST_TIMEOUT = 10

    def __init__(self, api_key: str = TMDB_API_KEY):
        self.api_key = api_key
        self._genres = {}
        self._search_cache = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_lock = asyncio.Lock()
        self._retry_after_until = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    @staticmethod
    def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    async def _wait_for_global_limit(self):
        async with self._rate_limit_lock:
            delay = self._retry_after_until - asyncio.get_running_loop().time()
            if delay > 0:
                await asyncio.sleep(delay)

    async def _request(self, endpoint: str, params: dict) -> Optional[dict]:
        if not self.api_key:
            return None

        for attempt in range(self.MAX_RETRIES + 1):
            await self._wait_for_global_limit()
            try:
                session = await self._get_session()
                async with session.get(f"{self.BASE_URL}{endpoint}", params=params) as response:
                    if response.status == 200:
                        return await response.json()

                    retryable = response.status == 429 or 500 <= response.status <= 599
                    if not retryable or attempt >= self.MAX_RETRIES:
                        return None

                    retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
                    exponential = min(self.MAX_BACKOFF, self.INITIAL_BACKOFF * (2 ** attempt))
                    delay = retry_after if retry_after is not None else exponential + random.uniform(0, exponential * 0.25)
                    delay = min(self.MAX_BACKOFF, max(0.0, delay))

                    if response.status == 429:
                        async with self._rate_limit_lock:
                            self._retry_after_until = max(
                                self._retry_after_until,
                                asyncio.get_running_loop().time() + delay,
                            )
                    await asyncio.sleep(delay)

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt >= self.MAX_RETRIES:
                    print(f"TMDB request exhausted retries: {exc}")
                    return None
                exponential = min(self.MAX_BACKOFF, self.INITIAL_BACKOFF * (2 ** attempt))
                delay = exponential + random.uniform(0, exponential * 0.25)
                await asyncio.sleep(min(self.MAX_BACKOFF, delay))
        return None

    async def search(self, title: str, year: Optional[int] = None) -> Optional[dict]:
        if not self.api_key:
            return None
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None

        cache_key = (title.casefold(), year)
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        params = {
            "api_key": self.api_key,
            "query": title,
            "language": TMDB_LANGUAGE,
            "include_adult": "false",
        }
        if year:
            params["year"] = year

        data = await self._request("/search/multi", params)
        if not data:
            return None
        results = [x for x in data.get("results", []) if x.get("media_type") in {"movie", "tv"}]
        if not results:
            self._search_cache[cache_key] = None
            return None
        if year:
            exact = [x for x in results if str(x.get("release_date", x.get("first_air_date", "")))[:4] == str(year)]
            if exact:
                results = exact
        metadata = self._normalize(results[0])
        self._search_cache[cache_key] = metadata
        return metadata

    async def enrich(self, title: str, year: Optional[int] = None) -> Optional[dict]:
        metadata = await self.search(title, year)
        if not metadata:
            return None
        genre_map = await self.genres(metadata.get("media_type", "movie"))
        metadata["genres"] = [genre_map[x] for x in metadata.get("genre_ids", []) if x in genre_map]
        return metadata

    def _normalize(self, item: dict) -> dict:
        media_type = item.get("media_type", "movie")
        date = item.get("release_date") or item.get("first_air_date") or ""
        poster_path = item.get("poster_path")
        return {
            "tmdb_id": item.get("id"),
            "media_type": media_type,
            "tmdb_title": item.get("title") or item.get("name") or "Unknown",
            "tmdb_year": int(date[:4]) if date[:4].isdigit() else None,
            "rating": round(float(item.get("vote_average") or 0), 1),
            "poster_url": f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None,
            "overview": item.get("overview") or "",
            "genre_ids": item.get("genre_ids") or [],
        }

    async def genres(self, media_type: str = "movie") -> dict:
        if not self.api_key:
            return {}
        if media_type in self._genres:
            return self._genres[media_type]
        endpoint = "movie" if media_type == "movie" else "tv"
        params = {"api_key": self.api_key, "language": TMDB_LANGUAGE}
        data = await self._request(f"/genre/{endpoint}/list", params)
        if not data:
            return {}
        self._genres[media_type] = {x["id"]: x["name"] for x in data.get("genres", [])}
        return self._genres[media_type]

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
