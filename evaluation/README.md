# Private evaluation corpus

SpendLens must be evaluated against human-labeled receipt truth before the auto-accept policy is loosened.

Real receipts are private. The entire working corpus stays under `evaluation/private/`, which is excluded from Git.

## Case structure

Each case contains the exact evaluation source plus metadata and manually verified truth:

```
evaluation/private/
  r001-photo/
    source.jpg
    metadata.json
    truth.json
  r001-file/
    source.jpg
    metadata.json
    truth.json
```

`metadata.json` records:

- unique `case_id`
- `receipt_key`, shared by multiple representations of the same physical receipt
- source delivery variant
- SHA-256 of the exact evaluation bytes
- MIME type
- optional notes

The `receipt_key` is important. To test Telegram compression, label the same physical receipt twice, for example:

- `r001-photo` with variant `telegram_photo`
- `r001-file` with variant `telegram_file`

Both cases use the same `receipt_key`, such as `r001`.

## Add a case

You can enter human-verified fields interactively:

```bash
python scripts/add_eval_case.py /path/to/receipt.jpg \
  --case-id r001-photo \
  --receipt-key r001 \
  --variant telegram_photo
```

Or prepare a verified `ReceiptExtraction` JSON and pass it directly:

```bash
python scripts/add_eval_case.py /path/to/receipt.jpg \
  --case-id r001-file \
  --receipt-key r001 \
  --variant telegram_file \
  --truth-json /path/to/r001-truth.json
```

The case creator copies the source into the private corpus, renames it generically as `source.<ext>`, calculates SHA-256, and verifies that merchant, date, total, and currency exist in the gold-standard truth.

## Run an experiment

Make sure Ollama is running and the configured model is installed.

```bash
python scripts/run_eval_corpus.py
```

By default this uses:

```
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3-vl:4b
OLLAMA_TIMEOUT_SECONDS=180
```

Each run writes ignored local output under:

```
evaluation/results/<timestamp>-<model>/
  records.jsonl
  report.json
  report.md
```

The runner is sequential by design. GPU concurrency would make timing and memory behavior harder to interpret.

## Primary safety metric

The main metric is:

```
false_auto_accept_rate =
incorrect auto-accepted receipts / all auto-accepted receipts
```

A receipt is critically correct only when all three fields are correct:

- merchant
- transaction date
- total

The initial MVP requirement remains below 1% false auto-accept among auto-accepted receipts.

The report also includes:

- prediction rate
- merchant accuracy
- date accuracy
- total accuracy
- critical receipt accuracy
- auto-accept rate
- manual-review rate
- silent auto-accept case IDs
- blocking validation reasons
- metrics by source-delivery variant
- paired Telegram photo-versus-file comparison

## Corpus composition

Do not collect 100 easy receipts. That produces a misleading benchmark.

The first corpus should deliberately include:

- long grocery receipts
- tiny print
- low light
- skew and perspective
- crumpled paper
- coupons
- member discounts
- weighted produce
- deposits and fees
- refunds and returns
- receipts with missing subtotal or tax
- multiple receipts from the same merchant
- unfamiliar merchants
- ambiguous dates
- Telegram photo and file versions of the same physical receipt

The corpus should represent the receipts SpendLens will actually receive, while intentionally oversampling known failure modes.

## Labeling rule

The truth file is human evidence, not model output. Verify it against the receipt itself.

Do not copy a model extraction into `truth.json` and then correct only obvious mistakes. That creates confirmation bias and weakens the benchmark.
