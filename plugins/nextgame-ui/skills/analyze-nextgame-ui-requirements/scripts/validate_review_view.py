#!/usr/bin/env python3
"""Validate a Review View against the complete immutable pending Draft."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _contract_common import load_json, sha256_file  # noqa: E402
from review_view import (  # noqa: E402
    DEFAULT_REQUIREMENT_SCHEMA,
    DEFAULT_VIEW_SCHEMA,
    validate_review_view,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("view", type=Path)
    parser.add_argument("--source-draft", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_VIEW_SCHEMA)
    parser.add_argument("--requirement-schema", type=Path, default=DEFAULT_REQUIREMENT_SCHEMA)
    args = parser.parse_args(argv)
    try:
        validation = validate_review_view(
            load_json(args.view),
            source_draft=load_json(args.source_draft),
            request=load_json(args.request),
            source_draft_file_sha256=sha256_file(args.source_draft),
            schema=args.schema,
            requirement_schema_path=args.requirement_schema,
        )
    except (OSError, ValueError) as exc:
        validation = {
            "valid": False,
            "errors": [{"code": "review-view.io", "path": "$", "message": str(exc)}],
            "warnings": [],
        }
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if validation.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
