import os
import re
import sqlite3
import time
from datetime import datetime, timezone

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
    ("Reddit_r_europe", "https://www.reddit.com/r/europe/new/.rss"),
]

TOPIC_KEYWORDS = {
    "EU_big": ["eu", "european commission", "parliament", "sanction", "nato", "ukraine", "russia"],
    "Lux_immigration": ["luxembourg", "residence", "visa", "blue card", "schengen", "immigration", "asylum"],
    "Quant_fin": ["quant", "trading", "crypto", "volatility", "ecb", "rates", "inflation", "market"],
    "Research_engineering": ["research", "paper", "university", "aerospace", "wind", "grid", "nuclear", "semiconductor", "ai"],
    "Space": ["nasa", "esa", "launch", "satellite", "space", "astronomy"],
    "Taiwan_life": ["taiwan", "lgbtq", "gender", "childcare", "fertility", "marriage", "cost of living", "saving"],
}

POLICY_KEYWORDS = [
    "policy", "law", "bill", "regulation", "directive", "ban", "permit", "residence", "visa", "immigration"
]

SOURCE_WEIGHT = {
    "Guardian": 1.2,
    "Euronews": 1.15,
    "DW": 1.1,
    "France24": 1.05,
    "LeMonde": 1.1,
    "Reddit": 0.95,
}

DB_PATH = "seen.sqlite"
LOOKBACK_HOURS = 48
MAX_MESSAGE_LEN = 3900


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, ts INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_title (title TEXT PRIMARY KEY, ts INTEGER)")
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


def score_item(source_name: str, title: str, summary: str, published_ts: int) -> float:
    text = norm_text(title + " " + summary)

    topic_hits = 0
    for kws in TOPIC_KEYWORDS.values():
        for kw in kws:
            if kw in text:
                topic_hits += 1

    policy_hits = 0
    for kw in POLICY_KEYWORDS:
        if kw in text:
            policy_hits += 1

    age_hours = max(0.0, (time.time() - published_ts) / 3600.0)
    recency = max(0.0, 1.0 - age_hours / 24.0)

    w = 1.0
    for k, v in SOURCE_WEIGHT.items():
        if k.lower() in source_name.lower():
            w = v
            break

    return (topic_hits * 1.0 + policy_hits * 0.5 + recency * 2.0) * w


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
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    results = data.get("response", {}).get("results", [])
    items = []
    for it in results:
        items.append({
            "source": "Guardian",
            "title": (it.get("webTitle") or "").strip(),
            "url": (it.get("webUrl") or "").strip(),
            "published_ts": parse_guardian_time(it.get("webPublicationDate")),
            "summary": strip_html((it.get("fields") or {}).get("trailText", ""))[:400],
        })
    return items


def format_message(top5):
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"🛰️ 每 3 小時精選（{now}）\n")
    for i, it in enumerate(top5, 1):
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

    items = []
    items.extend(fetch_rss())
    items.extend(fetch_guardian())

    cutoff = time.time() - LOOKBACK_HOURS * 3600
    fresh = []
    for it in items:
        if it["published_ts"] < cutoff:
            continue
        title_norm = norm_text(it["title"])
        if already_seen(conn, it["url"], title_norm):
            continue
        it["title_norm"] = title_norm
        fresh.append(it)

    for it in fresh:
        it["score"] = score_item(it["source"], it["title"], it["summary"], it["published_ts"])
    fresh.sort(key=lambda x: x["score"], reverse=True)

    top5 = fresh[:5]
    if not top5:
        tg_send("🛰️ 這一輪沒有抓到新的重要消息（或都已推播過）。")
        return

    for it in top5:
        mark_seen(conn, it["url"], it["title_norm"])

    msg = format_message(top5)
    tg_send(msg)


if __name__ == "__main__":
    main()
