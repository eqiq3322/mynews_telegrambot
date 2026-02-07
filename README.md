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

- News sites lookback: last `6` hours.
- Reddit lookback: last `48` hours.

## Push composition per run

Each run targets up to 5 items.

1. 2 Reddit items (fixed).
- 1 from `r/Luxembourg`: hottest in window by `score + num_comments`.
- 1 from the other listed subreddits: hottest by `score + num_comments`.

2. 3 news-site items.
- If any title matches `Lux_immigration`, 1 slot is forced to this topic first.
- Remaining slots are filled by highest keyword-hit count in title.

Fallback behavior to keep quota stable:
- If candidates are insufficient under primary windows (Reddit 48h, news 6h), bot relaxes constraints in this order:
- allow previously seen items, then allow older items.
- Goal is to keep 2 Reddit + 3 news whenever possible.

## Daily subreddit diversity rule

- Bot tracks pushed subreddit usage per UTC+8 day in table `subreddit_daily`.
- When choosing the 2nd Reddit item, if fewer than 3 subreddits have been used today, it prefers a subreddit not yet used today.

## Scoring and filtering

There is no source weight.
There is no policy keyword list.
Only title keywords are used (`TOPIC_KEYWORDS`).

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
- `heat: <score> upvotes + <comments> comments = <popularity>`

## Environment variables

- `TG_BOT_TOKEN` (required)
- `TG_CHAT_ID` (required)
- `GUARDIAN_API_KEY` (optional)

## Prerequisites outside this repo

Before running this project, you must complete the following external setup:

1. Create a Telegram bot.
- Open Telegram and message `@BotFather`.
- Create a bot and get the bot token (`TG_BOT_TOKEN`).

2. Get your target chat ID.
- Add your bot to your chat/group/channel.
- Send at least one message in that chat.
- Get the chat ID and set it as `TG_CHAT_ID`.

3. (Optional) Get a Guardian API key.
- Register at the Guardian Open Platform.
- Create an API key and set `GUARDIAN_API_KEY`.
- If omitted, Guardian source is skipped.

4. Configure GitHub Actions secrets (if using workflow).
- Add `TG_BOT_TOKEN`, `TG_CHAT_ID`, and optionally `GUARDIAN_API_KEY`
- Go to repository settings: `Settings -> Secrets and variables -> Actions`.

## Customization options

You can customize behavior directly in `main.py`:

- Source list:
- `RSS_SOURCES` for news websites
- `REDDIT_SUBREDDITS` for Reddit boards

- Keyword filtering:
- `TOPIC_KEYWORDS` to define topic categories and matching keywords
- `Lux_immigration` is treated as priority topic in current logic

- Time windows:
- `LOOKBACK_HOURS_NEWS` for news-site recency
- `LOOKBACK_HOURS_REDDIT` for Reddit recency

- Output mix and size:
- `TOTAL_PUSH_COUNT`, `FIXED_REDDIT_COUNT`, `OTHER_NEWS_COUNT`

- Ranking rules:
- News: keyword-hit count in title
- Reddit: `score + num_comments`

## Free vs paid APIs

- Current default setup is based on free/public endpoints:
- RSS feeds
- Reddit public JSON endpoints
- Guardian API key (free tier available)

- If you are willing to pay, you can extend this project with premium sources, for example:
- paid news APIs with richer metadata
- enterprise social listening APIs
- paid LLM-based relevance/ranking layers

The code is intentionally simple so you can replace fetch and ranking modules with paid integrations.

## Run

```powershell
python main.py
```
