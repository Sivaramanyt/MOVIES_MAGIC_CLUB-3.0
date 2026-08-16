from motor.motor_asyncio import AsyncIOMotorClient


class Database:
    def __init__(self, uri: str, name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[name]
        self.files = self.db.files
        self.users = self.db.users
        self.movies = self.db.movies

    async def setup(self):
        await self.files.create_index("file_id", unique=True)
        await self.files.create_index("file_unique_id", unique=True, sparse=True)
        await self.files.create_index([("search_text", 1)])
        await self.files.create_index("title")
        await self.files.create_index("tmdb_id")
        await self.files.create_index([("normalized_title", 1), ("year", 1)])
        await self.files.create_index([("duplicate_name", 1), ("size", 1), ("quality", 1)])
        await self.movies.create_index("tmdb_id", unique=True, sparse=True)
        await self.movies.create_index([("normalized_title", 1), ("year", 1)])
        await self.users.create_index("user_id", unique=True)

    async def add_user(self, user_id: int):
        await self.users.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id}},
            upsert=True,
        )

    async def find_duplicate_file(self, name: str, size: int, quality: str | None, exclude_unique_id: str | None = None):
        """Find an older file with the same normalized name, size and quality."""
        import re
        duplicate_name = re.sub(r"\s+", " ", (name or "").strip()).casefold()
        query = {
            "duplicate_name": duplicate_name,
            "size": int(size or 0),
            "quality": quality or "",
        }
        if exclude_unique_id:
            query["file_unique_id"] = {"$ne": exclude_unique_id}
        return await self.files.find_one(query, sort=[("_id", 1)])

    async def add_file(self, data: dict) -> bool:
        try:
            await self.files.insert_one(data)
            return True
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                return False
            raise

    async def update_file(self, file_id: str, data: dict):
        await self.files.update_one({"file_id": file_id}, {"$set": data})

    async def update_file_tmdb(self, file_id: str, metadata: dict):
        await self.files.update_one({"file_id": file_id}, {"$set": metadata})

    async def search_files(self, query: str, skip: int, limit: int):
        safe = query.replace(".", r"\.").replace("*", "").strip()
        regex = {"$regex": safe, "$options": "i"}
        cursor = self.files.find({"search_text": regex}).sort("_id", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def count_files(self, query: str) -> int:
        safe = query.replace(".", r"\.").replace("*", "").strip()
        regex = {"$regex": safe, "$options": "i"}
        return await self.files.count_documents({"search_text": regex})

    @staticmethod
    def _group_pipeline(regex: dict):
        return [
            {"$match": {"search_text": regex}},
            {
                "$addFields": {
                    "group_key": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": [{"$ifNull": ["$tmdb_id", None]}, None]},
                                    {"$ne": [{"$ifNull": ["$tmdb_id", None]}, ""]},
                                ]
                            },
                            {"$concat": ["tmdb:", {"$toString": "$tmdb_id"}]},
                            {
                                "$concat": [
                                    "title:",
                                    {"$toLower": {"$ifNull": ["$normalized_title", "$title"]}},
                                    ":year:",
                                    {"$toString": {"$ifNull": ["$year", 0]}},
                                ]
                            },
                        ]
                    }
                }
            },
        ]

    async def find_grouped_movies(self, query: str, skip: int, limit: int):
        safe = query.replace(".", r"\.").replace("*", "").strip()
        regex = {"$regex": safe, "$options": "i"}
        pipeline = self._group_pipeline(regex) + [
            {"$sort": {"_id": -1}},
            {
                "$group": {
                    "_id": "$group_key",
                    "representative": {"$first": "$$ROOT"},
                    "file_ids": {"$push": "$file_id"},
                    "languages": {"$addToSet": "$languages"},
                    "qualities": {"$addToSet": "$quality"},
                }
            },
            {"$sort": {"representative._id": -1}},
            {"$skip": skip},
            {"$limit": limit},
        ]
        rows = await self.files.aggregate(pipeline).to_list(length=limit)
        for row in rows:
            rep = row["representative"]
            langs = sorted({x for group in row.get("languages", []) if group for x in group})
            quals = sorted({x for x in row.get("qualities", []) if x})
            rep["group_file_ids"] = row.get("file_ids", [])
            rep["group_languages"] = langs
            rep["group_qualities"] = quals
            row["representative"] = rep
        return rows

    async def count_grouped_movies(self, query: str) -> int:
        safe = query.replace(".", r"\.").replace("*", "").strip()
        regex = {"$regex": safe, "$options": "i"}
        pipeline = self._group_pipeline(regex) + [
            {"$group": {"_id": "$group_key"}},
            {"$count": "total"},
        ]
        rows = await self.files.aggregate(pipeline).to_list(length=1)
        return rows[0]["total"] if rows else 0

    async def get_movie_files(self, representative: dict):
        tmdb_id = representative.get("tmdb_id")
        if tmdb_id:
            return await self.files.find({"tmdb_id": tmdb_id}).sort([("quality", 1), ("_id", -1)]).to_list(length=500)
        return await self.files.find({
            "normalized_title": representative.get("normalized_title"),
            "year": representative.get("year"),
        }).sort([("quality", 1), ("_id", -1)]).to_list(length=500)

    async def get_movie(self, tmdb_id: int):
        return await self.movies.find_one({"tmdb_id": tmdb_id})

    async def save_movie(self, metadata: dict):
        if metadata.get("tmdb_id"):
            await self.movies.update_one(
                {"tmdb_id": metadata["tmdb_id"]},
                {"$set": metadata},
                upsert=True,
            )

    async def stats(self):
        return (
            await self.files.count_documents({}),
            await self.users.count_documents({}),
            await self.movies.count_documents({}),
        )
