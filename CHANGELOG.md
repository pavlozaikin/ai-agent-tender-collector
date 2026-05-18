# Changelog

All notable changes to this project are documented here.

## [0.2.1] — 2026-05-18

### Fixed
- Correct `astral-sh/setup-uv` commit SHA in CI workflow (previous SHA was invalid and broke CI).
- Apply `ruff format` to `llm.py` (formatting drift from the security hardening commit).

## [0.2.0] — 2026-05-18

### Added
- **Configurable tender categories** — `config/filters.yaml` restructured into a `domain:` + `categories:` map. Users can now monitor any supply category (not just automotive chemistry) by editing the config file alone — no code changes required. The LLM classify/report system prompts, category labels, and HTML report title are all driven from config. Automotive chemistry remains the shipped default with identical prompt text.
- **Security hardening** — full codebase audit followed by TDD fixes:
  - *Prompt injection*: untrusted tender text wrapped in `<tender_data>` delimiters; anti-injection instruction added to both classify and report system prompts.
  - *LLM output validation*: any `category` value returned by the LLM that is not in the configured set is coerced to `"other"`.
  - *WeasyPrint SSRF*: deny-all `url_fetcher` blocks every `file://`/`http://` resource fetch from the PDF renderer.
  - *Input size limits*: title, description, and item descriptions truncated at 500/4000/500 characters in the LLM input builder.
  - *Secret typing*: API keys and SMTP password changed to `SecretStr`.
  - *Base URL validation*: `PROZORRO_API_BASE` validated to `http/https` scheme only.
  - *Log forging*: `sanitize_log()` strips control characters and ANSI escapes from attacker-influenced strings before they reach the logger.
  - *CI action pinning*: `actions/checkout` and `astral-sh/setup-uv` pinned to commit SHAs.
  - *Cleartext warning*: `SMTP_SECURITY=none` now logs a warning about credential exposure.
- **Comprehensive logging** — structured JSON Lines log file, run-status summary lines, per-tender `tender_fetched` events, and plain-English LLM error classification with fallback model narration.

### Changed
- `config/filters.yaml` format changed from a flat `cpv_prefixes`/`keywords` structure to a `domain:` + `categories:` hierarchy. Users upgrading from 0.1.0 must migrate their `filters.yaml` to the new format (see `config/filters.yaml` for the reference default).

## [0.1.0] — 2025-12-01

Initial release.

- Incremental PROZORRO feed crawl with SQLite cursor.
- Two-stage filtering: CPV + keyword prefilter, then LLM relevance classification.
- LLM support: OpenAI, Anthropic, Google Generative AI, Perplexity with primary→backup fallback.
- HTML + PDF email report in Ukrainian, grouped by category.
- Deadline reminders for previously reported tenders with approaching `tenderPeriod.endDate`.
- LLM usage accounting (`llm_usage` table).
- Docker Compose two-service setup with weekday cron scheduler.
