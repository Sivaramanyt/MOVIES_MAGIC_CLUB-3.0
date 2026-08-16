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
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "en-US").strip() or "en-US"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Safe duplicate policy: all three fields must match after normalization.
AUTO_DELETE_DUPLICATES = os.getenv("AUTO_DELETE_DUPLICATES", "true").strip().lower() in {"1", "true", "yes", "on"}
DUPLICATE_REQUIRE_ALL_FIELDS = os.getenv("DUPLICATE_REQUIRE_ALL_FIELDS", "true").strip().lower() in {"1", "true", "yes", "on"}
# Reindex is a dry run by default. Deletion requires the explicit --delete flag.
REINDEX_DRY_RUN = os.getenv("REINDEX_DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
