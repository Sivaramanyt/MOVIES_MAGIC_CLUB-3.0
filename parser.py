import re

QUALITY_RE = re.compile(r"(?<!\d)(2160p|4k|1080p|720p|480p|360p)(?!\d)", re.I)
# Digit lookarounds instead of \b: underscores are word characters, so \b
# never fires in names like Movie_2026_1080p.
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
LANGUAGE_RE = re.compile(r"\b(tamil|telugu|hindi|malayalam|kannada|english|bengali|marathi|punjabi|dual audio|multi audio)\b", re.I)
SEASON_RE = re.compile(r"\bS(\d{1,2})\b", re.I)
EPISODE_RE = re.compile(r"\bE(\d{1,3})\b", re.I)
SEPARATOR_RE = re.compile(r"[\s._\-–—()\[\]{}+:;,!'\"]+")
EXT_RE = re.compile(r"\.(mkv|mp4|avi|mov|webm|ts|m2ts|flv|wmv|mpg|mpeg|m4v|3gp)$", re.I)


def normalize_search_text(text: str) -> str:
    """Collapse every filename separator to a single space and casefold.

    Stored search text and user queries are both normalized with this, so a
    query like ``The Death of Robin Hood`` matches a filename such as
    ``The.Death.of.Robin.Hood.2026.1080p...`` regardless of separator style.
    """
    text = SEPARATOR_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def parse_media(name: str, caption: str = "") -> dict:
    raw = (name or caption or "").strip()
    text = EXT_RE.sub("", raw).strip() or raw
    quality = (QUALITY_RE.search(text).group(1).upper() if QUALITY_RE.search(text) else "")
    year_match = YEAR_RE.search(text)
    year = int(year_match.group(1)) if year_match else None
    languages = sorted({m.group(1).lower() for m in LANGUAGE_RE.finditer(text)})
    season = int(SEASON_RE.search(text).group(1)) if SEASON_RE.search(text) else None
    episode = int(EPISODE_RE.search(text).group(1)) if EPISODE_RE.search(text) else None

    title = text
    title = YEAR_RE.sub("", title)
    title = QUALITY_RE.sub("", title)
    title = LANGUAGE_RE.sub("", title)
    title = re.sub(r"\b(?:WEB[- .]?DL|WEBRip|BluRay|HDRip|HDTV|x264|x265|HEVC|AAC|DDP|AMZN|NF)\b", "", title, flags=re.I)
    title = re.sub(r"[._\[\](){}+]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -_")

    return {
        "title": title or text,
        "normalized_title": normalize_search_text(title or text),
        "year": year,
        "quality": quality,
        "languages": languages,
        "season": season,
        "episode": episode,
        "search_text": normalize_search_text(text),
    }
