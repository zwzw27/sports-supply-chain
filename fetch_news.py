"""
fetch_news.py — daily via GitHub Action
Queries NewsAPI -> filters through Claude -> appends to data.json
"""
import json,os,sys
from datetime import datetime,timedelta
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

NEWSAPI_KEY=os.environ.get("NEWSAPI_KEY","")
ANTHROPIC_KEY=os.environ.get("ANTHROPIC_API_KEY","")

QUERIES={
    "pl":["Premier League streaming rights","Premier League USA viewers","Premier League international broadcast"],
    "ipl":["IPL broadcast rights international","IPL cricket global expansion","cricket T20 media rights"],
    "nfl":["NFL Germany Europe expansion","NFL international games rights","NFL Game Pass Europe","NFL international broadcast deal"],
    "nba":["NBA international expansion","NBA Europe basketball league FIBA","NBA Africa China broadcast"],
    "laliga":["La Liga international rights","La Liga India expansion","Spanish football global broadcast"],
    "japac":["Ohtani global brand ambassador","World Baseball Classic broadcast rights","Asian sports streaming growth","Ohtani sponsorship deal international","Japanese baseball global audience","sumo international streaming"],
    "tennis":["ATP WTA international broadcast rights","tennis Grand Slam streaming deal","tennis Saudi Arabia expansion"],
    "f1":["Formula 1 new race international","F1 Las Vegas Miami Saudi broadcast","Formula 1 streaming rights global"],
}

LEAGUE_CONTEXT={
    "pl":"the Premier League's expansion into the US and other international markets — streaming deals, broadcast rights, pre-season tours, audience growth outside England",
    "ipl":"the IPL's expansion beyond India — broadcast deals in the UK, Middle East, USA, the T20 format as a global export, cricket's push into new markets",
    "nfl":"the NFL's push into Europe and beyond — the DAZN deal, Game Pass, regular-season games in Munich/Frankfurt/London/other international cities, rights packaging",
    "nba":"the NBA's global expansion — new European league with FIBA, Amazon broadcast talks, Basketball Without Borders, China, international revenue",
    "laliga":"La Liga's international expansion — offices in India, broadcast deals in the US and Middle East, star-driven global strategy",
    "japac":"Japan and APAC sports as global cultural exports. Relevant topics: Ohtani as a global brand/ambassador bridging Japanese and American markets, World Baseball Classic as international rights product, Asian sports streaming deals reaching Western audiences, the commercial globalization of Japanese/Korean/APAC athletes and leagues. REJECT: domestic MLB game recaps, domestic Japanese league scores, merchandise/jersey sales listings, fantasy baseball.",
    "tennis":"Tennis expanding into new markets — ATP/WTA events in Saudi Arabia, China, streaming rights fragmentation across platforms, Grand Slam broadcast deals",
    "f1":"Formula 1's global expansion — new races in Las Vegas, Miami, Saudi Arabia, the Netflix/Drive to Survive effect, Liberty Media's entertainment-first strategy, streaming rights",
}

DATA_FILE="data.json"

def fetch_newsapi(query,days_back=14):
    from_date=(datetime.utcnow()-timedelta(days=days_back)).strftime("%Y-%m-%d")
    params=urlencode({"q":query,"from":from_date,"sortBy":"relevancy","language":"en","pageSize":10,"apiKey":NEWSAPI_KEY})
    req=Request(f"https://newsapi.org/v2/everything?{params}",headers={"User-Agent":"SportsSupplyChain/1.0"})
    try:
        with urlopen(req,timeout=15) as r:return json.loads(r.read().decode()).get("articles",[])
    except HTTPError as e:
        print(f"  NewsAPI error ({e.code}): {query}",file=sys.stderr);return[]

def filter_with_claude(league,articles):
    if not articles:return[]
    context=LEAGUE_CONTEXT.get(league,"")
    block="\n\n".join([f"[{i+1}] {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nURL: {a.get('url','')}\nSnippet: {a.get('description','N/A')}" for i,a in enumerate(articles)])
    prompt=f"""You are an editor at The Current, a B2B publication for senior marketers and media agencies.

Curating a feed about: {context}

STRICT FILTERING — be aggressive about rejecting irrelevant content.

REJECT:
- Domestic results, scores, player transfers, match previews, race results
- Domestic-only business (salary caps, coaching, relegation, team orders)
- Articles that only tangentially mention the sport without focusing on INTERNATIONAL expansion, rights, or global brand impact
- General news aggregator content unless specifically about international expansion
- Clickbait, SEO spam, betting, fantasy sports, listicles, merchandise listings
- Removed/unavailable articles

KEEP articles that DIRECTLY address:
- International broadcast or streaming rights deals
- Leagues/tours playing events in new or expanding markets
- Audience growth data in export markets
- Global brand/sponsor partnerships that demonstrate international reach
- Strategic moves to enter new geographies
- Athletes as cross-cultural brand ambassadors (e.g. Ohtani bridging Japan-US markets)

Score 1-10. Return ONLY score 8+.

For keepers, return a JSON array:
- "title": cleaned title, proper capitalization and punctuation
- "source": publication name
- "url": URL
- "summary": max 120 chars, for a media buyer, sharp and specific
- "score": 8-10

Valid JSON only. No markdown/backticks/preamble. Nothing qualifies? Return []

Articles:
{block}"""
    body=json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":1500,"messages":[{"role":"user","content":prompt}]}).encode()
    req=Request("https://api.anthropic.com/v1/messages",data=body,headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},method="POST")
    try:
        with urlopen(req,timeout=45) as r:
            data=json.loads(r.read().decode())
            text="".join(b["text"] for b in data.get("content",[]) if b.get("type")=="text").strip()
            if text.startswith("```"):text=text.split("\n",1)[1].rsplit("```",1)[0].strip()
            result=json.loads(text)
            return result if isinstance(result,list) else[]
    except Exception as e:
        print(f"  Claude error: {e}",file=sys.stderr);return[]

def main():
    if not NEWSAPI_KEY:print("ERROR: NEWSAPI_KEY not set",file=sys.stderr);sys.exit(1)
    if not ANTHROPIC_KEY:print("ERROR: ANTHROPIC_API_KEY not set",file=sys.stderr);sys.exit(1)
    data=json.load(open(DATA_FILE)) if os.path.exists(DATA_FILE) else{"last_updated":None,"articles":{k:[] for k in QUERIES}}
    now=datetime.utcnow().isoformat()+"Z"
    total=0
    for league,queries in QUERIES.items():
        print(f"\n{'='*50}\n{league.upper()}")
        all_raw,seen=[],set()
        for q in queries:
            print(f"  {q}")
            import time; time.sleep(1.5)
            for a in fetch_newsapi(q):
                url=a.get("url","")
                if url and url not in seen:seen.add(url);all_raw.append(a)
        print(f"  Unique: {len(all_raw)}")
        if not all_raw:continue
        filtered=filter_with_claude(league,all_raw)
        print(f"  Kept: {len(filtered)}")
        if not filtered:continue
        for a in filtered:a["fetched_at"]=now;a["league"]=league
        if league not in data["articles"]:data["articles"][league]=[]
        existing_urls={a["url"] for a in data["articles"][league]}
        new=[a for a in filtered if a.get("url") and a["url"] not in existing_urls]
        print(f"  New: {len(new)}")
        data["articles"][league]=(new+data["articles"][league])[:50]
        total+=len(new)
    data["last_updated"]=now
    with open(DATA_FILE,"w") as f:json.dump(data,f,indent=2,ensure_ascii=False)
    print(f"\nDone. +{total} articles. {os.path.getsize(DATA_FILE)} bytes")

if __name__=="__main__":main()
