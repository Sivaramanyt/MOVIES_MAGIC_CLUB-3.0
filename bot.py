import asyncio
import logging
import math
import re
import time

from telethon import TelegramClient, events, Button
from telethon.errors import UserNotParticipantError

from config import (
    API_ID, API_HASH, BOT_TOKEN, MONGO_URI, DATABASE_NAME, CHANNEL_ID,
    ADMINS, FORCE_SUB_CHANNEL, RESULTS_PER_PAGE, TMDB_API_KEY,
    AUTO_DELETE_DUPLICATES, DUPLICATE_REQUIRE_ALL_FIELDS, REINDEX_DRY_RUN,
)
from database import Database
from parser import parse_media
from tmdb import TMDBClient
from shortlink_verification import VerificationStore, maybe_create_verification
from health_server import start_health_server, stop_health_server

logging.basicConfig(
    format="[%(levelname)s %(asctime)s] %(name)s: %(message)s",
    level=logging.INFO,
)

app = TelegramClient(
    "movies_magic_club_telethon",
    API_ID,
    API_HASH,
    auto_reconnect=True,
    receive_updates=True,
)
db = Database(MONGO_URI, DATABASE_NAME)
tmdb = TMDBClient(TMDB_API_KEY)
verification = VerificationStore(db)


def page_count(total: int) -> int:
    return max(1, math.ceil(total / RESULTS_PER_PAGE))


def normalize_filename(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def safe_duplicate_policy(data: dict) -> bool:
    if not DUPLICATE_REQUIRE_ALL_FIELDS:
        return True
    return bool(normalize_filename(data.get("file_name", ""))) and int(data.get("size") or 0) > 0 and bool((data.get("quality") or "").strip())


def poster_keyboard(query: str, page: int, total: int, group_key: str):
    pages = page_count(total)
    rows = [[Button.inline("🎬 Select Movie", data=f"movie|{group_key}")]]
    nav = []
    if page > 0:
        nav.append(Button.inline("⬅️ Previous", data=f"search|{page-1}|{query[:40]}"))
    nav.append(Button.inline(f"{page + 1}/{pages}", data="noop"))
    if page + 1 < pages:
        nav.append(Button.inline("Next ➡️", data=f"search|{page+1}|{query[:40]}"))
    rows.append(nav)
    return rows


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


async def build_file_data(message):
    media = message.document or message.video or message.audio
    file = message.file
    name = (file.name if file else None) or message.raw_text or "Unknown"
    meta = parse_media(name, message.raw_text or "")
    file_id = file.id if file else None
    unique_id = str(file_id) if file_id is not None else str(message.id)
    return {
        **meta,
        "file_id": file_id,
        "file_unique_id": unique_id,
        "file_name": name,
        "duplicate_name": normalize_filename(name),
        "size": getattr(file, "size", 0) or 0,
        "mime_type": getattr(file, "mime_type", "") or "",
        "message_id": message.id,
        "channel_id": message.chat_id,
    }


async def delete_duplicate_channel_message(message, duplicate: dict, reason="duplicate"):
    try:
        await message.delete()
        print(f"Deleted {reason}: message={message.id}, kept_file={duplicate.get('file_id')}")
        return True
    except Exception as exc:
        print(f"Unable to delete duplicate channel message {message.id}: {exc}")
        return False


async def find_safe_duplicate(data: dict):
    if not safe_duplicate_policy(data):
        return None
    return await db.find_duplicate_file(data["file_name"], int(data["size"]), data.get("quality"), data.get("file_unique_id"))


async def index_media_message(message) -> bool:
    if not (message.document or message.video or message.audio):
        return False
    data = await build_file_data(message)
    if not data.get("file_id"):
        return False
    duplicate = await find_safe_duplicate(data)
    if duplicate and AUTO_DELETE_DUPLICATES:
        await delete_duplicate_channel_message(message, duplicate)
        return False
    if duplicate:
        print(f"Duplicate detected but deletion disabled: message={message.id}")
        return False
    added = await db.add_file(data)
    if not added:
        return False
    try:
        enriched = await tmdb.enrich(data.get("title", ""), data.get("year"))
        if enriched:
            await db.update_file_tmdb(data["file_id"], enriched)
            await db.save_movie(enriched)
    except Exception as exc:
        print(f"TMDB enrichment failed for {data['file_name']}: {exc}")
    return True


async def reindex_channel(progress_callback=None, delete_duplicates=False) -> dict:
    stats = {"scanned": 0, "indexed": 0, "enriched": 0, "failed": 0, "duplicates": 0, "last_message_id": 0}
    async for history_message in app.iter_messages(CHANNEL_ID):
        if not (history_message.document or history_message.video or history_message.audio):
            continue
        stats["scanned"] += 1
        stats["last_message_id"] = history_message.id
        try:
            data = await build_file_data(history_message)
            existing = await db.files.find_one({"file_unique_id": data.get("file_unique_id")})
            duplicate = await find_safe_duplicate(data)
            is_other_record = duplicate and (not existing or duplicate.get("file_id") != existing.get("file_id"))
            if is_other_record:
                if delete_duplicates:
                    if await delete_duplicate_channel_message(history_message, duplicate, "reindex duplicate"):
                        stats["duplicates"] += 1
                    if existing:
                        await db.files.delete_one({"file_id": existing["file_id"]})
                    continue
                stats["duplicates"] += 1
                if progress_callback:
                    await progress_callback(dict(stats))
                continue
            if existing:
                await db.update_file(existing["file_id"], data)
                file_id = existing["file_id"]
            else:
                if not await db.add_file(data):
                    continue
                file_id = data["file_id"]
            stats["indexed"] += 1
            metadata = await tmdb.enrich(data.get("title", ""), data.get("year"))
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


def format_reindex_progress(stats: dict, started_at: float, running: bool = True, dry_run: bool = True) -> str:
    elapsed = max(time.monotonic() - started_at, 0.1)
    mode = "🛡️ DRY RUN — no messages will be deleted" if dry_run else "🗑️ DELETE MODE — duplicates are being removed"
    state = "🔄 **Reindexing…**" if running else "✅ **Reindex complete**"
    return (f"{state}\n{mode}\n\n📦 Scanned: `{stats['scanned']}`\n🗂 Indexed: `{stats['indexed']}`\n🧩 Enriched: `{stats['enriched']}`\n"
            f"♻️ Duplicate candidates: `{stats.get('duplicates', 0)}`\n⚠️ Failed: `{stats['failed']}`\n⚡ Speed: `{stats['scanned']/elapsed:.1f} files/s`\n🆔 Last message: `{stats.get('last_message_id', 0)}`")


async def subscribed(user_id: int) -> bool:
    if not FORCE_SUB_CHANNEL:
        return True
    try:
        await app.get_permissions(FORCE_SUB_CHANNEL, user_id)
        return True
    except UserNotParticipantError:
        return False
    except Exception as exc:
        print(f"Force-sub check failed: {exc}")
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
    group_key = f"tmdb:{item['tmdb_id']}" if item.get("tmdb_id") else f"fallback:{item.get('normalized_title', item.get('title', ''))}:{item.get('year') or 0}"
    markup = poster_keyboard(query, page, total, group_key)
    poster = item.get("poster_url")
    if poster:
        try:
            return await message.reply(file=poster, message=movie_card(item), buttons=markup, parse_mode="md")
        except Exception as exc:
            print(f"Poster send failed, falling back to text: {exc}")
    return await message.reply(movie_card(item), buttons=markup, parse_mode="md")


async def render_search(message, query: str, page: int = 0):
    query = query.strip()
    if not query:
        return await message.reply("🔎 Send a movie or series name.")
    total, items = await build_results(query, page)
    if not total:
        return await message.reply(f"❌ No results found for **{query}**.", parse_mode="md")
    for item in items:
        await send_movie_card(message, item, query, page, total)


async def show_movie_options(message, selected: dict):
    files = await db.get_movie_files(selected) or [selected]
    title = selected.get("tmdb_title") or selected.get("title") or "Movie"
    year = selected.get("tmdb_year") or selected.get("year")
    languages = sorted({x for f in files for x in (f.get("languages") or [])})
    lines = [f"🎬 **{title}**"]
    if year:
        lines.append(f"📅 {year}")
    lines.append("\n🌐 **Select Language**")
    rows = []
    for i in range(0, len(languages), 2):
        rows.append([Button.inline(lang.title(), data=f"lang|{selected.get('tmdb_id', '')}|{lang}") for lang in languages[i:i+2]])
    if not rows:
        rows = [[Button.inline("🌐 All Languages", data=f"lang|{selected.get('tmdb_id', '')}|all")]]
    return await message.reply("\n".join(lines), buttons=rows, parse_mode="md")


async def find_movie_files(tmdb_id, language=None, fallback_key=None):
    if tmdb_id:
        files = await db.files.find({"tmdb_id": int(tmdb_id)}).sort([("quality", 1), ("_id", -1)]).to_list(length=500)
    elif fallback_key:
        title, year = fallback_key.rsplit(":", 1)
        files = await db.files.find({"normalized_title": title, "year": int(year)}).sort([("quality", 1), ("_id", -1)]).to_list(length=500)
    else:
        files = []
    if language and language != "all":
        files = [f for f in files if language.lower() in [x.lower() for x in (f.get("languages") or [])]]
    return files


async def send_file_after_verification(user_id: int, file_id: str, caption_item: dict):
    await app.send_file(user_id, file_id, caption=file_card(caption_item), parse_mode="md")


@app.on(events.NewMessage(pattern=r"^/start(?:\s+.*)?$", incoming=True, func=lambda e: e.is_private))
async def start(event):
    user_id = event.sender_id
    print(f"📥 /start received from user_id={user_id}")
    await db.add_user(user_id)
    args = event.raw_text.split(maxsplit=1)
    if len(args) == 2 and args[1].startswith("verify_"):
        token = args[1][7:]
        doc = await verification.consume(token, user_id)
        if not doc:
            return await event.reply("❌ Verification link is invalid or expired. Please request a new verification link.")
        file_id = doc.get("file_id")
        item = await db.files.find_one({"file_id": file_id}) if file_id else None
        if not item:
            return await event.reply("⚠️ Verification succeeded, but the requested file is no longer available.")
        await event.reply("✅ **Verification successful!**\nSending your file…", parse_mode="md")
        return await send_file_after_verification(user_id, file_id, item)
    if not await subscribed(user_id):
        return await event.reply("🔒 Please join the required channel first, then try again.")
    await event.reply("🎬 **Movies Magic Club 3.0**\n\nSend a movie or series name to search.", parse_mode="md")


@app.on(events.NewMessage(pattern=r"^/ping(?:@\w+)?$", incoming=True, func=lambda e: e.is_private))
async def ping_cmd(event):
    print(f"📥 /ping received from user_id={event.sender_id}")
    await event.reply("🏓 Pong! Bot update handling is working.")


@app.on(events.NewMessage(pattern=r"^/help(?:@\w+)?$", incoming=True, func=lambda e: e.is_private))
async def help_cmd(event):
    await event.reply("🔎 Send a movie/series name to search.\n\nAdmins: `/stats`\n🔄 Admin: `/reindex`", parse_mode="md")


@app.on(events.NewMessage(pattern=r"^/stats(?:@\w+)?$", incoming=True, func=lambda e: e.is_private))
async def stats(event):
    if event.sender_id not in ADMINS:
        return
    files, users, movies = await db.stats()
    await event.reply(f"📊 **Stats**\n\n📁 Files: `{files}`\n👤 Users: `{users}`\n🎬 TMDB movies: `{movies}`", parse_mode="md")


@app.on(events.NewMessage(pattern=r"^/reindex(?:\s+.*)?$", incoming=True, func=lambda e: e.is_private))
async def reindex_cmd(event):
    if event.sender_id not in ADMINS:
        return await event.reply("⛔ You are not authorized to use this command.")
    args = event.raw_text.split()[1:]
    delete_requested = "--delete" in args
    delete_mode = delete_requested and not REINDEX_DRY_RUN
    if delete_requested and REINDEX_DRY_RUN:
        return await event.reply("🛡️ REINDEX_DRY_RUN is enabled, so deletion is blocked. Set `REINDEX_DRY_RUN=false` before using `/reindex --delete`.", parse_mode="md")
    started_at = time.monotonic()
    status = await event.reply("🔄 **Reindexing…**\n🛡️ DRY RUN — no messages will be deleted\n\n📦 Scanned: `0`\n🗂 Indexed: `0`\n🧩 Enriched: `0`\n♻️ Duplicate candidates: `0`\n⚠️ Failed: `0`", parse_mode="md")
    last_update = 0.0

    async def progress(stats):
        nonlocal last_update
        now = time.monotonic()
        if stats["scanned"] != 1 and now - last_update < 3:
            return
        last_update = now
        try:
            await status.edit(format_reindex_progress(stats, started_at, True, not delete_mode), parse_mode="md")
        except Exception as exc:
            print(f"Reindex progress update failed: {exc}")

    try:
        final = await reindex_channel(progress, delete_duplicates=delete_mode)
        await status.edit(format_reindex_progress(final, started_at, False, not delete_mode), parse_mode="md")
    except Exception as exc:
        await status.edit(f"❌ **Reindex failed**\n`{exc}`", parse_mode="md")


@app.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and bool(e.raw_text) and not e.raw_text.startswith("/")))
async def search_handler(event):
    if not await subscribed(event.sender_id):
        return await event.reply("🔒 Please join the required channel first.")
    await db.add_user(event.sender_id)
    print(f"📥 Search received from user_id={event.sender_id}: {event.raw_text[:80]!r}")
    await render_search(event.message, event.raw_text)


@app.on(events.CallbackQuery(pattern=re.compile(rb"^search\|")))
async def page_callback(event):
    data = event.data.decode("utf-8", errors="replace")
    _, page, search = data.split("|", 2)
    page = max(0, int(page))
    total = await db.count_grouped_movies(search)
    page = min(page, page_count(total) - 1)
    await event.answer()
    message = await event.get_message()
    await render_search(message, search, page)
    try:
        await message.delete()
    except Exception:
        pass


@app.on(events.CallbackQuery(pattern=re.compile(rb"^movie\|")))
async def movie_callback(event):
    data = event.data.decode("utf-8", errors="replace")
    group_key = data.split("|", 1)[1]
    selected = None
    if group_key.startswith("tmdb:"):
        try:
            selected = await db.files.find_one({"tmdb_id": int(group_key.split(":", 1)[1])})
        except ValueError:
            pass
    elif group_key.startswith("fallback:"):
        value = group_key.split(":", 1)[1]
        title, year = value.rsplit(":", 1)
        selected = await db.files.find_one({"normalized_title": title, "year": int(year)})
    if not selected:
        return await event.answer("Movie is no longer indexed.", alert=True)
    await event.answer()
    message = await event.get_message()
    await show_movie_options(message, selected)


@app.on(events.CallbackQuery(pattern=re.compile(rb"^lang\|")))
async def language_callback(event):
    data = event.data.decode("utf-8", errors="replace")
    _, tmdb_id, language = data.split("|", 2)
    files = await find_movie_files(tmdb_id, language)
    if not files:
        return await event.answer("No files found for this language.", alert=True)
    rows = [[Button.inline(f"🎞 {language.title()} • {(item.get('quality') or 'Quality unknown')}"[:60], data=f"file|{item['file_id']}")] for item in files]
    await event.answer()
    await event.edit("🎞 **Select Quality / File**", buttons=rows, parse_mode="md")


@app.on(events.CallbackQuery(pattern=re.compile(rb"^file\|")))
async def file_callback(event):
    data = event.data.decode("utf-8", errors="replace")
    file_id = data.split("|", 1)[1]
    item = await db.files.find_one({"file_id": file_id})
    if not item:
        return await event.answer("File no longer exists in the index.", alert=True)
    await event.answer()
    try:
        shortlink = await maybe_create_verification(verification, event.sender_id, file_id)
    except Exception as exc:
        print(f"Verification creation failed: {exc}")
        return await event.reply("⚠️ Verification service is temporarily unavailable. Please try again later.")
    if shortlink:
        keyboard = [[Button.url("🔐 Verify & Get File", shortlink)]]
        return await event.reply("🔒 **Verification required**\n\nComplete the shortlink verification, then Telegram will automatically send your requested file.", buttons=keyboard, parse_mode="md")
    await send_file_after_verification(event.sender_id, file_id, item)


@app.on(events.CallbackQuery(data=b"noop"))
async def noop(event):
    await event.answer()


@app.on(events.NewMessage(chats=CHANNEL_ID, incoming=True, func=lambda e: bool(e.message.document or e.message.video or e.message.audio)))
async def index_channel(event):
    message = event.message
    print(f"📦 Channel media update received: chat_id={message.chat_id}, message_id={message.id}")
    await index_media_message(message)


@app.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def diagnostic_private_update(event):
    try:
        print(f"📡 Telethon private update received: user_id={event.sender_id}, message_id={event.message.id}, text={event.raw_text[:80]!r}")
    except Exception as exc:
        print(f"⚠️ Diagnostic update logger failed: {exc}")


@app.on(events.CallbackQuery())
async def diagnostic_callback_update(event):
    try:
        print(f"📡 Telethon callback received: user_id={event.sender_id}, data={event.data!r}")
    except Exception as exc:
        print(f"⚠️ Diagnostic callback logger failed: {exc}")


@app.on(events.NewMessage(chats=CHANNEL_ID, incoming=True))
async def diagnostic_channel_update(event):
    try:
        print(f"📡 Telethon channel update received: chat_id={event.message.chat_id}, message_id={event.message.id}")
    except Exception as exc:
        print(f"⚠️ Diagnostic channel logger failed: {exc}")


async def main():
    health_runner = None
    try:
        print("🚀 Starting MOVIES_MAGIC_CLUB-3.0 with Telethon...")
        print("🔧 Initializing MongoDB...")
        await db.setup()
        print("✅ MongoDB initialized")

        print("🔧 Initializing verification store...")
        await verification.setup()
        print("✅ Verification store initialized")

        health_runner = await start_health_server()

        print("🔌 Connecting Telethon to Telegram...")
        await app.start(bot_token=BOT_TOKEN)
        me = await app.get_me()
        print(f"✅ Telegram connection established: @{me.username} (id={me.id})")
        print(f"✅ Telethon handlers registered: {len(app.list_event_handlers())}")
        print("🟢 Bot is ready to receive Telegram updates")

        await app.run_until_disconnected()

    except Exception as exc:
        print(f"❌ Bot startup/runtime failed: {type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            if app.is_connected():
                await app.disconnect()
        except Exception as exc:
            print(f"⚠️ Bot disconnect failed: {exc}")
        if health_runner:
            try:
                await stop_health_server(health_runner)
            except Exception as exc:
                print(f"⚠️ Health server stop failed: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
