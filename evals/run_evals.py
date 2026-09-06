"""Intent classification accuracy gate.

Evaluates whichever classifier the environment selects:

    LLM_MODE=mock  -> the deterministic keyword fallback (default, offline, free)
    LLM_MODE=live  -> Gemini, using the real system prompt

Running the same set against both is the point. The fallback is not a toy: it
serves every request whenever the model is rate limited or down, so a regression
there is a production regression, and it is the only path that CI can afford to
exercise on every commit.

    python -m evals.run_evals
    python -m evals.run_evals --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from app.config import get_settings
from app.llm.client import IntentClassifier

GOLDEN_PATH = Path(__file__).parent / "golden.yaml"

MIN_ACCURACY = 0.90


def load_cases(path: Path = GOLDEN_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def run() -> dict:
    settings = get_settings()
    classifier = IntentClassifier(settings)
    cases = load_cases()

    results = []
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_action: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})

    for case in cases:
        predicted = classifier.classify(case["question"]).intent.action
        expected = case["expected"]
        correct = predicted == expected

        results.append(
            {
                "question": case["question"],
                "expected": expected,
                "predicted": predicted,
                "correct": correct,
            }
        )
        confusion[(expected, predicted)] += 1
        per_action[expected]["total"] += 1
        per_action[expected]["correct"] += int(correct)

    correct_total = sum(r["correct"] for r in results)
    return {
        "mode": "live" if settings.llm_live else "mock",
        "total": len(results),
        "correct": correct_total,
        "accuracy": correct_total / len(results) if results else 0.0,
        "per_action": dict(per_action),
        "confusion": {f"{e}->{p}": n for (e, p), n in sorted(confusion.items())},
        "failures": [r for r in results if not r["correct"]],
    }


def report(summary: dict) -> None:
    print("=" * 72)
    print(f"INTENT CLASSIFICATION EVAL  (classifier: {summary['mode']})")
    print("=" * 72)
    print(f"  accuracy   {summary['correct']}/{summary['total']}  "
          f"({summary['accuracy']:.1%})  min {MIN_ACCURACY:.0%}")

    print("\n  per intent:")
    for action, stats in sorted(summary["per_action"].items()):
        correct, total = stats["correct"], stats["total"]
        flag = "" if correct == total else "  <-- misses"
        print(f"    {action:20} {correct:>2}/{total:<2}{flag}")

    if summary["failures"]:
        print("\n  MISCLASSIFIED:")
        for f in summary["failures"]:
            print(f"    {f['expected']:16} -> {f['predicted']:16} {f['question'][:44]}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write the summary as JSON to this path")
    args = parser.parse_args()

    summary = run()
    report(summary)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if summary["accuracy"] < MIN_ACCURACY:
        print(f"FAIL: accuracy {summary['accuracy']:.1%} is below {MIN_ACCURACY:.0%}")
        return 1

    print(f"PASS: accuracy {summary['accuracy']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
