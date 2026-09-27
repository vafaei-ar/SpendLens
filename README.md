# SpendLens

SpendLens is a local-data-first receipt capture and spending analysis system. A private Telegram bot preserves receipt source evidence locally, extracts structured purchase data with the configured AI provider, validates critical fields with deterministic code, and stores accepted records in local SQLite.

The database and source evidence are the source of truth. The model is not.

## Current milestone

The current branch implements an end-to-end image ingestion path:

1. an allowlisted Telegram user sends a receipt photo or image file
2. SpendLens stores the exact bytes it received in immutable content-addressed storage
3. Gemini transcribes the receipt at ultra-high image resolution
4. Gemini structures that transcription into the strict receipt schema
5. deterministic code recovers clearly labeled summary amounts and validates critical fields
6. clean receipts are auto-saved
7. uncertain receipts enter a Telegram review flow only after an automatic direct-image retry
8. every extraction attempt and final receipt remain auditable

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

## Receipt extraction

The default path is now API-first:

1. Gemini 3.8 Flash transcribes the receipt image using ultra-high media resolution
2. Gemini converts the transcription into a schema-constrained receipt object
3. SpendLens deterministically recovers clearly labeled subtotal/tax/total values from the transcript when needed
4. if the result is still incomplete, SpendLens automatically retries direct image-to-schema extraction
5. SpendLens applies deterministic validation
6. accepted receipt-level fields and item-level data are written to local SQLite

The model is asked to extract every readable purchased item, not just the total. Each line item can retain:

- raw printed description
- printed SKU/product code when visible
- normalized human-readable item name
- brand when supported
- quantity and unit price
- item-level discount
- final charged amount
- broad category such as groceries or household
- subcategory such as fruit, vegetables, meat_seafood, dairy_eggs, cleaning, or toiletries

This item-level history is intended to support later deterministic analytics such as grocery spend, fruit spend, frequently purchased products, and unusual items relative to the user's own purchase history.

Set the required API configuration:

~~~
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.8-flash
SPENDLENS_DEFAULT_CURRENCY=USD
~~~

The default currency is only used when the receipt/model omits a currency value. It is configurable and should match the user's normal receipt environment.

### Optional local fallback

Local OCR is disabled by default:

~~~
LOCAL_FALLBACK_ENABLED=false
~~~

If explicitly enabled, SpendLens can fall back to PaddleOCR-VL/dots.ocr through MLX plus a local Ollama text structurer after a Gemini result fails validation. This path requires the optional mac dependencies and Ollama:

~~~
python -m pip install -e ".[mac]"
ollama pull qwen3:4b-instruct
LOCAL_FALLBACK_ENABLED=true
~~~

### Remove local model caches

If local fallback is disabled, the MLX OCR model caches are not needed. Inspect the Hugging Face cache first:

~~~
hf cache ls --sort size
~~~

Preview removal:

~~~
hf cache rm model/mlx-community/PaddleOCR-VL-1.6-4bit \
  model/mlx-community/dots.ocr-4bit --dry-run
~~~

Then remove them:

~~~
hf cache rm model/mlx-community/PaddleOCR-VL-1.6-4bit \
  model/mlx-community/dots.ocr-4bit -y
~~~

If Qwen3 was installed only for SpendLens local fallback, it can also be removed from Ollama:

~~~
ollama rm qwen3:4b-instruct
~~~

SpendLens source images, SQLite data, and audit history are not affected by deleting model caches.

### Cloud privacy

Receipt images are sent to the Gemini API in the default configuration. As of September 2026, Google's Gemini API documentation says free-tier content may be used to improve Google products, while paid-tier content is not used for that purpose. Financial receipts should use the paid API tier when that privacy distinction matters.


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
- `GEMINI_API_KEY`

Then run:

```bash
spendlens-bot
```

The bot uses long polling. Unauthorized numeric Telegram user IDs are ignored.

## Run SpendLens as a macOS service

On macOS, SpendLens can run as a user LaunchAgent under launchd. After the one-time install, it no longer depends on an open Terminal window and starts automatically when you log in.

From the SpendLens repository:

```bash
python -m pip install -e ".[dev]"
spendlens-service install
```

The installer records the exact Python executable and project directory you used, reads secrets from the existing project `.env`, writes a LaunchAgent to `~/Library/LaunchAgents/com.spendlens.bot.plist`, and stores service logs under `~/Library/Logs/SpendLens/`.

Management commands:

```bash
spendlens-service status
spendlens-service start
spendlens-service stop
spendlens-service restart
spendlens-service logs
spendlens-service logs -n 200
spendlens-service uninstall
```

The service uses `KeepAlive`, so launchd restarts it if it crashes. `stop` unloads the LaunchAgent so it stays stopped until `start` or `restart`.

Telegram commands remain responsive while receipt extraction is running. Receipt processing is serialized so only one receipt pipeline runs at a time; additional receipts are queued and acknowledged immediately.

If you change Python environments or move the repository, run `spendlens-service install` again from the new environment/location.

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

## Telegram inspection commands

Routine inspection is available directly through the authorized Telegram bot. You do not need to open SQLite for normal checks.

```
/status
/last
/recent
/extractions
/reviews
/receipt 12
/source 7
/help
```

- `/status` shows source, ingestion, extraction, saved-receipt, and open-review counts plus the configured model.
- `/last` traces the newest upload from stored source through AI extraction, validation, saved receipt, or review state.
- `/recent [n]` shows recent canonical receipts. The default is 10 and the maximum is 20.
- `/extractions [n]` shows recent AI extraction attempts, including parse/provider failures.
- `/reviews` shows open review sessions for the authorized Telegram user.
- `/receipt [id]` shows canonical receipt details and extracted line items. Without an ID it shows the latest receipt.
- `/source [id]` sends the exact stored source back as a Telegram document. Without an ID it sends the latest source. It is sent as a document to avoid an additional photo-compression pass.
- `/help` shows the command list.
- SpendLens registers these commands with Telegram on startup, so they appear in the bot's command/menu button for authorized users.

All inspection commands use the same numeric Telegram user allowlist as receipt ingestion.

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
