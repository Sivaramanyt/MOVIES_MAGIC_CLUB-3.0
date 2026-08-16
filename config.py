import os
from dotenv import load_dotenv

load_dotenv()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


API_ID = int(required("API_ID"))
API_HASH = required("API_HASH")
BOT_TOKEN = required("BOT_TOKEN")
MONGO_URI = required("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "movies_magic_club")
CHANNEL_ID = int(required("CHANNEL_ID"))
ADMINS = {int(x) for x in os.getenv("ADMINS", "").split() if x.strip().lstrip("-").isdigit()}
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "").strip()
RESULTS_PER_PAGE = max(1, int(os.getenv("RESULTS_PER_PAGE", "8")))
