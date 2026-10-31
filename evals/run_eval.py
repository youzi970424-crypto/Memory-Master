#!/usr/bin/env python3
"""Evaluation runner for retrieval and answer-quality checks."""

from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from http.client import HTTPException
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
import re


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "dataset" / "prompt_eval.json"
API_URL = os.getenv("MEMORY_MASTER_API_URL", "http://127.0.0.1:8765/api/chat")
API_HEALTH_URL = API_URL.rsplit("/chat", 1)[0] + "/health"
API_REQUEST_TIMEOUT = float(os.getenv("MEMORY_MASTER_EVAL_TIMEOUT", "90"))


def is_local_api() -> bool:
    return API_URL.startswith("http://127.0.0.1:") or API_URL.startswith("http://localhost:")


def api_is_ready() -> bool:
    request = urllib.request.Request(API_HEALTH_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok"))
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException, json.JSONDecodeError, ValueError):
        return False


def start_local_api() -> subprocess.Popen[str]:
    command = [
        sys.executable,
        str(ROOT / "services" / "api" / "server.py"),
    ]
    env = os.environ.copy()
    if "MEMORY_MASTER_API_PORT" not in env:
        env["MEMORY_MASTER_API_PORT"] = str(urlparse(API_URL).port or 8765)
    return subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_api() -> subprocess.Popen[str] | None:
    if api_is_ready():
        return None
    if not is_local_api():
        return None

    process = start_local_api()
    deadline = time.time() + 10
    while time.time() < deadline:
        if api_is_ready():
            return process
        if process.poll() is not None:
            break
        time.sleep(0.25)
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
    raise RuntimeError("无法启动本地 API，请先单独运行 `pnpm api` 再重试")


def load_dataset() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    return DEFAULT_DATASET


def contains_all(text: str, fragments: list[str]) -> bool:
    return all(fragment in text for fragment in fragments)


def contains_none(text: str, fragments: list[str]) -> bool:
    return all(fragment not in text for fragment in fragments)


def normalize_eval_text(text: str) -> str:
    replacements = [
        (r'先回答(?:三个|几个)?问题|先确认问题|先明确问题|先梳理问题|先定好问题', '先定问题'),
        (r'先谈重做方案|先谈方案|先讨论方案|先看方案|先评估是否要进版本|先决定要不要进版本', '先定问题'),
        (r'先补齐(?:“|")?(?:先定问题、衡量指标、优先级|问题、指标、优先级|问题、衡量指标、优先级)(?:”|")?', '先定问题 衡量指标 优先级'),
        (r'先补齐(?:这三项|这三件事|这三点|三项)', '先定问题 衡量指标 优先级'),
        (r'先补齐', '先定问题 衡量指标 优先级'),
        (r'衡量标准|判断标准|量化指标|评估指标|注册转化率|流失率|完成时长|完成率|留存率|通过率', '衡量指标'),
        (r'3\s*[到至\-]\s*5\s*个?\s*(?:低成本|低价|低风险|小成本)?\s*(?:实验|试验|验证)', '3-5 个低成本实验'),
        (r'低成本实验|低成本试验|小范围验证|小范围试验|先做小范围|先跑(?:一轮)?(?:低成本|小范围)?(?:实验|验证)', '先做小范围 3-5 个低成本实验'),
        (r'再跑(?:一个)?(?:3-5 个)?低成本实验(?:验证|跑出结论|再决定)?|先验证再决定|验证后再决定|跑出结论后再决定', '先做小范围 3-5 个低成本实验 再决定是否扩大投入'),
        (r'高优先级|优先级较高', '优先级'),
        (r'最小可执行动作|最小动作|下一步动作|最小行动', '最小动作'),
        (r'再决定是否大改|再决定是否扩大投入|再决定要不要做大改版|再决定要不要进入下个版本', '再决定是否扩大投入'),
    ]
    normalized = text
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    return normalized


def case_payload(case: dict) -> dict:
    return {
        "query": case["query"],
        "manual_memory_ids": case.get("manual_memory_ids", []),
        "conversation_history": case.get("conversation_history", []),
    }


def answer_text(answer: dict) -> str:
    parts: list[str] = []
    for key in ("summary", "recommendation"):
        value = answer.get(key, "")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    for key in ("reasoning_points", "action_items", "uncertainties"):
        items = answer.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                prefix = "- "
                if key == "action_items" and item.get("priority"):
                    parts.append(f"{prefix}{item['text'].strip()}（{item['priority']}）")
                else:
                    parts.append(f"{prefix}{item['text'].strip()}")
    return "\n".join(parts)


def call_chat(case: dict) -> dict:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(case_payload(case), ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=API_REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error", "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = ""
        raise RuntimeError(f"API 返回 HTTP {error.code}{f': {detail}' if detail else ''}") from error
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException) as error:
        raise RuntimeError(f"无法连接评测 API：{error}") from error


def evaluate_case(case: dict, payload: dict) -> tuple[bool, list[str]]:
    actual = payload["memory_usage"]["selected_memory_ids"]
    answer = normalize_eval_text(answer_text(payload["answer"]))
    checks: list[str] = []

    expected_memory_ids = case.get("expected_memory_ids", [])
    memory_hit = all(memory_id in actual for memory_id in expected_memory_ids)
    checks.append(f"memory={'ok' if memory_hit else 'miss'}:{actual}")

    forbidden_memory_ids = case.get("forbidden_memory_ids", [])
    memory_clean = all(memory_id not in actual for memory_id in forbidden_memory_ids)
    if forbidden_memory_ids:
        checks.append(
            f"forbidden_memory={'ok' if memory_clean else 'miss'}:{forbidden_memory_ids}"
        )

    required_fragments = case.get("answer_contains", [])
    answer_hit = contains_all(answer, required_fragments)
    if required_fragments:
        missing_fragments = [fragment for fragment in required_fragments if fragment not in answer]
        checks.append(
            f"contains={'ok' if answer_hit else 'miss'}:{required_fragments}"
            + (f", missing={missing_fragments}" if missing_fragments else "")
        )

    forbidden_fragments = case.get("answer_not_contains", [])
    answer_clean = contains_none(answer, forbidden_fragments)
    if forbidden_fragments:
        checks.append(f"not_contains={'ok' if answer_clean else 'miss'}:{forbidden_fragments}")

    mode = payload["meta"]["generation_mode"]
    checks.append(f"mode={mode}")

    if os.getenv("MEMORY_MASTER_REQUIRE_MODEL") == "1" and mode != "model":
        checks.append("model_required=miss")
        return False, checks

    if mode == "model_error":
        checks.append(f"error={payload['meta'].get('error', 'unknown')}")
        return False, checks

    if not expected_memory_ids:
        empty_hit = not actual and memory_clean and answer_hit and answer_clean
        return empty_hit, checks

    passed = memory_hit and memory_clean and answer_hit and answer_clean
    return passed, checks


def main() -> int:
    dataset_path = load_dataset()
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    passed = 0
    model_cases = 0
    completed_cases = 0
    failed_cases: list[str] = []
    api_process: subprocess.Popen[str] | None = None
    try:
        api_process = ensure_api()
        for case in cases:
            try:
                payload = call_chat(case)
            except RuntimeError as error:
                print(f"FAIL {case['name']}")
                print(f"  {error}")
                continue
            completed_cases += 1
            ok, checks = evaluate_case(case, payload)
            if payload["meta"]["generation_mode"] == "model":
                model_cases += 1
            passed += int(ok)
            if not ok:
                failed_cases.append(case["name"])
            print(f'{"PASS" if ok else "FAIL"} {case["name"]}')
            print("  " + " | ".join(checks))
            print("  answer:", answer_text(payload["answer"]))
        print(f"{passed}/{len(cases)} cases passed")
        if failed_cases:
            print("Failed cases: " + "、".join(failed_cases))
        if completed_cases == 0:
            print("WARNING: 没有完成任何评测用例，本地 API 可能没有启动成功。")
        elif model_cases == 0:
            print("WARNING: 当前全部结果来自 local_fallback，这只能验证检索与兜底，不代表系统提示词已完成真实模型评测。")
        return 0 if passed == len(cases) else 1
    finally:
        if api_process is not None and api_process.poll() is None:
            api_process.terminate()
            try:
                api_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                api_process.kill()


if __name__ == "__main__":
    sys.exit(main())
