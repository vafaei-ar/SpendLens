# Private evaluation corpus

The extraction system must be evaluated against human-labeled receipt truth before auto-accept behavior is tuned.

Real receipts are private and must not be committed to this public repository.

Recommended local layout:

```
evaluation/private/
  receipt_001/
    source.jpg
    truth.json
  receipt_002/
    source.pdf
    truth.json
```

The truth JSON should follow `spendlens.models.ReceiptExtraction` and must be manually verified against the source.

For model experiments, write a JSONL file with one `EvaluationCase` per line. If extraction fails to produce a valid schema, set `prediction` to null.

Run:

```bash
python scripts/evaluate_predictions.py evaluation/predictions/run.jsonl
```

Primary metric:

```
false_auto_accept_rate =
incorrect auto-accepted receipts / all auto-accepted receipts
```

A receipt is critically correct only when merchant, transaction date, and total are all correct. The initial MVP target is a false auto-accept rate below 1%.

Keep the first corpus deliberately diverse. Include long grocery receipts, discounts, weighted items, returns, low-light photographs, skewed images, small-print receipts, Telegram photo uploads, and document/file uploads.
