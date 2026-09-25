# SpendLens

SpendLens is a local-first receipt capture and spending analysis system. A private Telegram bot preserves receipt source evidence, extracts structured purchase data with a local vision model, validates critical fields with deterministic code, and stores accepted records in local SQLite.

The database and source evidence are the source of truth. The model is not.

## Current milestone

The current branch implements an end-to-end image ingestion path:

1. an allowlisted Telegram user sends a receipt photo or image file
2. SpendLens stores the exact bytes it received in immutable content-addressed storage
3. a local Ollama vision model extracts a strict `ReceiptExtraction` object
4. deterministic code checks critical fields and likely duplicates
5. clean receipts are auto-saved
6. uncertain receipts enter a Telegram review flow
7. every extraction attempt and final receipt remain auditable

PDFs are already preserved, but PDF extraction is intentionally deferred.

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

Large source files stay outside SQLite so encrypted incremental backups can copy only new evidence.

```
data/
  spendlens.sqlite
  receipts/
    ab/
      ab12...ef.jpg
```

Source filenames are derived from SHA-256 hashes. SQLite stores the relative path, hash, MIME type, Telegram provenance, extraction attempts, review state, audit history, and canonical transaction data.

Derived processing images are not retained. They can be regenerated from the source.

## Local model

The default configuration uses Ollama with `qwen3-vl:4b`.

Install Ollama separately, then pull the model:

```bash
ollama pull qwen3-vl:4b
```

SpendLens uses Ollama's local `/api/chat` endpoint with a JSON schema generated directly from the Pydantic receipt model.

The model can be changed in `.env` without changing the database schema.

## Local development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Set:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`

Then run:

```bash
spendlens-bot
```

The bot uses long polling. Unauthorized numeric Telegram user IDs are ignored.

## Review flow

A valid high-confidence-by-rules receipt requires no tap.

If validation fails, SpendLens sends a review message such as:

```
Review needed #12
Merchant: Example Market
Date: 2026-09-25
Subtotal: 10.00
Tax: 0.60
Total: 11.60
```

Reply directly to that bot message with a correction:

```
total 10.60
```

Supported correction fields include merchant, date, time, subtotal, tax, tip, fees, discount, total, currency, and transaction type.

If the corrected record passes deterministic checks, SpendLens saves it immediately. For cases that still require explicit human judgment, including a suspected duplicate, use:

```
/accept 12
```

To discard the structured record while retaining the source evidence:

```
/discard 12
```

## Duplicate protection

SpendLens currently has two layers:

1. exact-source matching using SHA-256
2. transaction-level matching using normalized merchant, currency, exact total, and a date window of plus or minus one day

A likely transaction duplicate never auto-saves.

## MVP safety target

The primary metric is silent corruption among auto-accepted receipts.

- fewer than 1% of auto-accepted receipts may have an incorrect merchant, transaction date, or total
- at least 90% of receipts should eventually require zero taps
- 100% of received source evidence must be retained unless the user explicitly deletes it
- exact and likely transaction duplicates must not be silently inserted

A private human-labeled evaluation set is required before changing the acceptance threshold based on intuition.

## Tests

```bash
pytest -q
ruff check .
```

CI also blocks common private artifacts such as receipt images, databases, environment files, and private evaluation data.

Real evaluation receipts must never be committed to this public repository. See `evaluation/README.md`.

## License

GPL-3.0. See `LICENSE`.
