import asyncio
import logging

# Pyrogram's own documentation recommends using idle() for event-driven
# applications so the update dispatcher remains alive while the process runs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

from pyrogram import idle

from bot import app, db, verification, start_health_server, stop_health_server
from config import ADMINS


@app.on_raw_update()
async def raw_update_logger(client, update, users, chats):
    print(f"📡 RAW TELEGRAM UPDATE: {type(update).__name__}", flush=True)


async def main():
    health_runner = None
    try:
        print("🚀 Runner: starting MOVIES_MAGIC_CLUB-3.0", flush=True)

        print("🔧 Runner: initializing MongoDB...", flush=True)
        await db.setup()
        print("✅ Runner: MongoDB initialized", flush=True)

        print("🔧 Runner: initializing verification store...", flush=True)
        await verification.setup()
        print("✅ Runner: verification store initialized", flush=True)

        health_runner = await start_health_server()

        print("🔌 Runner: starting Pyrogram...", flush=True)
        await app.start()

        me = await app.get_me()
        print(
            f"✅ Runner: Telegram connected as @{me.username} (id={me.id})",
            flush=True,
        )
        print("✅ Runner: update dispatcher is active", flush=True)
        print("🟢 Runner: entering Pyrogram idle()", flush=True)

        # Keep the Pyrogram update loop alive using the official idle utility.
        await idle()

    except Exception as exc:
        print(
            f"❌ Runner startup/runtime failure: {type(exc).__name__}: {exc}",
            flush=True,
        )
        raise
    finally:
        try:
            if app.is_connected:
                await app.stop()
                print("🛑 Runner: Pyrogram stopped", flush=True)
        except Exception as exc:
            print(f"⚠️ Runner: Pyrogram stop failed: {exc}", flush=True)

        if health_runner:
            try:
                await stop_health_server(health_runner)
            except Exception as exc:
                print(f"⚠️ Runner: health server stop failed: {exc}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
