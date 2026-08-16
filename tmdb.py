import re
from typing import Optional

import aiohttp

from config import TMDB_API_KEY, TMDB_IMAGE_BASE, TMDB_LANGUAGE


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str = TMDB_API_KEY):
        self.api_key = api_key
        self._genres = {}

    async def search(self, title: str, year: Optional[int] = None) -> Optional[dict]:
        if not self.api_key:
            return None
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None
        params = {"api_key": self.api_key, "query": title, "language": TMDB_LANGUAGE, "include_adult": "false"}
        if year:
            params["year"] = year
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/search/multi", params=params, timeout=8) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
        except (aiohttp.ClientError, TimeoutError):
            return None
        results = [x for x in data.get("results", []) if x.get("media_type") in {"movie", "tv"}]
        if not results:
            return None
        if year:
            exact = [x for x in results if str(x.get("release_date", x.get("first_air_date", "")))[:4] == str(year)]
            if exact:
                results = exact
        return self._normalize(results[0])

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
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/genre/{endpoint}/list", params=params, timeout=8) as response:
                    if response.status != 200:
                        return {}
                    data = await response.json()
                    self._genres[media_type] = {x["id"]: x["name"] for x in data.get("genres", [])}
                    return self._genres[media_type]
        except (aiohttp.ClientError, TimeoutError):
            return {}
