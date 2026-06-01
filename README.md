# Reddit Intelligence Bot

A powerful Discord bot that transforms any Reddit user, post, comment, or thread into a detailed intelligence report.

## Features

- **User Analysis** — Full profile, karma breakdown, activity patterns, community participation
- **Comment Analysis** — Context, depth, sentiment, toxicity estimate
- **Post Analysis** — Engagement metrics, comment velocity, AI summary
- **Thread Intelligence** — Full comment tree, key arguments, community consensus
- **Risk Assessment** — 10-point scoring system (spam/bot/genuine detection)
- **AI-Powered** — Sentiment analysis, tone classification, behavioral profiling

## Data Sources

- **Primary**: Reddit's public `.json` endpoints with `curl_cffi` browser-grade TLS impersonation
- **Fallback**: Reddit Official API via `AsyncPRAW`

## Setup

### 1. Clone & Install

```bash
git clone <repo>
cd redditOSITN
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your tokens
```

You'll need:
- A [Discord Bot Token](https://discord.com/developers/applications)
- Reddit API credentials from [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) (for fallback)
- An OpenAI API key (for AI analysis features)

### 3. Invite the Bot

Use the Discord Developer Portal to generate an invite URL with:
- `bot` scope
- `applications.commands` scope
- Permissions: `Send Messages`, `Embed Links`, `Read Message History`

### 4. Run

```bash
python -m bot.main
```

## Commands

| Command | Description |
|---|---|
| `/user <username>` | Full user intelligence report |
| `/analyze <username>` | Alias for `/user` |
| `/risk <username>` | Risk assessment only |
| `/comment <url>` | Comment analysis |
| `/post <url>` | Post analysis |
| `/thread <url>` | Full thread intelligence |

## Architecture

```
Discord Bot
    │
   
Command Router (Slash Commands)
    │
   
Reddit Fetch Layer
    ├── Primary: JSON Endpoint (curl_cffi + TLS impersonation)
    └── Fallback: AsyncPRAW (Reddit API)
    │
   
Analysis Engine
    ├── User / Comment / Post / Thread Analyzers
    └── Risk Scorer
    │
   
AI Summarizer (OpenAI)
    │
   
Discord Embed Builder
```

## Configuration

| Variable | Description | Default |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token | *required* |
| `DISCORD_GUILD_ID` | Guild ID for instant slash commands (blank = global) | `""` |
| `REDDIT_CLIENT_ID` | Reddit app client ID (fallback) | *required* |
| `REDDIT_CLIENT_SECRET` | Reddit app client secret (fallback) | *required* |
| `OPENAI_API_KEY` | OpenAI API key for AI features | *required* |
| `OPENAI_MODEL` | LLM model to use | `gpt-4o-mini` |
| `MAX_FETCH_ITEMS` | Max posts/comments per analysis | `500` |
| `CACHE_BACKEND` | `memory` or `sqlite` | `memory` |
| `CACHE_TTL` | Cache duration in seconds | `600` |

## License

MIT
