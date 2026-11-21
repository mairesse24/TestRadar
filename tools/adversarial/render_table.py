"""Render results.json from evaluate_scenarios.py as a Markdown table.

    python3 tools/adversarial/render_table.py OUT_DIR/results.json
"""

from __future__ import annotations

import json
import sys

POLICIES = ("safe", "balanced", "aggressive")


def cell(row: dict, policy: str) -> str:
    entry = row[policy]
    selected = "ALL" if entry["fell_back"] else str(entry["selected"])
    missed = len(entry["missed_tests"])
    return f"{selected} / **{missed}**" if missed else f"{selected} / 0"


def main() -> None:
    result = json.load(open(sys.argv[1], encoding="utf-8"))
    print("| Scenario | Category | File | Failing | " + " | ".join(f"{p} sel / missed" for p in POLICIES) + " |")
    print("|---|---|---|---:|" + "---|" * len(POLICIES))
    for row in result["scenarios"]:
        patch = row.get("file", "")
        print(f"| {row['id']} | {row['stratum']} | {patch} | {len(row['failing'])} | "
              + " | ".join(cell(row, p) for p in POLICIES) + " |")
    print()
    print("| Policy | Simulated s (incl. 300 s per missed commit) | Missed commits |")
    print("|---|---:|---|")
    for policy, evaluation in result["scores"].items():
        missed = ", ".join(evaluation["missed_commits"]) or "none"
        print(f"| {policy} | {evaluation['simulated_milliseconds'] / 1000:.1f} | {missed} |")


if __name__ == "__main__":
    main()
