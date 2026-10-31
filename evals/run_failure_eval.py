#!/usr/bin/env python3
"""Offline failure-injection checks for model, embedding, and retrieval fallbacks."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "services" / "api" / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("memory_master_server_failure", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载本地 API 模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: Any):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def model_settings(**overrides: Any) -> dict[str, Any]:
    settings = {
        "enabled": True,
        "base_url": "http://model.test/v1",
        "api_key": "test",
        "model": "test-model",
        "embedding_model": "",
    }
    settings.update(overrides)
    return settings


def valid_model_payload(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


def valid_answer(mode: str = "model") -> dict[str, Any]:
    return {
        "summary": "测试摘要",
        "recommendation": "测试建议",
        "reasoning_points": [],
        "action_items": [],
        "uncertainties": [],
        "_mode": mode,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memory-master-failure-") as directory:
        os.environ["MEMORY_MASTER_DB"] = str(Path(directory) / "failure.db")
        server = load_server()
        connection = server.db()
        original_config = server.config
        original_call_model = server.call_model
        original_call_embeddings = server.call_embeddings
        original_urlopen = urllib.request.urlopen
        checks: list[tuple[str, bool, str]] = []

        try:
            calls = 0
            request_bodies: list[dict[str, Any]] = []

            def invalid_then_valid(_request: Any, timeout: int):
                nonlocal calls
                calls += 1
                request_bodies.append(json.loads(_request.data.decode("utf-8")))
                if calls == 1:
                    return FakeResponse(valid_model_payload("这不是 JSON"))
                return FakeResponse(valid_model_payload(json.dumps(valid_answer(), ensure_ascii=False)))

            urllib.request.urlopen = invalid_then_valid
            answer = server.call_model(model_settings(), {"query": "测试", "memories": []})
            checks.append(
                (
                    "模型第一次非法 JSON 后重试成功",
                    calls == 2 and answer is not None and answer["_mode"] == "model",
                    f"calls={calls}, answer={answer}",
                )
            )
            checks.append(
                (
                    "模型请求携带默认回复 token 上限",
                    request_bodies[-1]["max_tokens"] == server.CHAT_RESPONSE_MAX_TOKENS_DEFAULT,
                    str(request_bodies[-1]),
                )
            )

            calls = 0
            request_bodies = []

            def always_valid_capture(_request: Any, timeout: int):
                nonlocal calls
                calls += 1
                request_bodies.append(json.loads(_request.data.decode("utf-8")))
                return FakeResponse(valid_model_payload(json.dumps(valid_answer(), ensure_ascii=False)))

            urllib.request.urlopen = always_valid_capture
            answer = server.call_model(model_settings(), {"query": "帮我写一篇1990字的文章", "memories": []})
            checks.append(
                (
                    "长文请求放宽回复 token 上限",
                    calls == 1
                    and answer is not None
                    and request_bodies[-1]["max_tokens"] == server.CHAT_RESPONSE_MAX_TOKENS_LONG,
                    f"calls={calls}, request={request_bodies[-1] if request_bodies else None}",
                )
            )

            calls = 0

            def always_invalid(_request: Any, timeout: int):
                nonlocal calls
                calls += 1
                return FakeResponse(valid_model_payload("仍然不是 JSON"))

            urllib.request.urlopen = always_invalid
            answer = server.call_model(model_settings(), {"query": "测试", "memories": []})
            checks.append(
                (
                    "模型多次非法 JSON 后按原文兜底展示",
                    calls == 3
                    and answer is not None
                    and answer["_mode"] == "model"
                    and answer["recommendation"] == "仍然不是 JSON"
                    and any("非标准 JSON" in warning for warning in answer.get("_warnings", [])),
                    f"calls={calls}, answer={answer}",
                )
            )

            calls = 0

            def malformed_jsonish(_request: Any, timeout: int):
                nonlocal calls
                calls += 1
                return FakeResponse(valid_model_payload(
                    '{"summary": "青禾需要先确认预算。", "recommendation": "## 一、客户回顾\n\n青禾（销售跟进线索）\n\n- 预算未确认。'
                ))

            urllib.request.urlopen = malformed_jsonish
            try:
                server.call_model(model_settings(), {"query": "测试", "memories": []})
                malformed_rejected = False
                malformed_detail = "未抛出错误"
            except ValueError as error:
                malformed_rejected = (
                    calls == 3
                    and getattr(error, "code", "") == "model_invalid_json"
                )
                malformed_detail = f"calls={calls}, error={error}"
            checks.append(
                (
                    "半截 JSON 不抽取展示而是重试后失败",
                    malformed_rejected,
                    malformed_detail,
                )
            )

            server.config = lambda _connection: model_settings()
            server.call_model = lambda _settings, _payload: (_ for _ in ()).throw(
                server.ModelCallError("模型返回 JSON 不完整", "model_invalid_json")
            )
            invalid_json_fallback_response = server.handle_chat(
                connection,
                {"query": "需求评审前需要准备什么？", "manual_memory_ids": ["m-3"]},
            )
            checks.append(
                (
                    "模型 JSON 不完整时自动使用本地证据兜底",
                    invalid_json_fallback_response["meta"]["generation_mode"] == "local_fallback"
                    and any("不完整或非标准 JSON" in warning for warning in invalid_json_fallback_response["meta"]["warnings"])
                    and "需求评审" in invalid_json_fallback_response["answer"]["recommendation"],
                    str(invalid_json_fallback_response["meta"]),
                )
            )
            server.call_model = original_call_model

            calls = 0

            def always_timeout(_request: Any, timeout: int):
                nonlocal calls
                calls += 1
                raise urllib.error.URLError("timeout")

            urllib.request.urlopen = always_timeout
            try:
                server.call_model(model_settings(), {"query": "测试", "memories": []})
                timeout_failed = False
                timeout_detail = "未抛出错误"
            except ValueError as error:
                timeout_failed = (
                    calls == 3
                    and "连接" in str(error)
                    and getattr(error, "code", "") == "model_connection_error"
                )
                timeout_detail = f"calls={calls}, error={error}"
            checks.append(("模型超时重试后明确失败", timeout_failed, timeout_detail))

            server.config = lambda _connection: model_settings(
                base_url="http://embedding.test/v1",
                embedding_model="test-embedding",
            )

            def failed_embeddings(_settings: dict[str, Any], _inputs: list[str]):
                raise ValueError("模拟 Embedding 失败")

            server.call_embeddings = failed_embeddings
            server.call_model = lambda _settings, _payload: valid_answer()
            embedding_response = server.handle_chat(
                connection,
                {"query": "需求评审前需要准备什么？", "manual_memory_ids": []},
            )
            checks.append(
                (
                    "Embedding 失败降级且返回 warning",
                    embedding_response["meta"]["retrieval_mode"] == "bm25_keyword_fallback"
                    and any("Embedding" in warning for warning in embedding_response["meta"]["warnings"]),
                    str(embedding_response["meta"]),
                )
            )

            server.config = lambda _connection: {
                "provider": "",
                "base_url": "",
                "api_key": "",
                "model": "",
                "embedding_model": "",
                "enabled": False,
            }
            fallback_response = server.handle_chat(
                connection,
                {"query": "没有配置模型时怎么办？", "manual_memory_ids": []},
            )
            checks.append(
                (
                    "未启用模型时明确标记本地兜底",
                    fallback_response["meta"]["generation_mode"] == "local_fallback"
                    and fallback_response["meta"]["model"] == "local-fallback",
                    str(fallback_response["meta"]),
                )
            )

            server.config = lambda _connection: model_settings()

            def empty_response_model(_settings: dict[str, Any], _payload: dict[str, Any]):
                raise server.ModelCallError("模型返回内容为空", "model_empty_response")

            server.call_model = empty_response_model
            empty_model_response = server.handle_chat(
                connection,
                {"query": "需求评审前需要准备什么？", "manual_memory_ids": ["m-3"]},
            )
            checks.append(
                (
                    "模型空响应时自动使用本地证据兜底",
                    empty_model_response["meta"]["generation_mode"] == "local_fallback"
                    and "model_error" != empty_model_response["meta"]["generation_mode"]
                    and any("模型返回空内容" in warning for warning in empty_model_response["meta"]["warnings"])
                    and "需求评审" in empty_model_response["answer"]["recommendation"],
                    str(empty_model_response["meta"]),
                )
            )

            server.config = lambda _connection: model_settings(
                base_url="",
                api_key="",
                model="",
                embedding_model="",
            )
            server.call_model = lambda _settings, _payload: None
            missing_config_response = server.handle_chat(
                connection,
                {"query": "模型配置缺失怎么办？", "manual_memory_ids": []},
            )
            checks.append(
                (
                    "模型配置缺失不伪装成兜底成功",
                    missing_config_response["meta"]["generation_mode"] == "model_error"
                    and missing_config_response["meta"]["error_code"] == "model_config_missing",
                    str(missing_config_response["meta"]),
                )
            )

            server.config = lambda _connection: model_settings()
            captured_payload: dict[str, Any] = {}

            def capture_model(_settings: dict[str, Any], payload: dict[str, Any]):
                captured_payload.update(payload)
                return valid_answer()

            server.call_model = capture_model
            long_id = "long-card"
            connection.execute(
                """
                INSERT INTO memories
                (id, title, content, memory_type, source, tags_json, visibility,
                 created_at, updated_at, source_weight, quality_weight, citations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    long_id,
                    "超长测试卡",
                    "长内容" * 900,
                    "经验型",
                    "手动添加",
                    "[]",
                    "visible",
                    server.now(),
                    server.now(),
                    1.0,
                    0.8,
                    0,
                ),
            )
            connection.commit()
            long_response = server.handle_chat(
                connection,
                {"query": "超长测试卡", "manual_memory_ids": [long_id]},
            )
            sent_content = captured_payload["memories"][0]["content"]
            checks.append(
                (
                    "超长记忆卡截断并返回 warning",
                    len(sent_content) == 2001
                    and long_id in long_response["meta"]["warnings"][0],
                    f"sent_length={len(sent_content)}, meta={long_response['meta']}",
                )
            )

            missing_response = server.handle_chat(
                connection,
                {"query": "需求评审前需要准备什么？", "manual_memory_ids": ["missing-card"]},
            )
            checks.append(
                (
                    "手动卡不存在时保留 dropped ID 和 warning",
                    missing_response["memory_usage"]["dropped_memory_ids"] == ["missing-card"]
                    and any("未找到手动指定" in warning for warning in missing_response["meta"]["warnings"]),
                    str(missing_response["memory_usage"]),
                )
            )

            def no_citation_model(_settings: dict[str, Any], _payload: dict[str, Any]):
                return {
                    **valid_answer(),
                    "reasoning_points": [{"text": "没有绑定引用", "memory_ids": []}],
                }

            server.call_model = no_citation_model
            no_citation_response = server.handle_chat(
                connection,
                {"query": "需求评审前需要准备什么？", "manual_memory_ids": ["m-3"]},
            )
            checks.append(
                (
                    "模型没有有效引用时返回 warning",
                    any("没有返回有效引用" in warning for warning in no_citation_response["meta"]["warnings"])
                    and no_citation_response["memory_usage"]["citation_count"] == 0,
                    str(no_citation_response["meta"]),
                )
            )
        finally:
            server.config = original_config
            server.call_model = original_call_model
            server.call_embeddings = original_call_embeddings
            urllib.request.urlopen = original_urlopen
            connection.close()

        passed = 0
        for name, ok, details in checks:
            passed += int(ok)
            print(f'{"PASS" if ok else "FAIL"} {name}')
            print(f"  {details}")
        print(f"{passed}/{len(checks)} failure checks passed")
        return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
