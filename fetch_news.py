"""
fetch_news.py
Runs daily via GitHub Action.
1. Queries NewsAPI for each league's international expansion story
2. Sends results to Claude for relevance filtering + summary
3. Appends keeper articles to data.json
"""

import json
import os
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote
from urllib.error import HTTPError

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Search queries per league — tuned for international rights/expansion coverage
QUERIES = {
    "pl": "Premier League AND (USA OR America OR NBC OR Peacock OR streaming rights OR international)",
    "ipl": "IPL AND (broadcast OR rights OR UK OR Middle East OR USA OR cricket expansion)",
    "nfl": "NFL AND (Germany OR Europe OR international OR London OR Game Pass OR DAZN)",
    "nba": "NBA AND (Europe OR China OR Africa OR FIBA OR international OR expansion OR Amazon)",
    "laliga": "La Liga AND (India OR USA OR Middle East OR international OR broadcast rights)",
    "anime": "(Japanese sports OR NPB OR B.League OR sumo OR anime sports) AND (global OR international OR streaming)",
}

DATA_FILE = "data.json"


def fetch_newsapi(query: str, days_back: int = 3) -> list:
    """Fetch articles from NewsAPI."""
    from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
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
        print(f"  NewsAPI error for query: {e.code}", file=sys.stderr)
        return []


def filter_with_claude(league: str, articles: list) -> list:
    """Send raw articles to Claude for relevance scoring and summary."""
    if not articles:
        return []

    # Build a compact representation
    article_block = "\n\n".join([
        f"[{i+1}] {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nURL: {a.get('url','')}\nSnippet: {a.get('description','')}"
        for i, a in enumerate(articles)
    ])

    prompt = f"""You are an editor at a B2B publication covering programmatic advertising and sports media rights for senior marketers and agencies.

Review these articles about {league} international expansion / rights deals. For each article, score its relevance (1-10) to the story of sports leagues exporting their product globally — rights deals, broadcast expansion, audience growth in new markets, advertiser implications.

Return ONLY articles scoring 7 or above. For each keeper, return a JSON array of objects with these fields:
- "title": the article title (clean it up if needed)
- "source": publication name
- "url": the article URL
- "summary": a one-line summary (max 120 chars) written for a media buyer audience — sharp, no hype
- "score": your relevance score

Return valid JSON only. No markdown, no preamble. If no articles score 7+, return an empty array [].

Articles:
{article_block}"""

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
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            # Parse JSON from response
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
    except Exception as e:
        print(f"  Claude API error: {e}", file=sys.stderr)
        return []


def load_existing() -> dict:
    """Load existing data.json or create empty structure."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "articles": {k: [] for k in QUERIES.keys()}}


def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def deduplicate(existing: list, new: list) -> list:
    """Don't add articles we already have (by URL)."""
    existing_urls = {a["url"] for a in existing}
    return [a for a in new if a["url"] not in existing_urls]


def main():
    if not NEWSAPI_KEY:
        print("ERROR: NEWSAPI_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    data = load_existing()
    now = datetime.utcnow().isoformat() + "Z"
    total_added = 0

    for league, query in QUERIES.items():
        print(f"\n{'='*40}")
        print(f"Processing: {league.upper()}")
        print(f"Query: {query[:60]}...")

        # Fetch
        raw = fetch_newsapi(query)
        print(f"  NewsAPI returned {len(raw)} articles")

        if not raw:
            continue

        # Filter through Claude
        filtered = filter_with_claude(league, raw)
        print(f"  Claude kept {len(filtered)} articles")

        if not filtered:
            continue

        # Add metadata
        for article in filtered:
            article["fetched_at"] = now
            article["league"] = league

        # Deduplicate against existing
        if league not in data["articles"]:
            data["articles"][league] = []

        new_articles = deduplicate(data["articles"][league], filtered)
        print(f"  {len(new_articles)} new after dedup")

        data["articles"][league] = new_articles + data["articles"][league]

        # Cap at 50 per league to keep file manageable
        data["articles"][league] = data["articles"][league][:50]

        total_added += len(new_articles)

    data["last_updated"] = now
    save_data(data)
    print(f"\nDone. Added {total_added} new articles total.")
    print(f"Data file size: {os.path.getsize(DATA_FILE)} bytes")


if __name__ == "__main__":
    main()
