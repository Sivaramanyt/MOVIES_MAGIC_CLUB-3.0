"""Backwards-compatible entrypoint.

All channel indexing, polling, catch-up, source-reference storage and
delivery now live natively in bot.py, so `python bot.py` and
`python channel_patch.py` behave identically. This module remains so that
existing Dockerfile/Procfile/Koyeb run commands keep working.
"""
import asyncio

import bot

if __name__ == "__main__":
    asyncio.run(bot.main())
