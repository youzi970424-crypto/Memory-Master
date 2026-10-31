#!/usr/bin/env python3
"""Offline checks for structured answer fields and citation boundaries."""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "services" / "api" / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("memory_master_server_structured", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载本地 API 模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memory-master-structured-") as directory:
        os.environ["MEMORY_MASTER_DB"] = str(Path(directory) / "structured.db")
        server = load_server()
        connection = server.db()
        original_config = server.config
        original_call_model = server.call_model
        original_classify_turn_with_model = server.classify_turn_with_model
        original_verify_used_memory_ids = server.verify_used_memory_ids
        original_retrieve = server.retrieve
        captured_payload: dict[str, Any] = {}
        format_revision_payload: dict[str, Any] = {}
        todo_number_revision_payload: dict[str, Any] = {}
        short_number_revision_payload: dict[str, Any] = {}
        constraint_revision_payload: dict[str, Any] = {}
        disabled_memory_payload: dict[str, Any] = {}
        priority_framework_payload: dict[str, Any] = {}
        server.config = lambda _connection: {
            "provider": "test",
            "base_url": "http://model.test",
            "api_key": "test",
            "model": "test-model",
            "embedding_model": "",
            "enabled": True,
        }

        def fake_call_model(_settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
            if payload["query"] == "注册流程":
                captured_payload.update(payload)
                return {
                    "summary": "先评审，再用实验验证。",
                    "recommendation": "先明确需求评审条件，再决定是否扩大投入。",
                    "reasoning_points": [
                        {"text": "注册流程需要先做实验", "memory_ids": ["m-4"]},
                        {"text": "需求评审需要先明确指标", "memory_ids": ["m-3"]},
                    ],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "用数字1 2 3这种展示，我看看一共多少条":
                format_revision_payload.update(payload)
                return {
                    "summary": "",
                    "recommendation": "1. 第一条\n2. 第二条\n\n共2条",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "帮我给待办信息增加一个序号":
                todo_number_revision_payload.update(payload)
                return {
                    "summary": "",
                    "recommendation": "1. 整理云启客户进展\n2. 跟进数据迁移评估",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "帮我用序号排列下":
                short_number_revision_payload.update(payload)
                return {
                    "summary": "",
                    "recommendation": "1. 整理云启客户进展\n2. 跟进数据迁移评估",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "这段精简一下，只保留重点":
                constraint_revision_payload.update(payload)
                return {
                    "summary": "",
                    "recommendation": "重点：整理云启客户进展；跟进数据迁移评估。",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "不要使用卡片信息帮我重新写一下这篇内容":
                disabled_memory_payload.update(payload)
                return {
                    "summary": "",
                    "recommendation": "已不使用卡片信息，直接基于原文重写。",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "继续查一下还有没有其他待办":
                return {
                    "summary": "",
                    "recommendation": "继续查询到 1 条其他待办。",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "用这张卡的口吻重写":
                return {
                    "summary": "",
                    "recommendation": "这是一段新的重写内容，没有使用候选卡片里的具体事实或表达。",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "为什么历史版本容易延期？":
                return {
                    "summary": "基于后台信息给出建议",
                    "recommendation": "建议先锁定小范围实验。",
                    "reasoning_points": [
                        {"text": "后台记忆支持范围控制", "memory_ids": ["hidden-1"]},
                    ],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "用这张卡内容重写":
                return {
                    "summary": "",
                    "recommendation": "先定问题、再谈方案、最后排期。进入评审前先说明要解决的问题、衡量指标和优先级。",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            if payload["query"] == "用RICE给注册流程和需求评审排个优先级":
                priority_framework_payload.update(payload)
                return {
                    "summary": "当前只能做定性排序。",
                    "recommendation": "先按统一维度比较，再补齐量化参数。",
                    "reasoning_points": [],
                    "action_items": [],
                    "uncertainties": [],
                    "_mode": "model",
                }
            return {
                "summary": "不应暴露内部编号",
                "recommendation": "先定问题，再确认衡量指标和优先级。",
                "reasoning_points": [
                    {"text": "需求评审规则", "memory_ids": ["m-3", "not-a-real-id"]},
                ],
                "action_items": [{"text": "准备评审材料", "priority": "unexpected"}],
                "uncertainties": [
                    {"text": "仍需确认范围", "memory_ids": ["hidden-1", "not-a-real-id"]},
                ],
                "_mode": "model",
            }

        server.call_model = fake_call_model

        def fake_verify_used_memory_ids(
            _settings: dict[str, Any],
            query: str,
            _answer: dict[str, Any],
            _memories: list[dict[str, Any]],
        ) -> list[str]:
            if query in {"需求评审测试", "用这张卡内容重写"}:
                return ["m-3"]
            if query == "注册流程":
                return ["m-3", "m-4"]
            return []

        server.verify_used_memory_ids = fake_verify_used_memory_ids

        def fake_classify_turn_with_model(
            _settings: dict[str, Any],
            query: str,
            _history: list[dict[str, str]],
        ) -> dict[str, Any] | None:
            if query in {
                "用数字1 2 3这种展示，我看看一共多少条",
                "帮我给待办信息增加一个序号",
                "帮我用序号排列下",
            }:
                return {
                    "turn_type": "format_revision",
                    "confidence": 0.96,
                    "reason": "用户是在要求改写上一轮答案的展示格式，不需要查询新信息。",
                }
            if query == "这段精简一下，只保留重点":
                return {
                    "turn_type": "constraint_revision",
                    "confidence": 0.96,
                    "reason": "用户是在压缩上一轮答案，不需要查询新信息。",
                }
            if query == "继续查一下还有没有其他待办":
                return {
                    "turn_type": "follow_up",
                    "confidence": 0.92,
                    "reason": "用户是在上一轮待办主题下继续查询更多内容。",
                }
            return None

        server.classify_turn_with_model = fake_classify_turn_with_model
        checks: list[tuple[str, bool, str]] = []
        try:
            response = server.handle_chat(
                connection,
                {"query": "需求评审测试", "manual_memory_ids": ["m-3"]},
            )
            answer = response["answer"]
            checks.append(
                (
                    "只保留输入中的合法引用 ID",
                    answer["reasoning_points"][0]["memory_ids"] == ["m-3"]
                    and answer["uncertainties"][0]["memory_ids"] == [],
                    str(answer),
                )
            )
            checks.append(
                (
                    "引用只来自实际绑定的可见卡",
                    [item["id"] for item in response["memory_usage"]["citations"]] == ["m-3"]
                    and response["memory_usage"]["citation_count"] == 1,
                    str(response["memory_usage"]),
                )
            )
            manual_without_model_reference_response = server.handle_chat(
                connection,
                {"query": "用这张卡的口吻重写", "manual_memory_ids": ["m-3"]},
            )
            checks.append(
                (
                    "手动选择但回答未实际使用时不回显卡片",
                    manual_without_model_reference_response["memory_usage"]["primary_citation_ids"] == []
                    and manual_without_model_reference_response["memory_usage"]["citation_count"] == 0
                    and manual_without_model_reference_response["memory_usage"]["recalled_memories"] == []
                    and manual_without_model_reference_response["memory_usage"]["citations"] == [],
                    str(manual_without_model_reference_response["memory_usage"]),
                )
            )
            manual_used_without_model_reference_response = server.handle_chat(
                connection,
                {"query": "用这张卡内容重写", "manual_memory_ids": ["m-3"]},
            )
            checks.append(
                (
                    "回答实际使用卡片内容时回显手动卡",
                    manual_used_without_model_reference_response["memory_usage"]["primary_citation_ids"] == ["m-3"]
                    and manual_used_without_model_reference_response["memory_usage"]["citation_count"] == 1
                    and manual_used_without_model_reference_response["memory_usage"]["recalled_memories"][0]["id"] == "m-3"
                    and manual_used_without_model_reference_response["memory_usage"]["citations"][0]["id"] == "m-3",
                    str(manual_used_without_model_reference_response["memory_usage"]),
                )
            )
            checks.append(
                (
                    "动作优先级异常时归一化",
                    answer["action_items"][0]["priority"] == "medium",
                    str(answer["action_items"]),
                )
            )

            def manual_plus_auto_retrieve(
                _connection: Any,
                _query: str,
                _manual_ids: list[str],
                _settings: dict[str, Any],
                _diagnostics: dict[str, Any] | None = None,
            ) -> list[dict[str, Any]]:
                selected = original_retrieve(
                    connection,
                    "注册流程",
                    ["m-3", "m-4"],
                    _settings,
                    _diagnostics,
                )
                for item in selected:
                    item["manual"] = item["memory"]["id"] == "m-3"
                return selected

            server.retrieve = manual_plus_auto_retrieve
            prioritized_response = server.handle_chat(
                connection,
                {
                    "query": "注册流程",
                    "manual_memory_ids": ["m-3"],
                },
            )
            primary_memory = next(
                item for item in captured_payload["memories"] if item["id"] == "m-3"
            )
            secondary_memory = next(
                item for item in captured_payload["memories"] if item["id"] == "m-4"
            )
            checks.append(
                (
                    "手动卡在模型上下文中标记为主引用并提高权重",
                    primary_memory["citation_priority"] == "primary"
                    and primary_memory["retrieval_weight"] == 1.0
                    and secondary_memory["citation_priority"] == "secondary"
                    and secondary_memory["retrieval_weight"] == 0.25,
                    str(captured_payload.get("memories")),
                )
            )
            checks.append(
                (
                    "引用结果按主动引用优先排序并分层",
                    [item["id"] for item in prioritized_response["memory_usage"]["citations"]]
                    == ["m-3", "m-4"]
                    and prioritized_response["memory_usage"]["primary_citation_ids"] == ["m-3"]
                    and prioritized_response["memory_usage"]["secondary_citation_ids"] == ["m-4"],
                    str(prioritized_response["memory_usage"]),
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
                {
                    "query": "注册流程",
                    "manual_memory_ids": ["m-3"],
                },
            )
            checks.append(
                (
                    "本地兜底也按主动引用优先排序",
                    fallback_response["memory_usage"]["primary_citation_ids"] == ["m-3"]
                    and fallback_response["memory_usage"]["citations"][0]["id"] == "m-3",
                    str(fallback_response["memory_usage"]),
                )
            )
            comparison_fallback_response = server.handle_chat(
                connection,
                {
                    "query": "用RICE给注册流程和需求评审排个优先级",
                    "manual_memory_ids": ["m-3", "m-4"],
                },
            )
            checks.append(
                (
                    "比较优先级类本地兜底输出定性比较且不编造分数",
                    "**定性比较**" in comparison_fallback_response["answer"]["recommendation"]
                    and "信息缺口" in comparison_fallback_response["answer"]["recommendation"]
                    and "不能生成精确" in comparison_fallback_response["answer"]["recommendation"],
                    comparison_fallback_response["answer"]["recommendation"],
                )
            )
            server.config = lambda _connection: {
                "provider": "test",
                "base_url": "http://model.test",
                "api_key": "test",
                "model": "test-model",
                "embedding_model": "",
                "enabled": True,
            }
            server.retrieve = original_retrieve
            server.handle_chat(
                connection,
                {
                    "query": "用RICE给注册流程和需求评审排个优先级",
                    "manual_memory_ids": ["m-3", "m-4"],
                },
            )
            checks.append(
                (
                    "比较和方法框架类问题携带通用生成约束",
                    "比较、排序、优先级或取舍判断任务" in priority_framework_payload.get("special_request", "")
                    and "不要编造分数" in priority_framework_payload.get("special_request", "")
                    and "最多用一句话说明方法口径" in priority_framework_payload.get("special_request", ""),
                    priority_framework_payload.get("special_request", ""),
                )
            )

            retrieve_calls = 0

            def counting_retrieve(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
                nonlocal retrieve_calls
                retrieve_calls += 1
                return []

            server.retrieve = counting_retrieve
            format_revision_response = server.handle_chat(
                connection,
                {
                    "query": "用数字1 2 3这种展示，我看看一共多少条",
                    "manual_memory_ids": [],
                    "conversation_history": [
                        {"role": "user", "text": "9.18以后还有哪些待办？"},
                        {"role": "assistant", "text": "9.18及以后待办清单（共2条）\n\n整理云启客户进展\n跟进数据迁移评估"},
                    ],
                },
            )
            checks.append(
                (
                    "格式追问复用上一轮答案且不触发新召回",
                    retrieve_calls == 0
                    and format_revision_payload["turn_type"] == "format_revision"
                    and format_revision_payload["context_policy"]["skip_auto_recall"] is True
                    and format_revision_response["memory_usage"]["recalled_memories"] == []
                    and "warnings" not in format_revision_response["meta"],
                    f"calls={retrieve_calls}, payload={format_revision_payload}, meta={format_revision_response['meta']}",
                )
            )
            server.retrieve = original_retrieve

            retrieve_calls = 0
            server.retrieve = counting_retrieve
            todo_number_revision_response = server.handle_chat(
                connection,
                {
                    "query": "帮我给待办信息增加一个序号",
                    "manual_memory_ids": [],
                    "conversation_history": [
                        {"role": "user", "text": "9.18以后还有哪些待办？"},
                        {"role": "assistant", "text": "9.18及以后待办清单（共2条）\n\n整理云启客户进展\n跟进数据迁移评估"},
                    ],
                },
            )
            checks.append(
                (
                    "待办编号追问不触发新召回或待办专项召回",
                    retrieve_calls == 0
                    and todo_number_revision_payload["turn_type"] == "format_revision"
                    and todo_number_revision_payload["context_policy"]["skip_auto_recall"] is True
                    and todo_number_revision_response["memory_usage"]["recalled_memories"] == []
                    and todo_number_revision_response["memory_usage"]["weekly_todos"] == []
                    and "warnings" not in todo_number_revision_response["meta"],
                    f"calls={retrieve_calls}, payload={todo_number_revision_payload}, usage={todo_number_revision_response['memory_usage']}, meta={todo_number_revision_response['meta']}",
                )
            )
            server.retrieve = original_retrieve

            retrieve_calls = 0
            server.retrieve = counting_retrieve
            short_number_revision_response = server.handle_chat(
                connection,
                {
                    "query": "帮我用序号排列下",
                    "manual_memory_ids": [],
                    "conversation_history": [
                        {"role": "user", "text": "9.18以后还有哪些待办？"},
                        {"role": "assistant", "text": "9/18（周五）\n整理云启、恒诚两个客户最新进展\n跟进王工反馈历史数据迁移工作量评估"},
                    ],
                },
            )
            checks.append(
                (
                    "短句序号追问不触发新召回或待办专项召回",
                    retrieve_calls == 0
                    and short_number_revision_payload["turn_type"] == "format_revision"
                    and short_number_revision_payload["context_policy"]["skip_auto_recall"] is True
                    and short_number_revision_response["memory_usage"]["recalled_memories"] == []
                    and short_number_revision_response["memory_usage"]["weekly_todos"] == []
                    and short_number_revision_response["meta"]["turn_type"] == "format_revision"
                    and "warnings" not in short_number_revision_response["meta"],
                    f"calls={retrieve_calls}, payload={short_number_revision_payload}, usage={short_number_revision_response['memory_usage']}, meta={short_number_revision_response['meta']}",
                )
            )
            server.retrieve = original_retrieve

            retrieve_calls = 0
            server.retrieve = counting_retrieve
            constraint_revision_response = server.handle_chat(
                connection,
                {
                    "query": "这段精简一下，只保留重点",
                    "manual_memory_ids": [],
                    "conversation_history": [
                        {"role": "user", "text": "9.18以后还有哪些待办？"},
                        {"role": "assistant", "text": "9.18及以后待办清单（共2条）\n\n整理云启客户进展\n跟进数据迁移评估"},
                    ],
                },
            )
            checks.append(
                (
                    "精简约束追问复用上一轮答案且不触发新召回",
                    retrieve_calls == 0
                    and constraint_revision_payload["turn_type"] == "constraint_revision"
                    and constraint_revision_payload["context_policy"]["skip_auto_recall"] is True
                    and constraint_revision_response["memory_usage"]["recalled_memories"] == []
                    and "warnings" not in constraint_revision_response["meta"],
                    f"calls={retrieve_calls}, payload={constraint_revision_payload}, meta={constraint_revision_response['meta']}",
                )
            )
            server.retrieve = original_retrieve

            retrieve_calls = 0
            server.retrieve = counting_retrieve
            follow_up_retrieval_response = server.handle_chat(
                connection,
                {
                    "query": "继续查一下还有没有其他待办",
                    "manual_memory_ids": [],
                    "conversation_history": [
                        {"role": "user", "text": "9.18以后还有哪些待办？"},
                        {"role": "assistant", "text": "9/18（周五）\n整理云启、恒诚两个客户最新进展"},
                    ],
                },
            )
            checks.append(
                (
                    "继续查询类追问允许进入召回流程",
                    retrieve_calls == 1
                    and follow_up_retrieval_response["meta"]["turn_type"] == "follow_up"
                    and follow_up_retrieval_response["meta"]["turn_classification"]["turn_type"] == "follow_up",
                    f"calls={retrieve_calls}, meta={follow_up_retrieval_response['meta']}",
                )
            )
            server.retrieve = original_retrieve

            retrieve_calls = 0
            server.retrieve = counting_retrieve
            disabled_memory_response = server.handle_chat(
                connection,
                {
                    "query": "不要使用卡片信息帮我重新写一下这篇内容",
                    "manual_memory_ids": ["m-3"],
                    "conversation_history": [
                        {"role": "user", "text": "帮我用卡片里的语气去重写这篇文章"},
                        {"role": "assistant", "text": "已按卡片语气重写。"},
                    ],
                },
            )
            checks.append(
                (
                    "明确不使用卡片信息时不召回不展示手动卡",
                    retrieve_calls == 0
                    and disabled_memory_payload["memories"] == []
                    and disabled_memory_payload["manual_memory_ids"] == []
                    and disabled_memory_response["memory_usage"]["selected_memory_ids"] == []
                    and disabled_memory_response["memory_usage"]["manual_memory_ids"] == []
                    and disabled_memory_response["memory_usage"]["recalled_memories"] == []
                    and disabled_memory_response["memory_usage"]["citations"] == []
                    and disabled_memory_response["memory_usage"]["citation_count"] == 0
                    and disabled_memory_response["meta"]["memory_usage_disabled"] is True,
                    f"calls={retrieve_calls}, payload={disabled_memory_payload}, usage={disabled_memory_response['memory_usage']}, meta={disabled_memory_response['meta']}",
                )
            )
            server.retrieve = original_retrieve

            hidden_response = server.handle_chat(
                connection,
                {"query": "为什么历史版本容易延期？", "manual_memory_ids": []},
            )
            checks.append(
                (
                    "隐藏记忆不进入可见引用",
                    hidden_response["memory_usage"]["citations"] == []
                    and hidden_response["memory_usage"]["citation_count"] == 0,
                    str(hidden_response["memory_usage"]),
                )
            )

            missing_response = server.handle_chat(
                connection,
                {"query": "需求评审测试", "manual_memory_ids": ["missing-card"]},
            )
            checks.append(
                (
                    "手动卡不存在时返回警告",
                    missing_response["memory_usage"]["dropped_memory_ids"] == ["missing-card"]
                    and bool(missing_response["meta"]["warnings"]),
                    str(missing_response["meta"]),
                )
            )
        finally:
            server.config = original_config
            server.call_model = original_call_model
            server.classify_turn_with_model = original_classify_turn_with_model
            server.verify_used_memory_ids = original_verify_used_memory_ids
            server.retrieve = original_retrieve
            connection.close()

        passed = 0
        for name, ok, details in checks:
            passed += int(ok)
            print(f'{"PASS" if ok else "FAIL"} {name}')
            print(f"  {details}")
        print(f"{passed}/{len(checks)} structured checks passed")
        return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
