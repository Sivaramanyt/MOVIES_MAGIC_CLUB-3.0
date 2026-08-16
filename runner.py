import asyncio
import traceback

from bot import app, db, verification, start_health_server


async def main():
    print("[BOOT] Starting database setup...")
    await db.setup()
    print("[BOOT] Starting verification store...")
    await verification.setup()

    print("[BOOT] Starting Pyrogram...")
    await app.start()

    try:
        me = await app.get_me()
        print(f"[BOOT] Telegram connected successfully as @{me.username} (id={me.id})")

        # Only expose the Koyeb health endpoint after Telegram is actually connected.
        await start_health_server()
        print("[BOOT] Movies Magic Club is READY and listening for Telegram updates.")

        # Keep the asyncio event loop alive so Pyrogram can process updates.
        await asyncio.Event().wait()
    except Exception:
        print("[BOOT] Fatal runtime error:")
        traceback.print_exc()
        raise
    finally:
        if app.is_connected:
            print("[BOOT] Stopping Pyrogram...")
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
