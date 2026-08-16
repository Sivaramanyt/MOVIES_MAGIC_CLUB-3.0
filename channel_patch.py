import asyncio

from telethon import events

import bot


def patched_build_file_data(message):
    async def _build():
        file = message.file
        name = (file.name if file else None) or message.raw_text or "Unknown"
        meta = bot.parse_media(name, message.raw_text or "")
        chat_id = int(message.chat_id) if message.chat_id is not None else bot.CHANNEL_ID
        message_id = int(message.id)
        # Telethon's media id is not a Bot API file_id. Keep a stable source reference.
        file_id = f"telethon:{chat_id}:{message_id}"
        return {
            **meta,
            "file_id": file_id,
            "file_unique_id": file_id,
            "file_name": name,
            "duplicate_name": bot.normalize_filename(name),
            "size": getattr(file, "size", 0) or 0,
            "mime_type": getattr(file, "mime_type", "") or "",
            "message_id": message_id,
            "channel_id": chat_id,
        }
    return _build()


async def patched_send_file_after_verification(user_id, file_id, caption_item):
    chat_id = caption_item.get("channel_id")
    message_id = caption_item.get("message_id")
    if chat_id is not None and message_id is not None:
        try:
            source = await bot.app.get_messages(int(chat_id), ids=int(message_id))
            if source and (source.document or source.video or source.audio):
                await bot.app.send_file(
                    user_id,
                    source.media,
                    caption=bot.file_card(caption_item),
                    parse_mode="md",
                )
                return
        except Exception as exc:
            print(f"⚠️ Source-message delivery failed for {chat_id}:{message_id}: {exc}")
    # Legacy fallback for older records that contain a directly usable file reference.
    await bot.app.send_file(user_id, file_id, caption=bot.file_card(caption_item), parse_mode="md")


async def broad_channel_update(event):
    message = event.message
    if event.chat_id != bot.CHANNEL_ID:
        return
    if not (message.document or message.video or message.audio):
        print(f"📡 Channel update received: chat_id={event.chat_id}, message_id={message.id}, no media")
        return
    print(
        f"📦 CHANNEL MEDIA RECEIVED: chat_id={event.chat_id}, "
        f"message_id={message.id}, name={getattr(message.file, 'name', None)!r}"
    )
    try:
        indexed = await bot.index_media_message(message)
        print(f"{'✅ INDEXED' if indexed else 'ℹ️ NOT INDEXED'} channel message {message.id}")
    except Exception as exc:
        print(f"❌ CHANNEL INDEX FAILED message={message.id}: {type(exc).__name__}: {exc}")


async def probe_and_catch_up():
    await asyncio.sleep(8)
    try:
        entity = await bot.app.get_entity(bot.CHANNEL_ID)
        print(f"🔎 Channel access OK: id={bot.CHANNEL_ID}, title={getattr(entity, 'title', None)!r}")
        latest = await bot.app.get_messages(bot.CHANNEL_ID, limit=20)
        media_count = 0
        indexed_count = 0
        for message in latest:
            if not (message.document or message.video or message.audio):
                continue
            media_count += 1
            try:
                if await bot.index_media_message(message):
                    indexed_count += 1
            except Exception as exc:
                print(f"❌ Catch-up indexing failed message={message.id}: {exc}")
        print(f"🔄 Channel catch-up complete: media={media_count}, newly_indexed={indexed_count}")
    except Exception as exc:
        print(f"❌ CHANNEL ACCESS/CATCH-UP FAILED: {type(exc).__name__}: {exc}")
        print("⚠️ Make sure BOT_TOKEN belongs to a bot that is an administrator in CHANNEL_ID.")


# Patch the Telegram-specific pieces before bot.main() starts.
bot.build_file_data = patched_build_file_data
bot.send_file_after_verification = patched_send_file_after_verification
bot.app.add_event_handler(broad_channel_update, events.NewMessage(incoming=True))

_original_main = bot.main


async def main():
    asyncio.create_task(probe_and_catch_up())
    await _original_main()


if __name__ == "__main__":
    asyncio.run(main())
