import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests
import feedparser

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
GUARDIAN_API_KEY = os.environ.get("GUARDIAN_API_KEY")

if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing TG_BOT_TOKEN or TG_CHAT_ID in environment.")

RSS_SOURCES = [
    ("DW", "https://rss.dw.com/rdf/rss-en-all"),
    ("France24", "https://www.france24.com/en/rss"),
    ("LeMonde_EN_Science", "https://www.lemonde.fr/en/science/rss_full.xml"),
]

REDDIT_SUBREDDITS = [
    "worldnews",
    "science",
    "europe",
    "Luxembourg",
    "EuropeanUnion",
    "dataisbeautiful",
]

TOPIC_KEYWORDS = {
    "EU_big": ["eu", "european commission", "ukraine", "russia"],
    "Lux_immigration": ["luxembourg", "residence", "visa", "blue card", "schengen", "immigration"],
    "Quant_fin": ["quant", "trading", "crypto", "volatility", "ecb", "rates", "inflation", "market"],
    "Research_engineering": ["research", "paper", "university", "aerospace", "wind", "grid", "nuclear", "semiconductor", "ai"],
    "Space": ["nasa", "esa", "launch", "satellite", "space", "astronomy"],
    "Taiwan_life": ["taiwan", "lgbtq", "gender", "childcare", "fertility", "marriage", "cost of living", "saving"],
}

DB_PATH = "seen.sqlite"
LOOKBACK_HOURS = 6
MAX_MESSAGE_LEN = 3900
TOTAL_PUSH_COUNT = 5
FIXED_REDDIT_COUNT = 2
OTHER_NEWS_COUNT = TOTAL_PUSH_COUNT - FIXED_REDDIT_COUNT


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, ts INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_title (title TEXT PRIMARY KEY, ts INTEGER)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS subreddit_daily (day TEXT, subreddit TEXT, cnt INTEGER, PRIMARY KEY(day, subreddit))"
    )
    conn.commit()
    return conn


def already_seen(conn, url: str, title_norm: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen WHERE url=?", (url,))
    if cur.fetchone() is not None:
        return True
    cur = conn.execute("SELECT 1 FROM seen_title WHERE title=?", (title_norm,))
    return cur.fetchone() is not None


def mark_seen(conn, url: str, title_norm: str):
    ts = int(time.time())
    conn.execute("INSERT OR IGNORE INTO seen(url, ts) VALUES(?, ?)", (url, ts))
    conn.execute("INSERT OR IGNORE INTO seen_title(title, ts) VALUES(?, ?)", (title_norm, ts))
    conn.commit()


def norm_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_html(s: str) -> str:
    return re.sub(r"<.*?>", "", s or "").strip()


def topic_hits_in_title(title: str) -> int:
    text = norm_text(title)
    hits = 0
    for kws in TOPIC_KEYWORDS.values():
        for kw in kws:
            if kw in text:
                hits += 1
    return hits


def lux_immigration_hit(title: str) -> bool:
    text = norm_text(title)
    for kw in TOPIC_KEYWORDS["Lux_immigration"]:
        if kw in text:
            return True
    return False


def extract_summary(entry) -> str:
    for key in ["summary", "description"]:
        if key in entry and entry[key]:
            return strip_html(entry[key])[:400]
    return ""


def parse_published(entry) -> int:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        return int(time.mktime(t))
    return int(time.time())


def fetch_rss():
    items = []
    headers = {"User-Agent": "Mozilla/5.0 (news-bot; +https://example.com)"}
    for name, url in RSS_SOURCES:
        d = feedparser.parse(url, request_headers=headers)
        for e in d.entries[:50]:
            link = e.get("link")
            if not link:
                continue
            items.append({
                "source": name,
                "kind": "news",
                "title": (e.get("title") or "").strip(),
                "url": link.strip(),
                "published_ts": parse_published(e),
                "summary": extract_summary(e),
            })
    return items


def parse_guardian_time(s: str) -> int:
    if not s:
        return int(time.time())
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return int(time.time())


def fetch_guardian():
    if not GUARDIAN_API_KEY:
        return []
    url = "https://content.guardianapis.com/search"
    params = {
        "api-key": GUARDIAN_API_KEY,
        "show-fields": "trailText",
        "page-size": 50,
        "order-by": "newest",
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        print(f"[warn] Guardian fetch failed: {e}")
        return []
    results = data.get("response", {}).get("results", [])
    items = []
    for it in results:
        items.append({
            "source": "Guardian",
            "kind": "news",
            "title": (it.get("webTitle") or "").strip(),
            "url": (it.get("webUrl") or "").strip(),
            "published_ts": parse_guardian_time(it.get("webPublicationDate")),
            "summary": strip_html((it.get("fields") or {}).get("trailText", ""))[:400],
        })
    return items


def fetch_reddit_hot():
    items = []
    headers = {"User-Agent": "Mozilla/5.0 (news-bot; +https://example.com)"}
    for sub in REDDIT_SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/hot.json"
        try:
            r = requests.get(url, params={"limit": 50}, headers=headers, timeout=20)
            r.raise_for_status()
            children = (r.json().get("data") or {}).get("children") or []
        except requests.RequestException as e:
            print(f"[warn] Reddit fetch failed for r/{sub}: {e}")
            continue
        for child in children:
            data = child.get("data") or {}
            permalink = data.get("permalink") or ""
            if not permalink:
                continue
            score = int(data.get("score") or 0)
            comments = int(data.get("num_comments") or 0)
            items.append({
                "source": f"Reddit_r_{sub}",
                "kind": "reddit",
                "subreddit": sub,
                "title": (data.get("title") or "").strip(),
                "url": f"https://www.reddit.com{permalink}",
                "published_ts": int(data.get("created_utc") or time.time()),
                "summary": "",
                "score": score,
                "comments": comments,
                "popularity": score + comments,
            })
    return items


def utc8_day_key() -> str:
    utc_plus_8 = timezone(timedelta(hours=8))
    return datetime.now(utc_plus_8).strftime("%Y-%m-%d")


def get_used_subreddits_today(conn, day_key: str):
    cur = conn.execute("SELECT subreddit FROM subreddit_daily WHERE day=?", (day_key,))
    return {r[0] for r in cur.fetchall()}


def mark_subreddit_used(conn, day_key: str, subreddit: str):
    conn.execute(
        "INSERT INTO subreddit_daily(day, subreddit, cnt) VALUES(?, ?, 1) "
        "ON CONFLICT(day, subreddit) DO UPDATE SET cnt = cnt + 1",
        (day_key, subreddit),
    )
    conn.commit()


def format_message(top5):
    lines = []
    utc_plus_8 = timezone(timedelta(hours=8))
    local_time = datetime.now(utc_plus_8).strftime("%H:%M")
    lines.append(f"news feed for Laura at {local_time}\\n")
    for i, it in enumerate(top5, 1):
        if it.get("kind") == "reddit":
            lines.append(
                f"{i}) [Reddit r/{it['subreddit']}] {it['title']}"
            )
            lines.append(f"   熱度：{it['score']} 讚 + {it['comments']} 留言 = {it['popularity']}")
        else:
            lines.append(f"{i}) [{it['source']}] {it['title']}")
        if it["summary"]:
            lines.append(f"   摘要：{it['summary']}")
        lines.append(f"   連結：{it['url']}\n")
    msg = "\n".join(lines)
    return msg[:MAX_MESSAGE_LEN]


def tg_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()


def main():
    conn = init_db()

    news_items = []
    news_items.extend(fetch_rss())
    news_items.extend(fetch_guardian())
    reddit_items = fetch_reddit_hot()

    cutoff = time.time() - LOOKBACK_HOURS * 3600
    fresh_news = []
    for it in news_items:
        if it["published_ts"] < cutoff:
            continue
        title_norm = norm_text(it["title"])
        if already_seen(conn, it["url"], title_norm):
            continue
        it["title_norm"] = title_norm
        it["topic_hits"] = topic_hits_in_title(it["title"])
        if it["topic_hits"] <= 0:
            continue
        it["lux_hit"] = lux_immigration_hit(it["title"])
        fresh_news.append(it)

    fresh_reddit = []
    for it in reddit_items:
        if it["published_ts"] < cutoff:
            continue
        title_norm = norm_text(it["title"])
        if already_seen(conn, it["url"], title_norm):
            continue
        it["title_norm"] = title_norm
        fresh_reddit.append(it)

    selected_news = []
    lux_news = [x for x in fresh_news if x["lux_hit"]]
    lux_news.sort(key=lambda x: (x["topic_hits"], x["published_ts"]), reverse=True)
    if lux_news:
        selected_news.append(lux_news[0])

    selected_urls = {x["url"] for x in selected_news}
    remaining_news = [x for x in fresh_news if x["url"] not in selected_urls]
    remaining_news.sort(key=lambda x: (x["topic_hits"], x["published_ts"]), reverse=True)
    selected_news.extend(remaining_news[: max(0, OTHER_NEWS_COUNT - len(selected_news))])

    selected_reddit = []
    lux_sub = "Luxembourg"
    lux_posts = [x for x in fresh_reddit if x.get("subreddit") == lux_sub]
    lux_posts.sort(key=lambda x: (x["popularity"], x["published_ts"]), reverse=True)
    if lux_posts:
        selected_reddit.append(lux_posts[0])

    non_lux_posts = [x for x in fresh_reddit if x.get("subreddit") != lux_sub]
    non_lux_posts.sort(key=lambda x: (x["popularity"], x["published_ts"]), reverse=True)

    day_key = utc8_day_key()
    used_today = get_used_subreddits_today(conn, day_key)
    planned_today = {x["subreddit"] for x in selected_reddit}

    second_pick = None
    if non_lux_posts:
        if len(used_today.union(planned_today)) < 3:
            for cand in non_lux_posts:
                if cand["subreddit"] not in used_today.union(planned_today):
                    second_pick = cand
                    break
        if second_pick is None:
            second_pick = non_lux_posts[0]
    if second_pick:
        selected_reddit.append(second_pick)

    selected = selected_reddit[:FIXED_REDDIT_COUNT] + selected_news[:OTHER_NEWS_COUNT]
    if not selected:
        tg_send("🛰️ 這一輪沒有抓到新的重要消息（或都已推播過）。")
        return

    for it in selected:
        mark_seen(conn, it["url"], it["title_norm"])
        if it.get("kind") == "reddit":
            mark_subreddit_used(conn, day_key, it["subreddit"])

    msg = format_message(selected)
    tg_send(msg)


if __name__ == "__main__":
    main()


