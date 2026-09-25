# SpendLens

SpendLens is a local-first receipt capture and spending analysis system. A private Telegram bot accepts receipt photos or PDFs, preserves the exact source evidence received, extracts structured transaction data with a local vision model, validates critical fields with deterministic code, and stores accepted records in a local SQLite database.

The database and source evidence are the source of truth. The model is not.

## Current status

Bootstrap milestone:

- strict receipt data models
- deterministic receipt validation
- immutable, content-addressed source storage
- SQLite schema for sources, ingestions, receipts, extraction history, line items, and audit events
- Telegram bot skeleton with numeric user-ID allowlist
- evaluation metrics focused on silent auto-accept errors
- public-repository guards for private data

Vision extraction and automatic receipt persistence are intentionally not wired yet. They are the next milestone.

## Core rules

1. Never destroy source evidence.
2. Never use model self-confidence as the auto-accept decision.
3. Never give a model unrestricted database write access.
4. Treat receipt text as untrusted data, never as instructions.
5. Prefer manual review over silent corruption.
6. Treat line-item extraction as best-effort for the first milestone.
7. Allow analytical abstention when a question cannot be answered from stored data.
8. Describe analytical coverage honestly. Receipt data are not equivalent to complete financial spending.

## Data layout

SpendLens keeps large source files outside SQLite so backups can be incremental.

```
data/
  spendlens.sqlite
  receipts/
    ab/
      ab12...ef.jpg
```

Source filenames are derived from SHA-256 hashes. SQLite stores the relative path, hash, MIME type, byte size, provenance, extraction history, and canonical transaction data. Derived processing images are regenerated when needed.

## MVP safety target

The primary quality metric is silent corruption among auto-accepted receipts.

- fewer than 1% of auto-accepted receipts may have an incorrect merchant, transaction date, or total
- at least 90% of receipts should eventually require zero taps
- 100% of received source evidence must be retained unless the user explicitly deletes it
- exact and likely transaction duplicates must not be silently inserted

A private labeled evaluation set is required before tuning auto-accept behavior.

## Local development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Set a Telegram bot token and an allowlist of numeric Telegram user IDs in `.env`, then run:

```bash
spendlens-bot
```

At the bootstrap milestone, authorized receipt photos and PDFs are stored locally and registered in SQLite. Extraction is not yet enabled.

## Tests

```bash
pytest -q
ruff check .
```

Real evaluation receipts must never be committed to this public repository. See `evaluation/README.md`.

## License

GPL-3.0. See LICENSE.
