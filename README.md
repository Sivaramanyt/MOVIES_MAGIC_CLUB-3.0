# Movies Magic Club 3.0 — Movie Filter Bot

A clean Telegram movie/series auto-filter bot built around a Telegram media channel, MongoDB, and TMDB metadata. It indexes media you are authorized to distribute, then lets users search by title and receive matching Telegram files.

## Features

- Automatic indexing of documents/videos/audio from a configured Telegram channel
- MongoDB-backed file catalog
- Case-insensitive title search
- Language and quality extraction from filenames
- TMDB title matching
- TMDB poster URL, year, rating, genres, overview, and movie/TV type
- Rich result cards showing title, year, rating, genres, language, and quality
- Pagination for large result sets
- Optional force-subscription check
- Admin statistics
- Environment-variable configuration
- Docker and Koyeb/Render-friendly deployment

## TMDB setup

1. Create a TMDB account and request an API key from the TMDB developer settings.
2. Put the API key in your deployment environment as `TMDB_API_KEY`.
3. Optionally set `TMDB_LANGUAGE=en-US` (or another TMDB-supported language code).
4. If no TMDB key is configured, the bot still works using filename metadata; posters and TMDB fields are simply omitted.

The bot caches matched TMDB metadata in MongoDB after the first successful match, reducing repeated TMDB requests.

## Architecture

```text
Telegram source channel
        |
        v
    Indexer handler
        |
        v
     MongoDB <------ TMDB API
        |
        v
  Search / callbacks
        |
        v
      Users
```

The bot stores Telegram `file_id` references and metadata; it does not download or mirror media to the application server.

## Setup

1. Create a Telegram bot with BotFather.
2. Create a MongoDB database and obtain its connection URI.
3. Add the bot as an administrator to your source channel so it can receive/index channel posts.
4. Copy `.env.example` to `.env` and fill in the values.
5. Install dependencies:

```bash
pip install -r requirements.txt
```

6. Start:

```bash
python bot.py
```

## Environment variables

`API_ID`, `API_HASH`, `BOT_TOKEN`, `MONGO_URI`, and `CHANNEL_ID` are required. `TMDB_API_KEY` enables poster and metadata enrichment. See `.env.example` for the complete list.

## Commands

- `/start` — welcome/search instructions
- `/help` — help
- `/stats` — admin-only statistics

Send a movie or series name in private chat to search.

## Indexing

Post or forward media to the configured source channel. The bot parses the filename/caption for title, year, language, quality, season, and episode where possible, then stores the Telegram file ID in MongoDB. Duplicate Telegram file IDs are ignored.

## Copyright / usage

Use this software only for media and metadata that you are legally authorized to store, index, and distribute. This project does not provide a source of copyrighted movies.

TMDB data and images are provided under TMDB's applicable terms. This project is not endorsed or certified by TMDB.
