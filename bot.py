import asyncio
import math

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, InputMediaPhoto
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


def result_keyboard(query: str, page: int, total: int):
    pages = max(1, math.ceil(total / RESULTS_PER_PAGE))
    rows = []
    if page > 0:
        rows.append([InlineKeyboardButton("⬅️ Previous", callback_data=f"search|{page-1}|{query[:40]}")])
    if page + 1 < pages:
        rows.append([InlineKeyboardButton("Next ➡️", callback_data=f"search|{page+1}|{query[:40]}")])
    return InlineKeyboardMarkup(rows) if rows else None


def language_text(item: dict) -> str:
    languages = item.get("languages") or []
    return ", ".join(x.title() for x in languages) if languages else "Not specified"


def format_card(item: dict, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    title = metadata.get("tmdb_title") or item.get("title") or "Unknown"
    year = metadata.get("year") or item.get("year")
    rating = metadata.get("rating")
    genres = metadata.get("genres") or []
    quality = item.get("quality")

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
    lines.append("🌐 " + language_text(item))
    if quality:
        lines.append(f"🎞 {quality}")
    return "\n".join(lines)


async def enrich_item(item: dict) -> dict:
    if item.get("tmdb_id") and item.get("tmdb_title"):
        return item
    metadata = await tmdb.search(item.get("title", ""), item.get("year"))
    if not metadata:
        return item
    genres = await tmdb.genres(metadata.get("media_type", "movie"))
    metadata["genres"] = [genres[x] for x in metadata.get("genre_ids", []) if x in genres]
    await db.update_file_tmdb(item["file_id"], metadata)
    item.update(metadata)
    return item


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
    total = await db.count_files(query)
    items = await db.search_files(query, page * RESULTS_PER_PAGE, RESULTS_PER_PAGE)
    enriched = []
    for item in items:
        try:
            enriched.append(await enrich_item(item))
        except Exception:
            enriched.append(item)
    return total, enriched


async def render_search(message, query: str, page: int = 0):
    query = query.strip()
    if not query:
        return await message.reply_text("🔎 Send a movie or series name.")
    total, items = await build_results(query, page)
    if not total:
        return await message.reply_text(f"❌ No results found for **{query}**.", parse_mode=ParseMode.MARKDOWN)

    text = [f"🔎 **Results for:** `{query}`", f"📚 Found: **{total}**", ""]
    buttons = []
    for i, item in enumerate(items, start=1):
        text.append(f"**{i}.** {format_card(item)}")
        buttons.append([InlineKeyboardButton(
            f"📥 {i}. {item.get('tmdb_title') or item.get('title', 'File')[:35]}",
            callback_data=f"file|{item['file_id']}"
        )])
    nav = result_keyboard(query, page, total)
    if nav:
        buttons.extend(nav.inline_keyboard)
    return await message.reply_text("\n\n".join(text), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("start") & filters.private)
async def start(_, message):
    await db.add_user(message.from_user.id)
    if not await subscribed(app, message.from_user.id):
        return await message.reply_text("🔒 Please join the required channel first, then try again.")
    await message.reply_text(
        "🎬 **Movies Magic Club 3.0**\n\n"
        "Send me a movie or series name and I will search the indexed Telegram files.\n\n"
        "Example: `Leo 2023`\n\n"
        "⚡ Fast • Simple • TMDB-powered",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(_, message):
    await message.reply_text("🔎 Send a movie/series name to search.\n\nAdmins: `/stats`", parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("stats") & filters.private)
async def stats(_, message):
    if message.from_user.id not in ADMINS:
        return
    files, users, movies = await db.stats()
    await message.reply_text(
        f"📊 **Stats**\n\n📁 Indexed files: `{files}`\n👤 Users: `{users}`\n🎬 TMDB movies: `{movies}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "stats", "cancel"]))
async def search_handler(_, message):
    if not await subscribed(app, message.from_user.id):
        return await message.reply_text("🔒 Please join the required channel first.")
    await db.add_user(message.from_user.id)
    await render_search(message, message.text)


@app.on_callback_query(filters.regex(r"^search\|"))
async def page_callback(_, query: CallbackQuery):
    _, page, search = query.data.split("|", 2)
    page = int(page)
    total, items = await build_results(search, page)
    text = [f"🔎 **Results for:** `{search}`", f"📚 Found: **{total}**", ""]
    buttons = []
    for i, item in enumerate(items, start=1):
        text.append(f"**{i}.** {format_card(item)}")
        buttons.append([InlineKeyboardButton(
            f"📥 {i}. {item.get('tmdb_title') or item.get('title', 'File')[:35]}",
            callback_data=f"file|{item['file_id']}"
        )])
    nav = result_keyboard(search, page, total)
    if nav:
        buttons.extend(nav.inline_keyboard)
    await query.message.edit_text("\n\n".join(text), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
    await query.answer()


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
    caption = format_card(item)
    await app.send_cached_media(query.from_user.id, file_id, caption=caption)


@app.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=10)
async def index_channel(_, message):
    if message.chat.id != CHANNEL_ID:
        return
    media = message.document or message.video or message.audio
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
    await db.add_file(data)


async def main():
    await db.setup()
    await app.start()
    me = await app.get_me()
    print(f"@{me.username} is running")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
