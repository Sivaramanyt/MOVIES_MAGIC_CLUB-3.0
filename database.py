from motor.motor_asyncio import AsyncIOMotorClient


class Database:
    def __init__(self, uri: str, name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[name]
        self.files = self.db.files
        self.users = self.db.users

    async def setup(self):
        await self.files.create_index("file_id", unique=True)
        await self.files.create_index([("search_text", "text")])
        await self.files.create_index("title")
        await self.users.create_index("user_id", unique=True)

    async def add_user(self, user_id: int):
        await self.users.update_one({"user_id": user_id}, {"$set": {"user_id": user_id}}, upsert=True)

    async def add_file(self, data: dict) -> bool:
        try:
            await self.files.insert_one(data)
            return True
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                return False
            raise

    async def search_files(self, query: str, skip: int, limit: int):
        regex = {"$regex": query.replace(".", r"\.").replace("*", "").strip(), "$options": "i"}
        cursor = self.files.find({"search_text": regex}).sort("_id", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def count_files(self, query: str) -> int:
        regex = {"$regex": query.replace(".", r"\.").replace("*", "").strip(), "$options": "i"}
        return await self.files.count_documents({"search_text": regex})

    async def stats(self):
        return await self.files.count_documents({}), await self.users.count_documents({})
