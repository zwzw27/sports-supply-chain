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
from urllib.parse import urlencode
from urllib.error import HTTPError

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Multiple shorter queries per league — broader net, let Claude filter
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
    ],
    "nba": [
        "NBA international expansion",
        "NBA Europe basketball league",
        "NBA Africa China broadcast",
    ],
    "laliga": [
        "La Liga international rights",
        "La Liga India expansion",
        "Spanish football global broadcast",
    ],
    "anime": [
        "Japanese sports global streaming",
        "NPB baseball international",
        "B.League basketball Japan global",
        "sumo wrestling international broadcast",
    ],
}

LEAGUE_CONTEXT = {
    "pl": "the Premier League's expansion into the US and other international markets — streaming deals, broadcast rights, pre-season tours, audience growth outside England",
    "ipl": "the IPL's expansion beyond India — broadcast deals in the UK, Middle East, USA, the T20 format as a global export product, cricket's push into new markets",
    "nfl": "the NFL's push into Europe, especially Germany — the DAZN deal termination, Game Pass growth, regular-season games in Munich/Frankfurt/London, rights packaging strategy",
    "nba": "the NBA's global expansion — the new European league with FIBA, Amazon broadcast talks, Basketball Without Borders in Africa, China relationship, international revenue growth",
    "laliga": "La Liga's international expansion — offices in India, broadcast deals in the US and Middle East, star-driven global strategy, competition with Premier League for international audiences",
    "anime": "Japanese sports going global — NPB baseball, B.League basketball, sumo, anime-adjacent sports content reaching international audiences through streaming platforms and fandom networks. NOT American baseball (MLB), NOT general anime/manga entertainment.",
}

DATA_FILE = "data.json"


def fetch_newsapi(query, days_back=14):
    from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    params = urlencode({
        "q": query,
        "from": from_date,
        "sortBy": "relevancy",
        "language": "en",
        "pageSize": 8,
        "apiKey": NEWSAPI_KEY,
    })
    url = f"https://newsapi.org/v2/everything?{params}"
    req = Request(url, headers={"User-Agent": "SportsSupplyChain/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("articles", [])
    except HTTPError as e:
        print(f"  NewsAPI error ({e.code}) for: {query}", file=sys.stderr)
        return []


def filter_with_claude(league, articles):
    if not articles:
        return []

    context = LEAGUE_CONTEXT.get(league, "this league's international expansion")

    article_block = "\n\n".join([
        f"[{i+1}] {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nURL: {a.get('url','')}\nSnippet: {a.get('description','N/A')}"
        for i, a in enumerate(articles)
    ])

    prompt = f"""You are an editor at The Current, a B2B publication covering programmatic advertising and sports media rights for senior marketers and media agencies.

You are curating a feed about: {context}

Review these articles. Your job is STRICT RELEVANCE FILTERING.

REJECT articles that are:
- About domestic league results, scores, player transfers, or match coverage
- About a league's domestic business only (salary caps, relegation battles, coaching changes)
- Only tangentially mentioning the league without focusing on international expansion
- About a DIFFERENT sport or league (e.g. MLB is NOT Japanese baseball, general anime is NOT sports)
- Clickbait, SEO spam, fantasy sports, betting content, or low-quality aggregator content
- Removed or unavailable articles

KEEP only articles that directly address:
- International broadcast or streaming rights deals
- Leagues playing regular-season games in foreign markets
- Audience growth data in export markets
- Advertiser or sponsor activity tied to international expansion
- Strategic moves to enter new geographies (new offices, partnerships, league launches)

Score each article 1-10 for relevance to the INTERNATIONAL EXPANSION story specifically.
Return ONLY articles scoring 8 or above.

For each keeper, return a JSON array of objects:
- "title": cleaned article title
- "source": publication name
- "url": article URL
- "summary": one-line summary, max 120 chars, for a media buyer audience — specific and sharp
- "score": your relevance score (8-10)

Return valid JSON only. No markdown, no backticks, no preamble, no explanation.
If nothing scores 8+, return exactly: []

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
        with urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            return result if isinstance(result, list) else []
    except Exception as e:
        print(f"  Claude API error: {e}", file=sys.stderr)
        return []


def load_existing():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "articles": {k: [] for k in QUERIES.keys()}}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def deduplicate(existing, new):
    existing_urls = {a["url"] for a in existing}
    return [a for a in new if a.get("url") and a["url"] not in existing_urls]


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

    for league, queries in QUERIES.items():
        print(f"\n{'='*50}")
        print(f"Processing: {league.upper()}")

        all_raw = []
        seen_urls = set()
        for q in queries:
            print(f"  Query: {q}")
            raw = fetch_newsapi(q)
            print(f"    -> {len(raw)} results")
            for a in raw:
                url = a.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_raw.append(a)

        print(f"  Total unique: {len(all_raw)}")

        if not all_raw:
            print("  Skipping — nothing found")
            continue

        filtered = filter_with_claude(league, all_raw)
        print(f"  Claude kept: {len(filtered)} (score 8+)")

        if not filtered:
            print("  Nothing passed filter")
            continue

        for article in filtered:
            article["fetched_at"] = now
            article["league"] = league

        if league not in data["articles"]:
            data["articles"][league] = []

        new_articles = deduplicate(data["articles"][league], filtered)
        print(f"  New after dedup: {len(new_articles)}")

        data["articles"][league] = new_articles + data["articles"][league]
        data["articles"][league] = data["articles"][league][:50]
        total_added += len(new_articles)

    data["last_updated"] = now
    save_data(data)
    print(f"\n{'='*50}")
    print(f"Done. {total_added} new articles added.")
    print(f"File size: {os.path.getsize(DATA_FILE)} bytes")


if __name__ == "__main__":
    main()
