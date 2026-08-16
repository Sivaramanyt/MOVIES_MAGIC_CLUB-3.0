# Movies Magic Club 3.0 — Movie Filter Bot

A clean Telegram movie/series auto-filter bot built around a Telegram media channel and MongoDB. It indexes media you are authorized to distribute, then lets users search by title and receive matching Telegram files.

## Features

- Automatic indexing of documents/videos from a configured Telegram channel
- MongoDB-backed file catalog
- Case-insensitive title search
- Language and quality extraction from filenames
- Pagination for large result sets
- Optional force-subscription check
- Admin statistics
- Environment-variable configuration
- Docker and Koyeb/Render-friendly deployment

## Architecture

```text
Telegram source channel -> Indexer -> MongoDB -> Search -> Telegram users
```

The bot stores Telegram `file_id` references and metadata; it does not download or mirror media to the application server.

## Setup

1. Create a Telegram bot with BotFather.
2. Create a MongoDB database and obtain its connection URI.
3. Add the bot as an administrator to your source channel.
4. Copy `.env.example` to `.env` and fill in the values.
5. Install dependencies: `pip install -r requirements.txt`
6. Start: `python bot.py`

## Usage

Send a movie or series name in private chat to search. Post authorized media to the configured source channel to index it.

## Copyright / usage

Use this software only for media and metadata that you are legally authorized to store, index, and distribute. This project does not provide a source of copyrighted movies.
