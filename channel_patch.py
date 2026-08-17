import asyncio
from telethon import events
import bot

POLL_SECONDS = 30
POLL_LIMIT = 100


def _channel_variants(cid: int) -> set:
    """Every integer form that can refer to the storage channel.

    Telethon reports channel chat_id only in marked ``-100<id>`` form, while
    configurations may hold the bare id. Accept all equivalent forms.
    """
    cid = int(cid)
    text = str(cid)
    bare = int(text[4:]) if text.startswith("-100") else abs(cid)
    return {cid, bare, -bare, int(f"-100{bare}")}


CHANNEL_VARIANTS = _channel_variants(bot.CHANNEL_ID)


def is_legacy_record(doc: dict) -> bool:
    """Records indexed before the Telethon source-reference scheme have no
    Telegram channel source, so they are neither deliverable nor updatable."""
    return not (doc.get("channel_id") and doc.get("message_id"))


def patched_build_file_data(message):
    async def _build():
        file = message.file
        name = (file.name if file else None) or message.raw_text or "Unknown"
        meta = bot.parse_media(name, message.raw_text or "")
        chat_id = int(message.chat_id) if message.chat_id is not None else bot.CHANNEL_ID
        message_id = int(message.id)
        source_ref = f"telethon:{chat_id}:{message_id}"
        return {**meta, "file_id": source_ref, "file_unique_id": source_ref,
                "file_name": name, "duplicate_name": bot.normalize_filename(name),
                "size": getattr(file, "size", 0) or 0,
                "mime_type": getattr(file, "mime_type", "") or "",
                "message_id": message_id, "channel_id": chat_id}
    return _build()


async def patched_send_file_after_verification(user_id, file_id, caption_item):
    chat_id = caption_item.get("channel_id")
    message_id = caption_item.get("message_id")
    if chat_id is not None and message_id is not None:
        try:
            source = await bot.app.get_messages(int(chat_id), ids=int(message_id))
            if source and (source.document or source.video or source.audio):
                print(f"📤 Delivering source {chat_id}:{message_id} to user {user_id}")
                await bot.app.send_file(user_id, source.media, caption=bot.file_card(caption_item), parse_mode="md")
                print(f"✅ File delivered from {chat_id}:{message_id}")
                return
            print(f"⚠️ Source {chat_id}:{message_id} has no supported media")
        except Exception as exc:
            print(f"❌ Source delivery failed {chat_id}:{message_id}: {type(exc).__name__}: {exc}")
    await bot.app.send_file(user_id, file_id, caption=bot.file_card(caption_item), parse_mode="md")


async def enrich_record(file_id, data):
    try:
        metadata = await bot.tmdb.enrich(data.get("title", ""), data.get("year"))
        if metadata:
            await bot.db.update_file_tmdb(file_id, metadata)
            await bot.db.save_movie(metadata)
            print(f"🎬 TMDB enriched: message={data.get('message_id')}, tmdb_id={metadata.get('tmdb_id')}")
        else:
            print(f"ℹ️ TMDB no match: message={data.get('message_id')}, title={data.get('title')!r}")
    except Exception as exc:
        print(f"⚠️ TMDB failed: message={data.get('message_id')}: {type(exc).__name__}: {exc}")


async def index_one(message, source="event"):
    if not (message.document or message.video or message.audio):
        return False
    try:
        data = await bot.build_file_data(message)
        existing = await bot.db.files.find_one({"file_unique_id": data["file_unique_id"]})
        if existing:
            await bot.db.update_file(existing["file_id"], {
                "file_name": data["file_name"], "message_id": data["message_id"],
                "channel_id": data["channel_id"], "size": data["size"],
                "mime_type": data["mime_type"], "search_text": data["search_text"],
                "normalized_title": data.get("normalized_title"),
                "title": data["title"], "year": data["year"],
                "season": data.get("season"), "episode": data.get("episode"),
                "quality": data["quality"], "languages": data["languages"]})
            return False
        duplicate = await bot.find_safe_duplicate(data)
        if duplicate:
            if is_legacy_record(duplicate):
                # Pre-Telethon record: adopt this channel message so the file
                # becomes searchable and deliverable instead of skipping it.
                await bot.db.files.update_one({"file_id": duplicate["file_id"]}, {"$set": data})
                print(f"🔁 Migrated legacy record: message={message.id}, name={data.get('file_name')!r}")
                await enrich_record(data["file_id"], data)
                return True
            print(f"♻️ Duplicate skipped: message={message.id}, existing={duplicate.get('file_id')}")
            return False
        if not await bot.db.add_file(data):
            return False
        print(f"🗂 Indexed: message={message.id}, name={data.get('file_name')!r}, title={data.get('title')!r}, search={data.get('search_text')!r}")
        await enrich_record(data["file_id"], data)
        return True
    except Exception as exc:
        print(f"❌ Index failed ({source}) message={getattr(message, 'id', '?')}: {type(exc).__name__}: {exc}")
        return False


async def broad_channel_update(event):
    if event.chat_id not in CHANNEL_VARIANTS:
        return
    message = event.message
    print(f"📡 Channel update: chat_id={event.chat_id}, message_id={message.id}, media={bool(message.document or message.video or message.audio)}")
    if await index_one(message, "event"):
        print(f"✅ New channel file indexed: message={message.id}")


async def scan_recent_messages(reason="poll", limit=POLL_LIMIT):
    try:
        messages = await bot.app.get_messages(bot.CHANNEL_ID, limit=limit)
        media = indexed = 0
        for message in messages:
            if not (message.document or message.video or message.audio):
                continue
            media += 1
            if await index_one(message, reason):
                indexed += 1
        print(f"🔄 Channel scan ({reason}): checked={len(messages)}, media={media}, newly_indexed={indexed}")
        return indexed
    except Exception as exc:
        print(f"❌ Channel scan failed ({reason}): {type(exc).__name__}: {exc}")
        return 0


async def wait_ready(timeout=120):
    """Wait until bot.main() has connected and authorized the Telethon client."""
    for _ in range(max(1, timeout // 2)):
        try:
            if bot.app.is_connected() and await bot.app.get_me():
                return True
        except Exception:
            pass
        await asyncio.sleep(2)
    return False


async def channel_poll_loop():
    await wait_ready()
    await asyncio.sleep(5)
    while True:
        await scan_recent_messages("poll", POLL_LIMIT)
        await asyncio.sleep(POLL_SECONDS)


async def probe_and_catch_up():
    if not await wait_ready():
        print("⚠️ Telethon not ready; startup catch-up skipped (polling will still run)")
        return
    try:
        entity = await bot.app.get_entity(bot.CHANNEL_ID)
        print(f"🔎 Channel access OK: id={bot.CHANNEL_ID}, title={getattr(entity, 'title', None)!r}")
        await scan_recent_messages("startup", 100)
    except Exception as exc:
        print(f"❌ CHANNEL ACCESS/CATCH-UP FAILED: {type(exc).__name__}: {exc}")


bot.build_file_data = patched_build_file_data
bot.send_file_after_verification = patched_send_file_after_verification
bot.app.add_event_handler(broad_channel_update, events.NewMessage(incoming=True))
_original_main = bot.main


async def main():
    asyncio.create_task(probe_and_catch_up())
    asyncio.create_task(channel_poll_loop())
    await _original_main()


if __name__ == "__main__":
    asyncio.run(main())
