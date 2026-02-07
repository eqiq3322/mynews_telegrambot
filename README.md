# realtime_push

Telegram news push bot.

## Data sources

### News sites
- DW RSS: `https://rss.dw.com/rdf/rss-en-all`
- France24 RSS: `https://www.france24.com/en/rss`
- LeMonde English Science RSS: `https://www.lemonde.fr/en/science/rss_full.xml`
- Guardian Content API (enabled only if `GUARDIAN_API_KEY` is set)

### Reddit boards
- `r/worldnews`
- `r/science`
- `r/europe`
- `r/Luxembourg`
- `r/EuropeanUnion`
- `r/dataisbeautiful`

Reddit is fetched from each board's hot feed: `https://www.reddit.com/r/<sub>/hot.json`.

## Time window

- Lookback window is last `6` hours (`LOOKBACK_HOURS = 6`).

## Push composition per run

Each run targets up to 5 items:

1. 2 Reddit items (fixed):
- 1 from `r/Luxembourg`: the hottest post in time window by `score + num_comments`.
- 1 from the other listed subreddits: hottest post by `score + num_comments`.

2. 3 news-site items:
- If any title matches `Lux_immigration` keywords, 1 slot is forced to that topic first.
- Remaining slots are filled by highest keyword-hit count in title.

Fallback behavior to keep quota stable:
- If Reddit/new items in 6 hours are insufficient, bot relaxes constraints in this order:
- allow previously seen items, then allow older items.
- Goal is to keep 2 Reddit + 3 news whenever possible.

## Daily subreddit diversity rule

- Bot tracks pushed subreddit usage per UTC+8 day in table `subreddit_daily`.
- When choosing the 2nd Reddit item, if fewer than 3 subreddits have been used today, it prefers a subreddit not yet used today.

## Scoring and filtering

There is no source weight anymore.
There is no policy keyword list anymore.

Only title keywords are used (`TOPIC_KEYWORDS`):

- `EU_big`: `eu`, `european commission`, `ukraine`, `russia`
- `Lux_immigration`: `luxembourg`, `residence`, `visa`, `blue card`, `schengen`, `immigration`
- `Quant_fin`: `quant`, `trading`, `crypto`, `volatility`, `ecb`, `rates`, `inflation`, `market`
- `Research_engineering`: `research`, `paper`, `university`, `aerospace`, `wind`, `grid`, `nuclear`, `semiconductor`, `ai`
- `Space`: `nasa`, `esa`, `launch`, `satellite`, `space`, `astronomy`
- `Taiwan_life`: `taiwan`, `lgbtq`, `gender`, `childcare`, `fertility`, `marriage`, `cost of living`, `saving`

News ranking key:
- `topic_hits_in_title` (number of matched keywords in title)
- tie-breaker: newer publish time first

Reddit ranking key:
- `popularity = score + num_comments`
- tie-breaker: newer publish time first

## De-duplication

`seen.sqlite` tables:
- `seen(url, ts)` for URL dedupe
- `seen_title(title, ts)` for normalized title dedupe
- `subreddit_daily(day, subreddit, cnt)` for daily board tracking

## Output format

- Header time uses `UTC+8` and 24-hour format:
- `news feed for Laura at HH:MM`
- Reddit item includes popularity detail:
- `熱度：<score> 讚 + <comments> 留言 = <popularity>`

## Environment variables

- `TG_BOT_TOKEN` (required)
- `TG_CHAT_ID` (required)
- `GUARDIAN_API_KEY` (optional)

## Run

```powershell
python main.py
```
