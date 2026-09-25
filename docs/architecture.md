# SpendLens architecture

## Boundary

SpendLens is a local spending-data system with AI-assisted extraction and interpretation. The AI is not the database and does not control canonical financial records directly.

## Ingestion

1. Telegram receives a receipt photo or PDF.
2. The bot accepts input only from an allowlisted numeric Telegram user ID.
3. The exact bytes received by SpendLens are hashed with SHA-256.
4. The source is stored immutably under `data/receipts/<hash-prefix>/<hash>`.
5. SQLite records provenance for every ingestion, including repeated delivery of the same exact source.
6. A local vision model will later produce a strict `ReceiptExtraction` object.
7. Deterministic validation decides automatic acceptance or manual review.
8. Human corrections create audit events rather than overwriting provenance.

Telegram photo uploads may be recompressed by Telegram. SpendLens therefore defines source evidence as the exact bytes received by the application. The labeled evaluation set must compare Telegram photo delivery with document/file delivery on difficult long receipts.

## Automatic acceptance

The acceptance gate never uses model self-reported confidence.

Blocking conditions in the bootstrap validator include:

- unusable merchant
- implausible future date
- invalid purchase/refund sign
- missing subtotal for arithmetic verification
- receipt-level arithmetic mismatch
- likely duplicate transaction

Line-item reconciliation is warning-only.

The primary safety metric is false auto-accept rate among receipts that the system chose to accept without review.

## Duplicate detection

Two levels are planned:

1. Exact-source duplicate: identical SHA-256.
2. Transaction-level duplicate: normalized merchant + same/similar total + transaction date within a small tolerance.

The second level must require review rather than silently inserting a second transaction.

## Persistence

SQLite stores structured records and audit history. Source images and PDFs remain separate content-addressed files so incremental encrypted backups do not recopy an ever-growing database BLOB.

Recommended MVP protection is full-disk or encrypted-volume storage plus encrypted backups. Application-level source encryption can be added later if the threat model requires separate key management.

## Analytics

Deterministic code calculates totals, trends, groupings, and charts. A language model may map a user question into a constrained `QueryDecision`. The query schema includes explicit `unsupported` and `needs_clarification` states. The model does not emit arbitrary write SQL.

Answers must describe coverage accurately. Until external transactions are imported, reports refer to purchases recorded in SpendLens, not total personal spending.

## Evaluation

A private human-labeled corpus is a first-class project asset. Every model, prompt, quantization, preprocessing, or validation change should be compared on the same corpus.

Critical correctness means merchant, transaction date, and total are all correct. Silent auto-accept errors are more important than raw extraction accuracy or auto-accept rate.
