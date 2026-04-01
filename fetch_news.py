"""
fetch_news.py — daily via GitHub Action
Queries NewsAPI -> filters through Claude -> appends to data.json
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

QUERIES = {
    "pl": [
        "Premier League streaming rights",
        "Premier League USA viewers",
        "Premier League international broadcast",
    ],
    "ipl": [
        "IPL broadcast rights international",
        "IPL cricket global expansion",
        "cricket T20 media rights",
    ],
    "nfl": [
        "NFL Germany Europe expansion",
        "NFL international games rights",
        "NFL Game Pass Europe",
        "NFL international broadcast deal",
    ],
    "nba": [
        "NBA international expansion",
        "NBA Europe basketball league FIBA",
        "NBA Africa China broadcast",
    ],
    "laliga": [
        "La Liga international rights",
        "La Liga India expansion",
        "Spanish football global broadcast",
    ],
    "japac": [
        "Ohtani global brand ambassador",
        "World Baseball Classic broadcast rights",
        "Asian sports streaming growth",
        "Ohtani sponsorship deal international",
        "Japanese baseball global audience",
        "sumo international streaming",
    ],
    "tennis": [
        "ATP WTA international broadcast rights",
        "tennis Grand Slam streaming deal",
        "tennis Saudi Arabia expansion",
    ],
    "f1": [
        "Formula 1 new race international",
        "F1 Las Vegas Miami Saudi broadcast",
        "Formula 1 streaming rights global",
    ],
}

LEAGUE_CONTEXT = {
    "pl": "the Premier League's expansion into the US and other international markets — streaming deals, broadcast rights, pre-season tours, audience growth outside England",
    "ipl": "the IPL's expansion beyond India — broadcast deals in the UK, Middle East, USA, the T20 format as a global export, cricket's push into new markets",
    "nfl": "the NFL's push into Europe and beyond — the DAZN deal, Game Pass, regular-season games in Munich/Frankfurt/London/other international cities, rights packaging",
    "nba": "the NBA's global expansion — new European league with FIBA, Amazon broadcast talks, Basketball Without Borders, China, international revenue",
    "laliga": "La Liga's international expansion — offices in India, broadcast deals in the US and Middle East, star-driven global strategy",
    "japac": "Japan and APAC sports as global cultural exports. Relevant: Ohtani as global brand ambassador, World Baseball Classic as international rights product, Asian sports streaming, commercial globalization of Japanese/Korean/APAC athletes. REJECT: domestic MLB game recaps, domestic Japanese league scores, merchandise listings, fantasy baseball.",
    "tennis": "Tennis expanding into new markets — ATP/WTA events in Saudi Arabia, China, streaming rights fragmentation, Grand Slam broadcast deals",
    "f1": "Formula 1's global expansion — new races in Las Vegas, Miami, Saudi Arabia, Netflix/Drive to Survive effect, Liberty Media strategy, streaming rights",
}

DATA_FILE = "data.json"


def fetch_newsapi(query, days_back=14):
    """Fetch articles from NewsAPI with rate limiting."""
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    params = urlencode({
        "q": query,
        "from": from_date,
        "sortBy": "relevancy",
        "language": "en",
        "pageSize": 10,
        "apiKey": NEWSAPI_KEY,
    })
    url = f"https://newsapi.org/v2/everything?{params}"
    req = Request(url, headers={"User-Agent": "SportsSupplyChain/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("articles", [])
    except HTTPError as e:
        print(f"    NewsAPI error ({e.code}): {query}", file=sys.stderr)
        return []


def filter_with_claude(league, articles):
    """Send raw articles to Claude for relevance scoring and summary."""
    if not articles:
        return []

    context = LEAGUE_CONTEXT.get(league, "")
    block = "\n\n".join([
        f"[{i+1}] {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nURL: {a.get('url','')}\nSnippet: {a.get('description','N/A')}"
        for i, a in enumerate(articles)
    ])

    prompt = f"""You are an editor at The Current, a B2B publication for senior marketers and media agencies.

Curating a feed about: {context}

STRICT FILTERING.

REJECT:
- Domestic results, scores, transfers, match previews, race results
- Domestic-only business (salary caps, coaching, relegation)
- Tangential mentions without international expansion focus
- General news aggregator junk
- Clickbait, betting, fantasy, merchandise listings
- Removed/unavailable articles

KEEP articles about:
- International broadcast or streaming rights deals
- Leagues playing events in new/expanding markets
- Audience growth in export markets
- Global brand/sponsor partnerships demonstrating international reach
- Athletes as cross-cultural brand ambassadors
- Strategic moves to enter new geographies

Score 1-10. Return ONLY score 8+.

JSON array format:
- "title": cleaned title with proper capitalization and punctuation
- "source": publication name
- "url": URL
- "summary": max 120 chars, for a media buyer, sharp and specific
- "score": 8-10

Valid JSON only. No markdown, no backticks, no preamble. Nothing qualifies? Return []

Articles:
{block}"""

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
            text = "".join(
                b["text"] for b in data.get("content", []) if b.get("type") == "text"
            ).strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            return result if isinstance(result, list) else []
    except Exception as e:
        print(f"    Claude error: {e}", file=sys.stderr)
        return []


def main():
    if not NEWSAPI_KEY:
        print("ERROR: NEWSAPI_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    else:
        data = {"last_updated": None, "articles": {k: [] for k in QUERIES}}

    total = 0

    for league, queries in QUERIES.items():
        print(f"\n{'=' * 50}")
        print(f"{league.upper()}")

        all_raw = []
        seen = set()

        for q in queries:
            print(f"  Query: {q}")
            # RATE LIMIT: wait 2 seconds between NewsAPI requests
            time.sleep(2)
            results = fetch_newsapi(q)
            print(f"    -> {len(results)} results")
            for a in results:
                url = a.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    all_raw.append(a)

        print(f"  Total unique: {len(all_raw)}")

        if not all_raw:
            print("  Skipping — nothing found")
            continue

        # Filter through Claude
        filtered = filter_with_claude(league, all_raw)
        print(f"  Claude kept: {len(filtered)}")

        if not filtered:
            print("  Nothing passed filter")
            continue

        # Add metadata
        for a in filtered:
            a["fetched_at"] = now_str
            a["league"] = league

        # Ensure league key exists
        if league not in data["articles"]:
            data["articles"][league] = []

        # Deduplicate
        existing_urls = {a["url"] for a in data["articles"][league]}
        new = [a for a in filtered if a.get("url") and a["url"] not in existing_urls]
        print(f"  New after dedup: {len(new)}")

        # Prepend and cap
        data["articles"][league] = (new + data["articles"][league])[:50]
        total += len(new)

    data["last_updated"] = now_str

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 50}")
    print(f"Done. +{total} new articles.")
    print(f"File size: {os.path.getsize(DATA_FILE)} bytes")


if __name__ == "__main__":
    main()
