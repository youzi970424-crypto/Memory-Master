#!/usr/bin/env python3
"""Run product-level normal and abnormal-input evaluation datasets."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_URL = os.getenv("MEMORY_MASTER_API_URL", "http://127.0.0.1:8765/api/chat")
DATASETS = (
    ROOT / "evals" / "dataset" / "product_normal.json",
    ROOT / "evals" / "dataset" / "product_short_input.json",
    ROOT / "evals" / "dataset" / "product_sensitive.json",
    ROOT / "evals" / "dataset" / "product_long_input.json",
)


def build_query(case: dict[str, Any]) -> str:
    if "query" in case:
        return str(case["query"])
    return str(case.get("query_prefix", "")) + str(case.get("query_repeat", "")) * int(case.get("query_repeat_count", 0))


def call_case(case: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    payload = {
        "query": build_query(case),
        "manual_memory_ids": case.get("manual_memory_ids", []),
        "conversation_history": case.get("conversation_history", []),
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = {}
        return error.code, detail


def response_text(payload: dict[str, Any]) -> str:
    answer = payload.get("answer", {})
    parts = [str(answer.get(key, "")) for key in ("summary", "recommendation")]
    for key in ("reasoning_points", "action_items", "uncertainties"):
        for item in answer.get(key, []):
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def evaluate(case: dict[str, Any], status: int, payload: dict[str, Any]) -> tuple[bool, str]:
    expected_status = case.get("expected_http_status")
    if expected_status is not None:
        ok = status == expected_status
        detail = f"http={status}, expected={expected_status}"
        expected_errors = case.get("expected_error_contains", [])
        actual_error = str(payload.get("error", ""))
        ok = ok and all(fragment in actual_error for fragment in expected_errors)
        if expected_errors:
            detail += f", error={actual_error!r}"
        return ok, detail

    if status != 200:
        return False, f"http={status}, error={payload.get('error', '')}"

    selected = payload.get("memory_usage", {}).get("selected_memory_ids", [])
    answer = response_text(payload)
    expected_ids = case.get("expected_memory_ids", [])
    forbidden_ids = case.get("forbidden_memory_ids", [])
    required = case.get("answer_contains", [])
    forbidden = case.get("answer_not_contains", [])
    expected_warnings = case.get("expected_warning_contains", [])
    warnings = " ".join(payload.get("meta", {}).get("warnings", []))

    checks = [
        all(memory_id in selected for memory_id in expected_ids),
        all(memory_id not in selected for memory_id in forbidden_ids),
        all(fragment in answer for fragment in required),
        all(fragment not in answer for fragment in forbidden),
        all(fragment in warnings for fragment in expected_warnings),
    ]
    return all(checks), (
        f"selected={selected}, mode={payload.get('meta', {}).get('generation_mode')}, "
        f"warnings={payload.get('meta', {}).get('warnings', [])}"
    )


def load_cases() -> list[dict[str, Any]]:
    selected = [Path(item).resolve() for item in sys.argv[1:]] if len(sys.argv) > 1 else list(DATASETS)
    cases: list[dict[str, Any]] = []
    for path in selected:
        cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return cases


def main() -> int:
    cases = load_cases()
    passed = 0
    for case in cases:
        try:
            status, payload = call_case(case)
            ok, detail = evaluate(case, status, payload)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            ok, detail = False, f"request error={error}"
        passed += int(ok)
        print(f'{"PASS" if ok else "FAIL"} [{case["category"]}] {case["name"]}')
        print(f"  {detail}")
    print(f"{passed}/{len(cases)} product cases passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
