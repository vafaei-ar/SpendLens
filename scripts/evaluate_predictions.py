import json
import sys
from pathlib import Path

from spendlens.evaluation import EvaluationCase, evaluate_cases


def main(path: Path) -> int:
    cases: list[EvaluationCase] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(EvaluationCase.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}") from exc

    metrics = evaluate_cases(cases)
    print(json.dumps(metrics.model_dump(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/evaluate_predictions.py predictions.jsonl")
    raise SystemExit(main(Path(sys.argv[1])))
