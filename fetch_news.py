"""
fetch_news.py — daily via GitHub Action
Queries GDELT (free, no API key) -> filters through Claude -> appends to data.json
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# One GDELT query per league. GDELT ANDs space-separated terms and supports
# "quoted phrases" and (a OR b) groups. Each query anchors on the league, then
# requires at least one international-expansion / rights term.
QUERIES = {
    "pl": '"Premier League" (streaming OR broadcast OR "media rights" OR international OR "United States" OR overseas OR audience)',
    "ipl": '(cricket OR "Indian Premier League") (broadcast OR streaming OR "media rights" OR international OR global OR T20)',
    "nfl": '"NFL" (international OR Germany OR Europe OR London OR "Game Pass" OR broadcast OR overseas OR expansion)',
    "nba": '"NBA" (international OR Europe OR FIBA OR China OR Africa OR broadcast OR expansion OR global)',
    "laliga": '("La Liga" OR LaLiga) (international OR India OR broadcast OR "media rights" OR global OR expansion)',
    "japac": '(Ohtani OR sumo OR "World Baseball Classic") (global OR international OR streaming OR brand OR sponsorship OR rights)',
    "tennis": '(ATP OR WTA OR tennis) ("Saudi Arabia" OR China OR "broadcast rights" OR streaming OR "Grand Slam" OR international)',
    "f1": '("Formula 1" OR "Formula One") (Vegas OR Miami OR Saudi OR streaming OR "broadcast rights" OR global OR international)',
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


def fetch_gdelt(query, days_back=14, maxrecords=25):
    """Fetch articles from the GDELT 2.0 DOC API (free, no key required)."""
    params = urlencode({
        "query": query,
        "mode": "ArtList",
        "maxrecords": maxrecords,
        "timespan": f"{days_back}d",
        "sort": "DateDesc",
        "format": "json",
    })
    url = f"https://api.gdeltproject.org/api/v2/doc/doc?{params}"
    req = Request(url, headers={"User-Agent": "SportsSupplyChain/1.0 (+github-actions)"})
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace").strip()
    except HTTPError as e:
        print(f"    GDELT error ({e.code}): {query[:60]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"    GDELT error: {e}", file=sys.stderr)
        return []

    # GDELT returns a plain-text message (not JSON) when a query is malformed.
    if not raw or raw[0] not in "{[":
        print(f"    GDELT non-JSON response: {raw[:120]}", file=sys.stderr)
        return []

    try:
        articles = json.loads(raw).get("articles", []) or []
    except json.JSONDecodeError:
        print(f"    GDELT JSON decode failed: {raw[:120]}", file=sys.stderr)
        return []

    out = []
    for a in articles:
        # GDELT has no per-article snippet; keep English-language results only.
        if (a.get("language") or "English") != "English":
            continue
        out.append({
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "source": {"name": a.get("domain", "")},
            "description": "",
            "seendate": a.get("seendate", ""),
        })
    return out


def filter_with_claude(league, articles):
    """Send raw articles to Claude for relevance scoring and summary."""
    if not articles:
        return []

    context = LEAGUE_CONTEXT.get(league, "")
    block = "\n\n".join([
        f"[{i+1}] {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nURL: {a.get('url','')}\nSnippet: {a.get('description') or 'N/A'}"
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
        "model": "claude-sonnet-4-6",
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

    for league, query in QUERIES.items():
        print(f"\n{'=' * 50}")
        print(f"{league.upper()}")
        print(f"  Query: {query}")

        # RATE LIMIT: GDELT asks for no more than one request every few seconds.
        time.sleep(5)
        results = fetch_gdelt(query)
        print(f"    -> {len(results)} results")

        all_raw = []
        seen = set()
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
