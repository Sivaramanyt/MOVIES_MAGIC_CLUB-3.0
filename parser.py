import re

QUALITY_RE = re.compile(r"(?<!\d)(2160p|4k|1080p|720p|480p|360p)(?!\d)", re.I)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
LANGUAGE_RE = re.compile(r"\b(tamil|telugu|hindi|malayalam|kannada|english|bengali|marathi|punjabi|dual audio|multi audio)\b", re.I)
SEASON_RE = re.compile(r"\bS(\d{1,2})\b", re.I)
EPISODE_RE = re.compile(r"\bE(\d{1,3})\b", re.I)


def parse_media(name: str, caption: str = "") -> dict:
    text = (name or caption or "").strip()
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
    title = re.sub(r"[._\[\](){}]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -_")

    return {
        "title": title or text,
        "year": year,
        "quality": quality,
        "languages": languages,
        "season": season,
        "episode": episode,
        "search_text": text,
    }
