# SpendLens architecture

## Boundary

SpendLens is a local spending-data system with AI-assisted extraction and interpretation. The AI is not the database and never controls canonical financial records directly.

## Image ingestion

1. Telegram receives a receipt photo or image document.
2. The bot accepts input only from an allowlisted numeric Telegram user ID.
3. The exact bytes received by SpendLens are hashed with SHA-256.
4. The source is stored immutably under `data/receipts/<hash-prefix>/<hash>`.
5. SQLite records every ingestion, including repeated delivery of the same source.
6. An Ollama vision model produces a strict `ReceiptExtraction` object.
7. The extraction response is persisted before any decision is made.
8. Deterministic validation decides automatic acceptance or manual review.
9. Human corrections operate on a persisted review session.
10. The final canonical receipt links back to the source and extraction attempt.

PDF sources are preserved but not yet sent to the vision model.

Telegram photo uploads may be recompressed by Telegram. SpendLens therefore defines source evidence as the exact bytes received by the application. The labeled evaluation set should compare Telegram photo delivery with file/document delivery on difficult long receipts.

## Extraction contract

The local model receives a JSON schema generated from the Pydantic `ReceiptExtraction` model. Critical fields are nullable so the model can abstain instead of inventing values.

Receipt text is explicitly treated as untrusted input. Printed instructions, URLs, prompts, or commands inside a receipt are data and must never alter application behavior.

The model's own confidence is not stored as an acceptance signal.

## Automatic acceptance

Current blocking conditions include:

- missing or unusable merchant
- missing or implausible future date
- missing currency
- missing total
- invalid purchase/refund sign
- missing subtotal for arithmetic verification
- receipt-level arithmetic mismatch
- likely duplicate transaction

Line-item reconciliation remains warning-only because real receipts contain coupons, weighted items, deposits, and other conventions that prevent reliable exact summation.

The primary safety metric is the false auto-accept rate among receipts that the system chose to accept without review.

## Duplicate detection

Two layers are implemented:

1. Exact-source duplicate: identical SHA-256.
2. Transaction-level duplicate: normalized merchant + currency + exact total + transaction date within plus or minus one day.

The second layer forces manual review rather than silently adding another transaction.

Future work may add OCR-text similarity or perceptual image matching, but these are not required for the MVP.

## Review flow

A failed deterministic check creates an open review session in SQLite. The bot displays the proposed merchant, date, subtotal, tax, total, currency, and validation failures.

The user can reply to that review message with a field correction such as `total 57.82`. SpendLens re-runs duplicate detection and validation after every correction.

If the corrected record passes, it saves automatically. The user can explicitly use `/accept <id>` after inspecting the source when a deterministic blocker should be overridden, for example an intentional duplicate. `/discard <id>` discards the proposed structured record but retains the immutable source.

## Persistence

SQLite stores structured records, extraction attempts, reviews, provenance, and audit history. Source images and PDFs remain separate content-addressed files so incremental encrypted backups do not recopy an ever-growing database BLOB.

Recommended MVP protection is full-disk or encrypted-volume storage plus encrypted backups. Application-level source encryption can be added later if the threat model requires separate key management.

## Analytics

Deterministic code will calculate totals, trends, groupings, and charts. A language model may later map a user question into a constrained `QueryDecision`. The query schema includes explicit `unsupported` and `needs_clarification` states. The model does not emit arbitrary write SQL.

Until external transactions are imported, reports must refer to purchases recorded in SpendLens rather than total personal spending.

## Evaluation

A private human-labeled corpus is a first-class project asset. Every model, prompt, quantization, preprocessing, or validation change should be compared on the same corpus.

Critical correctness means merchant, transaction date, and total are all correct. Silent auto-accept errors are more important than raw extraction accuracy or auto-accept rate.
