import os
from aiohttp import web

# Koyeb provides PORT automatically.
# 8000 is only the fallback for local testing.
PORT = int(os.getenv("PORT", "8000"))
HOST = "0.0.0.0"


async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "MOVIES_MAGIC_CLUB-3.0"
    })


async def root(request):
    return web.Response(
        text="MOVIES_MAGIC_CLUB-3.0 is running"
    )


async def start_health_server():
    app = web.Application()

    # Health check endpoint
    app.router.add_get("/health", health)

    # Root endpoint
    app.router.add_get("/", root)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )

    await site.start()

    print(f"✅ Health server running on {HOST}:{PORT}")
    print(f"✅ Health endpoint: http://0.0.0.0:{PORT}/health")

    return runner


async def stop_health_server(runner):
    if runner:
        await runner.cleanup()
        print("🛑 Health server stopped")
