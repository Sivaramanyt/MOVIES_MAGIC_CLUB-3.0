import asyncio
import math
import time

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pyrogram.errors import UserNotParticipant

from config import (
    API_ID, API_HASH, BOT_TOKEN, MONGO_URI, DATABASE_NAME, CHANNEL_ID,
    ADMINS, FORCE_SUB_CHANNEL, RESULTS_PER_PAGE, TMDB_API_KEY,
)
from database import Database
from parser import parse_media
from tmdb import TMDBClient

app = Client("movies_magic_club", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db = Database(MONGO_URI, DATABASE_NAME)
tmdb = TMDBClient(TMDB_API_KEY)


def page_count(total: int) -> int:
    return max(1, math.ceil(total / RESULTS_PER_PAGE))


def poster_keyboard(query: str, page: int, total: int, group_key: str):
    pages = page_count(total)
    rows = [[InlineKeyboardButton("🎬 Select Movie", callback_data=f"movie|{group_key}")]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search|{page-1}|{query[:40]}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"search|{page+1}|{query[:40]}"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def movie_card(item: dict) -> str:
    title = item.get("tmdb_title") or item.get("title") or "Unknown"
    year = item.get("tmdb_year") or item.get("year")
    rating = item.get("rating")
    genres = item.get("genres") or []
    languages = item.get("group_languages") or item.get("languages") or []
    qualities = item.get("group_qualities") or []
    lines = [f"🎬 **{title}**"]
    details = []
    if year:
        details.append(f"📅 {year}")
    if rating is not None:
        details.append(f"⭐ {rating}/10")
    if details:
        lines.append(" • ".join(details))
    if genres:
        lines.append("🎭 " + ", ".join(genres))
    if languages:
        lines.append("🌐 " + ", ".join(x.title() for x in languages))
    if qualities:
        lines.append("🎞 " + ", ".join(qualities))
    return "\n".join(lines)


def file_card(item: dict) -> str:
    title = item.get("tmdb_title") or item.get("title") or "Movie"
    year = item.get("tmdb_year") or item.get("year")
    quality = item.get("quality") or "Quality unknown"
    languages = ", ".join(x.title() for x in (item.get("languages") or [])) or "Not specified"
    lines = [f"🎬 **{title}**"]
    if year:
        lines.append(f"📅 {year}")
    lines.append(f"🌐 {languages}")
    lines.append(f"🎞 {quality}")
    return "\n".join(lines)


async def enrich_item(item: dict) -> dict:
    if item.get("tmdb_id") and item.get("tmdb_title"):
        return item
    metadata = await tmdb.enrich(item.get("title", ""), item.get("year"))
    if not metadata:
        return item
    await db.update_file_tmdb(item["file_id"], metadata)
    await db.save_movie(metadata)
    item.update(metadata)
    return item


async def index_media_message(message) -> bool:
    media = message.document or message.video or message.audio
    if not media:
        return False
    name = getattr(media, "file_name", None) or message.caption or "Unknown"
    meta = parse_media(name, message.caption or "")
    data = {
        **meta,
        "file_id": media.file_id,
        "file_unique_id": media.file_unique_id,
        "file_name": name,
        "size": getattr(media, "file_size", 0),
        "mime_type": getattr(media, "mime_type", ""),
        "message_id": message.id,
        "channel_id": message.chat.id,
    }
    added = await db.add_file(data)
    if not added:
        return False
    try:
        enriched = await tmdb.enrich(meta.get("title", ""), meta.get("year"))
        if enriched:
            await db.update_file_tmdb(data["file_id"], enriched)
            await db.save_movie(enriched)
    except Exception as exc:
        print(f"TMDB enrichment failed for {name}: {exc}")
    return True


async def reindex_channel(progress_callback=None) -> dict:
    """Scan channel history and report live counters through progress_callback."""
    stats = {"scanned": 0, "indexed": 0, "enriched": 0, "failed": 0, "last_message_id": 0}
    async for history_message in app.get_chat_history(CHANNEL_ID):
        media = history_message.document or history_message.video or history_message.audio
        if not media:
            continue
        stats["scanned"] += 1
        stats["last_message_id"] = history_message.id
        try:
            name = getattr(media, "file_name", None) or history_message.caption or "Unknown"
            meta = parse_media(name, history_message.caption or "")
            data = {
                **meta,
                "file_id": media.file_id,
                "file_unique_id": media.file_unique_id,
                "file_name": name,
                "size": getattr(media, "file_size", 0),
                "mime_type": getattr(media, "mime_type", ""),
                "message_id": history_message.id,
                "channel_id": history_message.chat.id,
            }
            existing = await db.files.find_one({"file_unique_id": media.file_unique_id})
            if existing:
                await db.update_file(existing["file_id"], data)
                file_id = existing["file_id"]
            else:
                if not await db.add_file(data):
                    continue
                file_id = data["file_id"]
            stats["indexed"] += 1

            metadata = await tmdb.enrich(meta.get("title", ""), meta.get("year"))
            if metadata:
                await db.update_file_tmdb(file_id, metadata)
                await db.save_movie(metadata)
                stats["enriched"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"Reindex failed for message {getattr(history_message, 'id', '?')}: {exc}")

        if progress_callback:
            await progress_callback(dict(stats))
    return stats


def format_reindex_progress(stats: dict, started_at: float, running: bool = True) -> str:
    scanned = stats["scanned"]
    indexed = stats["indexed"]
    enriched = stats["enriched"]
    failed = stats["failed"]
    elapsed = max(time.monotonic() - started_at, 0.1)
    rate = scanned / elapsed
    state = "🔄 **Reindexing…**" if running else "✅ **Reindex complete**"
    return (
        f"{state}\n\n"
        f"📦 Scanned: `{scanned}`\n"
        f"🗂 Indexed: `{indexed}`\n"
        f"🧩 Enriched: `{enriched}`\n"
        f"⚠️ Failed: `{failed}`\n"
        f"⚡ Speed: `{rate:.1f} files/s`\n"
        f"🆔 Last message: `{stats.get('last_message_id', 0)}`"
    )


async def subscribed(client, user_id: int) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    try:
        await client.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception:
        return True


async def build_results(query: str, page: int):
    total = await db.count_grouped_movies(query)
    rows = await db.find_grouped_movies(query, page * RESULTS_PER_PAGE, RESULTS_PER_PAGE)
    items = []
    for row in rows:
        item = row["representative"]
        try:
            item = await enrich_item(item)
        except Exception:
            pass
        items.append(item)
    return total, items


async def send_movie_card(message, item: dict, query: str, page: int, total: int):
    if item.get("tmdb_id"):
        group_key = f"tmdb:{item['tmdb_id']}"
    else:
        group_key = f"fallback:{item.get('normalized_title', item.get('title', ''))}:{item.get('year') or 0}"
    caption = movie_card(item)
    markup = poster_keyboard(query, page, total, group_key)
    poster = item.get("poster_url")
    if poster:
        try:
            return await message.reply_photo(photo=poster, caption=caption, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
    return await message.reply_text(caption, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


async def render_search(message, query: str, page: int = 0):
    query = query.strip()
    if not query:
        return await message.reply_text("🔎 Send a movie or series name.")
    total, items = await build_results(query, page)
    if not total:
        return await message.reply_text(f"❌ No results found for **{query}**.", parse_mode=ParseMode.MARKDOWN)
    for item in items:
        await send_movie_card(message, item, query, page, total)


async def show_movie_options(message, selected: dict):
    files = await db.get_movie_files(selected)
    if not files:
        files = [selected]
    for item in files:
        try:
            await enrich_item(item)
        except Exception:
            pass
    title = selected.get("tmdb_title") or selected.get("title") or "Movie"
    year = selected.get("tmdb_year") or selected.get("year")
    languages = sorted({x for f in files for x in (f.get("languages") or [])})
    lines = [f"🎬 **{title}**"]
    if year:
        lines.append(f"📅 {year}")
    lines.append("\n🌐 **Select Language**")
    rows = []
    for i in range(0, len(languages), 2):
        rows.append([InlineKeyboardButton(lang.title(), callback_data=f"lang|{selected.get('tmdb_id', '')}|{lang}") for lang in languages[i:i + 2]])
    if not rows:
        rows = [[InlineKeyboardButton("🌐 All Languages", callback_data=f"lang|{selected.get('tmdb_id', '')}|all")]]
    return await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)


async def find_movie_files(tmdb_id, language=None, fallback_key=None):
    if tmdb_id:
        files = await db.files.find({"tmdb_id": int(tmdb_id)}).sort([("quality", 1), ("_id", -1)]).to_list(length=500)
    elif fallback_key:
        title, year = fallback_key.rsplit(":", 1)
        try:
            year = int(year)
        except ValueError:
            year = 0
        files = await db.files.find({"normalized_title": title, "year": year}).sort([("quality", 1), ("_id", -1)]).to_list(length=500)
    else:
        files = []
    if language and language != "all":
        files = [f for f in files if language.lower() in [x.lower() for x in (f.get("languages") or [])]]
    return files


@app.on_message(filters.command("start") & filters.private)
async def start(_, message):
    await db.add_user(message.from_user.id)
    if not await subscribed(app, message.from_user.id):
        return await message.reply_text("🔒 Please join the required channel first, then try again.")
    await message.reply_text("🎬 **Movies Magic Club 3.0**\n\nSend a movie or series name to search.", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(_, message):
    await message.reply_text("🔎 Send a movie/series name to search.\n\nAdmins: `/stats`\n🔄 Admin: `/reindex`", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("stats") & filters.private)
async def stats(_, message):
    if message.from_user.id not in ADMINS:
        return
    files, users, movies = await db.stats()
    await message.reply_text(f"📊 **Stats**\n\n📁 Files: `{files}`\n👤 Users: `{users}`\n🎬 TMDB movies: `{movies}`", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("reindex") & filters.private)
async def reindex_cmd(_, message):
    if message.from_user.id not in ADMINS:
        return await message.reply_text("⛔ You are not authorized to use this command.")

    started_at = time.monotonic()
    last_update = 0.0
    status = await message.reply_text(
        "🔄 **Reindexing…**\n\n📦 Scanned: `0`\n🗂 Indexed: `0`\n🧩 Enriched: `0`\n⚠️ Failed: `0`\n⚡ Speed: `0.0 files/s`",
        parse_mode=ParseMode.MARKDOWN,
    )

    async def progress(stats: dict):
        nonlocal last_update
        now = time.monotonic()
        # Telegram rate limits make per-file edits noisy. Update at most every
        # 3 seconds, plus immediately on the first processed file.
        if stats["scanned"] != 1 and now - last_update < 3:
            return
        last_update = now
        try:
            await status.edit_text(format_reindex_progress(stats, started_at), parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            print(f"Reindex progress update failed: {exc}")

    try:
        final = await reindex_channel(progress)
        await status.edit_text(format_reindex_progress(final, started_at, running=False), parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        await status.edit_text(f"❌ **Reindex failed**\n`{exc}`", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "stats", "reindex", "cancel"]))
async def search_handler(_, message):
    if not await subscribed(app, message.from_user.id):
        return await message.reply_text("🔒 Please join the required channel first.")
    await db.add_user(message.from_user.id)
    await render_search(message, message.text)


@app.on_callback_query(filters.regex(r"^search\|"))
async def page_callback(_, query: CallbackQuery):
    _, page, search = query.data.split("|", 2)
    page = max(0, int(page))
    total = await db.count_grouped_movies(search)
    page = min(page, page_count(total) - 1)
    await query.answer()
    await render_search(query.message, search, page)
    try:
        await query.message.delete()
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^movie\|"))
async def movie_callback(_, query: CallbackQuery):
    group_key = query.data.split("|", 1)[1]
    selected = None
    if group_key.startswith("tmdb:"):
        try:
            selected = await db.files.find_one({"tmdb_id": int(group_key.split(":", 1)[1])})
        except ValueError:
            pass
    elif group_key.startswith("fallback:"):
        value = group_key.split(":", 1)[1]
        title, year = value.rsplit(":", 1)
        try:
            year = int(year)
        except ValueError:
            year = 0
        selected = await db.files.find_one({"normalized_title": title, "year": year})
    if not selected:
        return await query.answer("Movie is no longer indexed.", show_alert=True)
    try:
        selected = await enrich_item(selected)
    except Exception:
        pass
    await query.answer()
    await show_movie_options(query.message, selected)


@app.on_callback_query(filters.regex(r"^lang\|"))
async def language_callback(_, query: CallbackQuery):
    _, tmdb_id, language = query.data.split("|", 2)
    files = await find_movie_files(tmdb_id, language)
    if not files:
        return await query.answer("No files found for this language.", show_alert=True)
    rows = []
    seen = set()
    for item in files:
        quality = item.get("quality") or "Quality unknown"
        key = (quality, item["file_id"])
        if key in seen:
            continue
        seen.add(key)
        rows.append([InlineKeyboardButton(f"🎞 {language.title()} • {quality}"[:60], callback_data=f"file|{item['file_id']}")])
    await query.answer()
    await query.message.edit_text("🎞 **Select Quality / File**", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)


@app.on_callback_query(filters.regex(r"^file\|"))
async def file_callback(_, query: CallbackQuery):
    file_id = query.data.split("|", 1)[1]
    item = await db.files.find_one({"file_id": file_id})
    if not item:
        return await query.answer("File no longer exists in the index.", show_alert=True)
    try:
        item = await enrich_item(item)
    except Exception:
        pass
    await query.answer("Sending file…")
    await app.send_cached_media(query.from_user.id, file_id, caption=file_card(item))


@app.on_callback_query(filters.regex(r"^noop$"))
async def noop(_, query: CallbackQuery):
    await query.answer()


@app.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=10)
async def index_channel(_, message):
    if message.chat.id != CHANNEL_ID:
        return
    await index_media_message(message)


async def main():
    await db.setup()
    await app.start()
    me = await app.get_me()
    print(f"@{me.username} is running")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
