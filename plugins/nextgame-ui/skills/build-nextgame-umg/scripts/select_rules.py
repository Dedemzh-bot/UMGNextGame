#!/usr/bin/env python3
"""Select only the UI rules that apply to a UILayoutSpec profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = SKILL_ROOT / "references" / "rule-index.json"
SOURCE_PRECEDENCE = {"explicit": 0, "observed": 1, "baseline": 2}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_dotted(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def applies(spec: dict[str, Any], conditions: Any) -> bool:
    if not conditions:
        return True
    if not isinstance(conditions, dict):
        return False
    for dotted, expected in conditions.items():
        actual = get_dotted(spec, dotted)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def select_rules(spec: dict[str, Any], index: dict[str, Any]) -> list[dict[str, Any]]:
    selected = [
        (position, rule)
        for position, rule in enumerate(index.get("rules", []))
        if isinstance(rule, dict) and applies(spec, rule.get("when", {}))
    ]
    selected.sort(
        key=lambda item: (
            SOURCE_PRECEDENCE.get(str(item[1].get("sourceType", "baseline")), len(SOURCE_PRECEDENCE)),
            item[0],
        )
    )
    return [rule for _, rule in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    args = parser.parse_args()
    try:
        spec = load_json(args.spec)
        index = load_json(args.rules)
        selected = select_rules(spec, index)
        selected_by_source: dict[str, list[str]] = {}
        for rule in selected:
            source_type = str(rule.get("sourceType", "baseline"))
            selected_by_source.setdefault(source_type, []).append(str(rule.get("id")))
        result = {
            "selectedRuleIds": [rule.get("id") for rule in selected],
            "selectedRuleIdsBySourceType": selected_by_source,
            "rules": selected,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
