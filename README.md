# Tender Agent — PROZORRO Automotive-Chemistry Monitor

An AI agent that crawls the Ukrainian public procurement platform **PROZORRO** daily, identifies tenders related to automotive chemicals, and delivers a formatted report by email — with deadline reminders for tenders already on your radar.

![CI](https://github.com/pavlozaikin/ai-agent-tender-collector/actions/workflows/ci.yml/badge.svg)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

---

## Features

- **Incremental crawl** — processes only tenders modified since the last run; never re-reports the same tender twice.
- **Two-stage filtering** — a fast CPV + keyword prefilter reduces the LLM call volume; the LLM makes the final relevance decision.
- **LLM classification** — supports OpenAI, Anthropic, Google Generative AI, and Perplexity, with automatic fallback to a backup model.
- **Deadline reminders** — highlights previously reported tenders whose submission deadline (`tenderPeriod.endDate`) falls within the next `DEADLINE_REMINDER_DAYS` days (default: 3). These appear as a prominent section at the top of both the HTML email and the PDF attachment.
- **PDF attachment** — every report email includes the same content as a PDF for archiving.
- **Weekday scheduler** — built-in cron runs at 08:00 Kyiv time, Monday through Friday (`0 8 * * 1-5`); no external cron daemon needed.
- **LLM usage accounting** — every API call is logged to a `llm_usage` table (tokens + estimated cost).
- **Docker-first** — two-service Compose setup with a dedicated volume-owner container for safe SQLite access.

**Monitored categories:**

| Category | Examples |
|---|---|
| Coolants and antifreeze | Radiator coolants, antifreeze concentrates |
| Brake fluids | DOT 3, DOT 4, DOT 5.1 |
| Windshield washer fluids | Summer / winter screen wash |
| Motor, industrial, and base oils | Engine oil, hydraulic oil, base stocks |

---

## Architecture

The pipeline executes as a single LangGraph graph. One run = one report cycle.

```
crawl ──→ prefilter ──→ classify ──→ dedupe ──→ deadline_check ──→ render ──→ notify ──→ persist
  │           │              │           │              │              │          │          │
PROZORRO   broad          LLM —       drop        warn about       HTML +     SMTP      save
  feed    filter        final        already      expiring         PDF       email     cursor +
         (CPV +        relevance     reported     previously                           sent IDs
        keywords)      decision      tenders       tenders
```

- **crawl** — incremental walk of the PROZORRO `/tenders` feed from the stored cursor.
- **prefilter** — deliberately broad CPV-group + keyword filter (`config/filters.yaml`). Reduces LLM traffic, not a substitute for it.
- **classify** — LLM makes the authoritative relevance decision for each candidate.
- **dedupe** — discards tenders already sent in previous runs (SQLite lookup).
- **deadline_check** — queries stored tenders for upcoming deadlines and surfaces them as reminders.
- **render** — builds the HTML report and PDF attachment.
- **notify** — sends the email via SMTP.
- **persist** — writes newly reported tender IDs and the updated feed cursor to SQLite.

---

## Requirements

**Docker path (recommended)**

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/) v2+

**Local path**

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/) package manager

**Both paths require**

- An API key for at least one supported LLM provider (OpenAI, Anthropic, Google, or Perplexity).
- SMTP access (Gmail App Password or a corporate mail server).

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/pavlozaikin/ai-agent-tender-collector.git
cd ai-agent-tender-collector

# 2. Create the environment file
cp .env.example .env
# Edit .env — fill in LLM API keys and SMTP credentials.

# 3. Create the recipients file (must exist before docker compose up)
cp config/recipients.example.yaml recipients.yaml
# Edit recipients.yaml — add to/cc/bcc addresses.

# 4. Start the scheduler
docker compose up -d
```

> **Important:** Create `recipients.yaml` before running `docker compose up`. If the file is absent, Docker will create a directory at that path instead of mounting a file, and the agent will fail to read recipients.

---

## Configuration

Copy `.env.example` to `.env` and fill in the values. The agent re-reads `recipients.yaml` before every run, so recipient changes take effect without a restart.

### LLM Models

| Variable | Default | Description |
|---|---|---|
| `LLM_CLASSIFY_PRIMARY` | `openai:gpt-5.4-mini` | Primary model for relevance classification. High-volume; use a "mini"/"nano" model to control cost. |
| `LLM_CLASSIFY_BACKUP` | `openai:gpt-5.4-nano` | Fallback if the primary classify model fails. |
| `LLM_REPORT_PRIMARY` | `openai:gpt-5.5` | Primary model for writing tender summaries. Low-volume; quality matters more than cost. |
| `LLM_REPORT_BACKUP` | `openai:gpt-5.4-mini` | Fallback if the primary report model fails. |

Model format: `provider:model-id`. Verify model IDs against your provider's current catalog before use. Supported providers: `openai`, `anthropic`, `google_genai`, `perplexity`.

### Provider API Keys

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key. Required if using any `openai:` model. |
| `ANTHROPIC_API_KEY` | Anthropic API key. Required if using any `anthropic:` model. |
| `GOOGLE_API_KEY` | Google Generative AI key. Required if using any `google_genai:` model. |
| `PERPLEXITY_API_KEY` | Perplexity API key. Required if using any `perplexity:` model. |

Only the keys for providers you actually use are required.

### SMTP

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname. |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_SECURITY` | `starttls` | Connection security: `starttls`, `ssl`, or `none`. |
| `SMTP_USERNAME` | _(required)_ | SMTP login username. |
| `SMTP_PASSWORD` | _(required)_ | SMTP password or App Password. |
| `SMTP_FROM` | _(defaults to username)_ | Sender address shown in the email. |

**Gmail:** use `smtp.gmail.com`, port `587`, security `starttls`, and a [Gmail App Password](https://support.google.com/accounts/answer/185833) — not your account password.

### Schedule

| Variable | Default | Description |
|---|---|---|
| `SCHEDULE_CRON` | `0 8 * * 1-5` | Cron expression for the scheduled run. Default: 08:00 Mon–Fri. |
| `TIMEZONE` | `Europe/Kyiv` | Timezone for interpreting the cron expression. |
| `SEND_WHEN_EMPTY` | `false` | Send an email even when there are no new tenders or reminders. |
| `DEADLINE_REMINDER_DAYS` | `3` | Warn about previously reported tenders whose submission deadline is within this many days. |

### Crawl

| Variable | Default | Description |
|---|---|---|
| `PROZORRO_API_BASE` | `https://public-api.prozorro.gov.ua/api/2.5` | PROZORRO API base URL. |
| `CRAWL_LOOKBACK_DAYS` | `1` | On the very first run (no cursor stored), start crawling tenders modified within this many days back. Subsequent runs always pick up from the stored cursor. |
| `REQUEST_TIMEOUT_SECONDS` | `30` | HTTP timeout per request to the PROZORRO API. |
| `MAX_RETRIES` | `4` | Number of retry attempts on transient API errors. |

### Paths and Logging

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `data` | Directory for the SQLite database and saved reports. Set to `/data` inside Docker. |
| `FILTERS_PATH` | `config/filters.yaml` | Path to the CPV + keyword prefilter configuration. |
| `RECIPIENTS_PATH` | `recipients.yaml` | Path to the recipients file. |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_FILE_ENABLED` | `true` | Write a structured JSON Lines log to `{DATA_DIR}/tender-agent.jsonl`. Set to `false` to disable the file sink. |

---

## Logging

Every run produces two interleaved output streams on the console:

- **Technical log lines** — standard structured log messages (level, logger name, message).
- **Human-readable summary lines** — plain-English status updates tagged `audience="human"`, designed to be readable at a glance without log-parsing skills.

### JSON Lines log file

When `LOG_FILE_ENABLED=true` (the default), every log line is also written to `data/tender-agent.jsonl` as a single JSON object. The file sits alongside the database and reports inside `DATA_DIR`. Rotation kicks in at 5 MB with 5 backups kept.

This file is useful for post-run audits, feeding into log aggregators, or debugging a specific run without re-running the pipeline.

### Run-status summaries

Each run ends with one of the following plain-English summary lines printed to the console:

```
Run finished successfully. Crawled 120 tender(s), 3 new. Email sent.
Run finished — no email sent. Crawled 120 tender(s) but found nothing new to report.
Run finished with degraded results — the AI model failed during the run. Crawled 120 tender(s). Report may be incomplete.
```

### Error classification

LLM API errors (rate limits, quota exceeded, authentication failure, timeouts, connection errors) are classified and logged with a plain-English explanation before the fallback model is tried. Example:

```
The OpenAI API is temporarily rate-limited — too many requests. Will retry with the backup model.
```

### Per-tender detail

Each tender fetched from the PROZORRO feed is logged at `INFO` level (`tender_fetched` event with tender `id` and `title`). Set `LOG_LEVEL=DEBUG` to see the full response payload.

---

## Usage

### Docker (recommended)

```bash
# Start the built-in weekday scheduler (detached)
docker compose up -d

# View live logs
docker compose logs -f

# Run a single cycle immediately (for testing)
docker compose exec tender-agent tender-agent run --now

# Run a dry-run cycle (full pipeline, no email sent, no state saved)
docker compose exec tender-agent tender-agent run --now --dry-run

# Check LLM usage statistics
docker compose exec tender-agent tender-agent stats --usage

# Health check
docker compose exec tender-agent tender-agent healthcheck

# Open the SQLite database for admin queries
docker compose exec db sqlite3 /data/tender_agent.db

# Stop all services
docker compose down
```

> **Operational note:** Run `docker compose up -d` once and leave it running. The container manages the built-in weekday scheduler automatically. **Do not trigger manual `--now` runs each day in production.** Manual runs advance the feed cursor, so the next scheduled run will see fewer new tenders than expected.

### Building and pushing the image

```bash
docker build -t ghcr.io/<org>/tender-agent:1.0.0 .
docker push ghcr.io/<org>/tender-agent:1.0.0
```

### Local (uv)

```bash
uv sync

uv run tender-agent run --now            # one full cycle with email
uv run tender-agent run --now --dry-run  # full pipeline, no email, no state changes
uv run tender-agent schedule             # start the blocking scheduler
uv run tender-agent stats --usage        # LLM usage statistics
uv run tender-agent healthcheck          # connectivity health check
```

`--dry-run` runs the complete pipeline and saves the HTML report to `data/reports/`, but does not send email or modify the database — safe for testing configuration changes.

> The first run crawls tenders from the past `CRAWL_LOOKBACK_DAYS` days (default: 1). Subsequent runs process only tenders new since the last cursor position.

---

## Development

```bash
uv sync

uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy src         # type-check (strict)
uv run pytest           # unit + integration tests with coverage

pre-commit install       # install pre-commit hooks
```

CI runs lint, type-check, and tests on every push and pull request (`.github/workflows/ci.yml`).

---

## Project Structure

```
src/tender_agent/
  settings.py          Configuration (loaded from .env)
  state.py             Domain models and LangGraph state
  storage.py           SQLite: cursor, deduplication, llm_usage
  filters.py           Broad prefilter (CPV groups + keywords)
  llm.py               LLM layer: provider routing, fallback, token accounting
  pipeline.py          LangGraph pipeline (nodes + graph definition)
  main.py              CLI entry points and scheduler
  prozorro/            PROZORRO API client and response models
  emailer/             SMTP sender, recipient loader, HTML/PDF report renderer

config/
  filters.yaml         CPV groups and keywords for the prefilter stage
  recipients.example.yaml  Template for recipients.yaml

.github/workflows/
  ci.yml               GitHub Actions CI pipeline
```

---

## Roadmap

- **Additional procurement platforms** — Zakupki.prom.ua, SmartTender, e-tender for private-sector tenders. The architecture already isolates the data source inside the `prozorro/` module, making new adapters straightforward.
- **Deadline reminders** — implemented: tenders with approaching submission deadlines are surfaced at the top of each report.
- **Configurable tender categories** — extend coverage to other supply categories without code changes.
