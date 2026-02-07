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
DEBUG = os.environ.get("DEBUG") == "1"

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
LOOKBACK_HOURS_NEWS = 6
LOOKBACK_HOURS_REDDIT = 48
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


def log(msg: str):
    if DEBUG:
        print(msg)


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


def kw_in_text(text: str, kw: str) -> bool:
    if " " in kw:
        return kw in text
    if kw.isalpha() and len(kw) <= 3:
        return re.search(rf"\\b{re.escape(kw)}\\b", text) is not None
    return kw in text


def strip_html(s: str) -> str:
    return re.sub(r"<.*?>", "", s or "").strip()


def topic_hits_in_title(title: str) -> int:
    text = norm_text(title)
    hits = 0
    for kws in TOPIC_KEYWORDS.values():
        for kw in kws:
            if kw_in_text(text, kw):
                hits += 1
    return hits


def topic_label(title: str):
    text = norm_text(title)
    best_topic = None
    best_hits = 0
    for topic, kws in TOPIC_KEYWORDS.items():
        hits = 0
        for kw in kws:
            if kw_in_text(text, kw):
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_topic = topic
    return best_topic, best_hits


def lux_immigration_hit(title: str) -> bool:
    text = norm_text(title)
    for kw in TOPIC_KEYWORDS["Lux_immigration"]:
        if kw_in_text(text, kw):
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
        log(f"[debug] RSS {name}: entries={len(d.entries)}")
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
        log("[debug] Guardian: missing API key")
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
    log(f"[debug] Guardian: results={len(results)}")
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
        children = []
        try:
            r = requests.get(url, params={"limit": 50}, headers=headers, timeout=20)
            log(f"[debug] Reddit JSON r/{sub}: status={r.status_code}")
            r.raise_for_status()
            children = (r.json().get("data") or {}).get("children") or []
        except requests.RequestException as e:
            print(f"[warn] Reddit fetch failed for r/{sub}: {e}")
        if not children:
            rss_url = f"https://www.reddit.com/r/{sub}/hot/.rss"
            d = feedparser.parse(rss_url, request_headers=headers)
            log(f"[debug] Reddit RSS r/{sub}: entries={len(d.entries)}")
            for e in d.entries[:50]:
                link = e.get("link")
                if not link:
                    continue
                items.append({
                    "source": f"Reddit_r_{sub}",
                    "kind": "reddit",
                    "subreddit": sub,
                    "title": (e.get("title") or "").strip(),
                    "url": link.strip(),
                    "published_ts": parse_published(e),
                    "summary": "",
                    "score": 0,
                    "comments": 0,
                    "popularity": 0,
                })
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


def pick_first(candidates, used_urls):
    for it in candidates:
        if it["url"] not in used_urls:
            used_urls.add(it["url"])
            return it
    return None


def pick_news_diverse(candidates, selected, selected_urls, limit):
    topic_counts = {}
    for it in selected:
        topic = it.get("topic")
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    for it in candidates:
        if len(selected) >= limit:
            break
        if it["url"] in selected_urls:
            continue
        topic = it.get("topic")
        if topic and topic_counts.get(topic, 0) == 0:
            selected.append(it)
            selected_urls.add(it["url"])
            topic_counts[topic] = 1

    for it in candidates:
        if len(selected) >= limit:
            break
        if it["url"] in selected_urls:
            continue
        topic = it.get("topic")
        selected.append(it)
        selected_urls.add(it["url"])
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1


def format_message(top5):
    lines = []
    utc_plus_8 = timezone(timedelta(hours=8))
    local_time = datetime.now(utc_plus_8).strftime("%H:%M")
    lines.append(f"news feed for Laura at {local_time}\n")
    for i, it in enumerate(top5, 1):
        if it.get("kind") == "reddit":
            lines.append(
                f"{i}) [Reddit r/{it['subreddit']}] {it['title']}"
            )
        else:
            lines.append(f"{i}) [{it['source']}] {it['title']}")
        if it["summary"]:
            lines.append(f"   Summary: {it['summary']}")
        lines.append(f"   Link: {it['url']}\n")
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

    news_cutoff = time.time() - LOOKBACK_HOURS_NEWS * 3600
    reddit_cutoff = time.time() - LOOKBACK_HOURS_REDDIT * 3600
    news_all_kw = []
    news_primary = []
    for it in news_items:
        title_norm = norm_text(it["title"])
        it["title_norm"] = title_norm
        it["topic_hits"] = topic_hits_in_title(it["title"])
        it["topic"], it["topic_hits_primary"] = topic_label(it["title"])
        if it["topic_hits"] <= 0:
            continue
        it["lux_hit"] = lux_immigration_hit(it["title"])
        it["is_seen"] = already_seen(conn, it["url"], title_norm)
        it["in_window"] = it["published_ts"] >= news_cutoff
        news_all_kw.append(it)
        if it["in_window"] and not it["is_seen"]:
            news_primary.append(it)

    reddit_all = []
    for it in reddit_items:
        title_norm = norm_text(it["title"])
        it["title_norm"] = title_norm
        it["is_seen"] = already_seen(conn, it["url"], title_norm)
        it["in_window"] = it["published_ts"] >= reddit_cutoff
        reddit_all.append(it)

    selected_news = []
    selected_news_urls = set()
    lux_news = [x for x in news_primary if x["lux_hit"]]
    lux_news.sort(key=lambda x: (x["topic_hits"], x["published_ts"]), reverse=True)
    if lux_news:
        first = pick_first(lux_news, selected_news_urls)
        if first:
            selected_news.append(first)
    else:
        lux_news_fallback = [x for x in news_all_kw if x["lux_hit"]]
        lux_news_fallback.sort(key=lambda x: (x["topic_hits"], x["published_ts"]), reverse=True)
        first = pick_first(lux_news_fallback, selected_news_urls)
        if first:
            selected_news.append(first)

    remaining_news_primary = [x for x in news_primary if x["url"] not in selected_news_urls]
    remaining_news_primary.sort(key=lambda x: (x["topic_hits"], x["published_ts"]), reverse=True)
    pick_news_diverse(remaining_news_primary, selected_news, selected_news_urls, OTHER_NEWS_COUNT)

    if len(selected_news) < OTHER_NEWS_COUNT:
        remaining_news_fallback = [x for x in news_all_kw if x["url"] not in selected_news_urls]
        remaining_news_fallback.sort(key=lambda x: (x["topic_hits"], x["published_ts"]), reverse=True)
        pick_news_diverse(remaining_news_fallback, selected_news, selected_news_urls, OTHER_NEWS_COUNT)
    if DEBUG:
        def _topic_counts(items):
            counts = {}
            for x in items:
                t = x.get("topic")
                counts[t] = counts.get(t, 0) + 1
            return counts
        log(f"[debug] news_all_kw={len(news_all_kw)} topic_counts={_topic_counts(news_all_kw)}")
        log(f"[debug] news_primary={len(news_primary)} topic_counts={_topic_counts(news_primary)}")
        log(f"[debug] selected_news={len(selected_news)} topic_counts={_topic_counts(selected_news)}")

    selected_reddit = []
    selected_reddit_urls = set()
    lux_sub = "Luxembourg"
    lux_posts_primary = [
        x for x in reddit_all
        if x.get("subreddit") == lux_sub and not x["is_seen"] and x.get("comments", 0) >= 10
    ]
    lux_posts_fallback = [
        x for x in reddit_all
        if x.get("subreddit") == lux_sub and x.get("comments", 0) >= 10
    ]
    log(
        "[debug] reddit lux pools sizes (>=10 comments, no time window): "
        f"primary={len(lux_posts_primary)} fallback={len(lux_posts_fallback)}"
    )
    for pool in [lux_posts_primary, lux_posts_fallback]:
        pool.sort(key=lambda x: x["published_ts"], reverse=True)
        pick = pick_first(pool, selected_reddit_urls)
        if pick:
            selected_reddit.append(pick)
            break

    day_key = utc8_day_key()
    used_today = get_used_subreddits_today(conn, day_key)
    planned_today = {x["subreddit"] for x in selected_reddit}

    second_pick = None
    non_lux_primary = [x for x in reddit_all if x.get("subreddit") != lux_sub and x["in_window"] and not x["is_seen"]]
    non_lux_fallback_1 = [x for x in reddit_all if x.get("subreddit") != lux_sub and x["in_window"]]
    non_lux_fallback_2 = [x for x in reddit_all if x.get("subreddit") != lux_sub and not x["is_seen"]]
    non_lux_fallback_3 = [x for x in reddit_all if x.get("subreddit") != lux_sub]
    log(
        "[debug] reddit non-lux pools sizes: "
        f"primary={len(non_lux_primary)} fallback1={len(non_lux_fallback_1)} "
        f"fallback2={len(non_lux_fallback_2)} fallback3={len(non_lux_fallback_3)}"
    )
    pools = [non_lux_primary, non_lux_fallback_1, non_lux_fallback_2, non_lux_fallback_3]
    for pool in pools:
        pool.sort(key=lambda x: (x["popularity"], x["published_ts"]), reverse=True)

    if len(used_today.union(planned_today)) < 3:
        for pool in pools:
            for cand in pool:
                if cand["url"] in selected_reddit_urls:
                    continue
                if cand["subreddit"] not in used_today.union(planned_today):
                    second_pick = cand
                    break
            if second_pick is not None:
                break

    if second_pick is None:
        for pool in pools:
            second_pick = pick_first(pool, selected_reddit_urls)
            if second_pick is not None:
                break
    if second_pick:
        selected_reddit_urls.add(second_pick["url"])
        selected_reddit.append(second_pick)
    log(f"[debug] selected_reddit={len(selected_reddit)}")

    selected = selected_reddit[:FIXED_REDDIT_COUNT] + selected_news[:OTHER_NEWS_COUNT]
    log(f"[debug] selected_total={len(selected)} kinds={[x.get('kind') for x in selected]}")
    if not selected:
        tg_send("No important new items were found this round (or all were already sent).")
        return

    for it in selected:
        mark_seen(conn, it["url"], it["title_norm"])
        if it.get("kind") == "reddit":
            mark_subreddit_used(conn, day_key, it["subreddit"])

    msg = format_message(selected)
    tg_send(msg)


if __name__ == "__main__":
    main()


