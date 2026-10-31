#!/usr/bin/env python3
"""Offline retrieval checks plus a mocked embedding-cache check."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "dataset" / "retrieval_eval.json"
SERVER_PATH = ROOT / "services" / "api" / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("memory_master_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载本地 API 模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_case(server: Any, connection: Any, case: dict[str, Any]) -> tuple[bool, str]:
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(connection, case["query"], case.get("manual_memory_ids", []), settings)
    selected_ids = [item["memory"]["id"] for item in selected]
    expected = case.get("expected_memory_ids", [])
    forbidden = case.get("forbidden_memory_ids", [])
    hit = all(memory_id in selected_ids for memory_id in expected)
    clean = all(memory_id not in selected_ids for memory_id in forbidden)
    details = f"selected={selected_ids}"

    citation_forbidden = case.get("forbidden_visible_citation_ids", [])
    if citation_forbidden:
        response = server.handle_chat(
            connection,
            {
                "query": case["query"],
                "manual_memory_ids": case.get("manual_memory_ids", []),
            },
            user_id="legacy",
        )
        citations = [item["id"] for item in response["memory_usage"]["citations"]]
        clean = clean and all(memory_id not in citations for memory_id in citation_forbidden)
        details += f" | citations={citations}"

    return hit and clean, details


def check_embedding_cache(server: Any, connection: Any) -> tuple[bool, str]:
    calls: list[list[str]] = []

    def fake_embeddings(_settings: dict[str, Any], inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        vectors: list[list[float]] = []
        for text in inputs:
            if "需求评审" in text or "衡量指标" in text or "投入版本" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif "注册流程" in text or "低成本实验" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors

    original = server.call_embeddings
    server.call_embeddings = fake_embeddings
    settings = {
        "enabled": True,
        "base_url": "http://embedding.test",
        "api_key": "test",
        "embedding_model": "test-embedding",
    }
    try:
        first = server.retrieve(connection, "如何判断需求是否值得投入版本？", [], settings)
        second = server.retrieve(connection, "如何判断需求是否值得投入版本？", [], settings)
        cached_count = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE embedding_model = ?",
            ("test-embedding",),
        ).fetchone()[0]
    finally:
        server.call_embeddings = original

    selected_ids = [item["memory"]["id"] for item in first]
    passed = (
        len(calls) == 2
        and len(calls[0]) == 6
        and len(calls[1]) == 1
        and cached_count == 5
        and "m-3" in selected_ids
        and bool(second)
    )
    return passed, f"embedding_calls={len(calls)} cached_cards={cached_count} selected={selected_ids}"


def check_duplicate_deduplication(server: Any, connection: Any) -> tuple[bool, str]:
    source = connection.execute("SELECT * FROM memories WHERE id = 'm-4'").fetchone()
    connection.execute(
        """
        INSERT INTO memories
        (id, title, content, memory_type, source, tags_json, project, visibility,
         created_at, updated_at, source_weight, quality_weight, citations)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "m-4-duplicate",
            source["title"],
            source["content"],
            source["memory_type"],
            source["source"],
            source["tags_json"],
            source["project"],
            source["visibility"],
            source["created_at"],
            source["updated_at"],
            source["source_weight"],
            source["quality_weight"],
            0,
        ),
    )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(connection, "注册流程要不要先做低成本实验？", ["m-4"], settings)
    duplicate_ids = {"m-4", "m-4-duplicate"}
    selected_duplicate_count = sum(item["memory"]["id"] in duplicate_ids for item in selected)
    selected_ids = [item["memory"]["id"] for item in selected]
    return selected_duplicate_count == 1, f"selected={selected_ids}"


def check_similar_memory_prefers_recent(server: Any, connection: Any) -> tuple[bool, str]:
    cards = [
        (
            "similar-old",
            "注册流程优化 · 低成本实验旧版",
            "注册流程优化先做低成本实验，重点看手机号验证、验证码等待和落地页承接，再决定是否重写整体体验。",
            "2026-06-01T10:00:00+08:00",
        ),
        (
            "similar-new",
            "注册流程优化 · 低成本实验新版",
            "注册流程优化先做低成本实验，重点看手机号验证、验证码等待和落地页承接，再决定是否重写整体体验。",
            "2026-06-08T10:00:00+08:00",
        ),
    ]
    for card in cards:
        connection.execute(
            """
            INSERT INTO memories
            (id, title, content, memory_type, source, tags_json, project, visibility,
             created_at, updated_at, source_weight, quality_weight, citations)
            VALUES (?, ?, ?, '结论型', '测试记录', ?, '注册流程', 'visible', ?, ?, 1.0, 0.8, 0)
            """,
            (
                card[0],
                card[1],
                card[2],
                json.dumps(["注册流程", "低成本实验"], ensure_ascii=False),
                card[3],
                card[3],
            ),
        )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(connection, "注册流程要不要先做低成本实验？", [], settings)
    selected_ids = [item["memory"]["id"] for item in selected]
    return "similar-new" in selected_ids and "similar-old" not in selected_ids, f"selected={selected_ids}"


def check_manual_only_boundary(server: Any, connection: Any) -> tuple[bool, str]:
    response = server.handle_chat(
        connection,
        {
            "query": "只根据我选的卡，需求评审前需要准备什么？",
            "manual_memory_ids": ["m-3"],
        },
                   user_id="legacy",
    )
    selected_ids = response["memory_usage"]["selected_memory_ids"]
    auto_ids = response["memory_usage"]["auto_recalled_memory_ids"]
    return selected_ids == ["m-3"] and not auto_ids, f"selected={selected_ids} auto={auto_ids}"


def check_cutoff_date_filter(server: Any, connection: Any) -> tuple[bool, str]:
    timestamp = "2026-06-18T10:00:00+08:00"
    rows = [
        (
            "date-past",
            "星河订单台导出成本旧记录",
            "截至6月4日，完整自定义导出估算10个工作日，固定三字段CSV导出估算2个工作日。",
            "知识沉淀",
            "测试记录",
            ["星河订单台", "批量导出", "成本"],
            "星河订单台",
            "2026-06-04T10:00:00+08:00",
        ),
        (
            "date-future",
            "星河订单台导出成本未来记录",
            "6月12日以后补充：固定字段CSV试点已批准，但必须安全评审通过。",
            "会议纪要",
            "测试记录",
            ["星河订单台", "批量导出", "成本"],
            "星河订单台",
            "2026-06-12T10:00:00+08:00",
        ),
    ]
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, title, content, memory_type, source, tags_json, project, visibility,
             created_at, updated_at, source_weight, quality_weight, citations)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?, 1.0, 0.8, 0)
            """,
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                json.dumps(row[5], ensure_ascii=False),
                row[6],
                row[7],
                timestamp,
            ),
        )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "截至2026年6月4日，星河订单台导出成本是多少？",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    return "date-past" in selected_ids and "date-future" not in selected_ids, f"selected={selected_ids}"


def check_simulated_current_date_filter(server: Any, connection: Any) -> tuple[bool, str]:
    rows = [
        (
            "pm-week-m02",
            "周一上午",
            "青禾公司是一家工业设备经销商，有18名销售，联系人王敏，销售主管。团队目前主要使用表格管理跟进。王敏希望减少销售忘记回访的问题，但不希望频繁收到通知。客户尚未确认预算，也没有作出购买承诺。",
            "2026-09-14T10:00:00+08:00",
        ),
        (
            "pm-week-m05",
            "周二上午",
            "9月15日访谈青禾王敏。王敏表示，上周18名销售中有4人反馈发生过漏跟进，但没有统计漏跟进总次数，也无法确认造成多少收入损失。目前通过主管在群里提醒解决。王敏优先希望销售每天看到自己到期未跟进的客户，不希望每条事项都发邮件。她愿意看原型，没有承诺付费。",
            "2026-09-15T10:00:00+08:00",
        ),
        (
            "pm-week-m17",
            "周五上午",
            "9月18日青禾王敏表示，下周愿意安排3名销售试用原型，具体时间尚未约定。新增待办：林悦下周一前联系王敏确定试用时间。客户仍未确认采购或付费。",
            "2026-09-18T10:00:00+08:00",
        ),
    ]
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, title, content, memory_type, source, tags_json, project, visibility,
             created_at, updated_at, source_weight, quality_weight, citations)
            VALUES (?, ?, ?, '经验型', '测试记录', ?, '客户跟进提醒', 'visible',
                    ?, ?, 1.0, 0.8, 0)
            """,
            (
                row[0],
                row[1],
                row[2],
                json.dumps(["青禾", "产品经理闭环评测"], ensure_ascii=False),
                row[3],
                row[3],
            ),
        )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "本次测试视当前日期为2026年9月14日。明天见青禾之前，帮我回顾下这个客户，以及需要重点确认什么。",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    connection.execute(
        "DELETE FROM memories WHERE id IN ('pm-week-m02', 'pm-week-m05', 'pm-week-m17')"
    )
    connection.commit()
    return (
        "pm-week-m02" in selected_ids
        and "pm-week-m05" not in selected_ids
        and "pm-week-m17" not in selected_ids,
        f"selected={selected_ids}",
    )


def check_retrieval_scope_hard_filters(server: Any, connection: Any) -> tuple[bool, str]:
    rows = [
        (
            "scope-match",
            "澄岳机电客户背景",
            "澄岳机电销售团队使用表格管理客户回访。",
            "workspace-a",
            "project-visit",
            "snapshot-0920",
            "2026-09-20T09:00:00+08:00",
        ),
        (
            "scope-other-project",
            "澄岳机电订单导出",
            "澄岳机电需要导出订单信息。",
            "workspace-a",
            "project-export",
            "snapshot-0920",
            "2026-09-20T09:00:00+08:00",
        ),
        (
            "scope-other-snapshot",
            "澄岳机电后续试用",
            "澄岳机电安排销售试用原型。",
            "workspace-a",
            "project-visit",
            "snapshot-0924",
            "2026-09-24T09:00:00+08:00",
        ),
    ]
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, title, content, memory_type, source, tags_json, project,
             workspace_id, project_id, snapshot_id, visibility,
             created_at, updated_at, event_at, available_at, source_weight, quality_weight, citations)
            VALUES (?, ?, ?, '经验型', '测试记录', ?, '客户回访提醒',
                    ?, ?, ?, 'visible', ?, ?, ?, ?, 1.0, 0.8, 0)
            """,
            (
                row[0],
                row[1],
                row[2],
                json.dumps(["澄岳机电"], ensure_ascii=False),
                row[3],
                row[4],
                row[5],
                row[6],
                row[6],
                row[6],
                row[6],
            ),
        )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "澄岳机电客户背景",
        [],
        settings,
        scope={
            "workspace_id": "workspace-a",
            "project_id": "project-visit",
            "snapshot_id": "snapshot-0920",
            "as_of": "2026-09-20T23:59:59+08:00",
        },
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    connection.execute(
        "DELETE FROM memories WHERE id LIKE 'scope-%'"
    )
    connection.commit()
    return (
        selected_ids == ["scope-match"],
        f"selected={selected_ids}",
    )


def check_initial_qinghe_visit_prefers_monday_scope(server: Any, connection: Any) -> tuple[bool, str]:
    rows = [
        (
            "pm-week-initial-m01",
            "周一上午",
            "9月14日，客户跟进提醒项目周会。目前收到销售反馈，部分用户容易忘记跟进客户，本周先验证问题，不承诺上线。待办：产品经理林悦周二前完成青禾和远山两家客户访谈；设计师陈晨周三前完成提醒方案草图；工程师周航周四前评估实现成本。暂定首版支持站内提醒和邮件提醒，最终范围周四确定。",
            "2026-09-14T09:00:00+08:00",
        ),
        (
            "pm-week-initial-m02",
            "周一上午",
            "青禾公司是一家工业设备经销商，有18名销售，联系人王敏，销售主管。团队目前主要使用表格管理跟进。王敏希望减少销售忘记回访的问题，但不希望频繁收到通知。客户尚未确认预算，也没有作出购买承诺。",
            "2026-09-14T10:00:00+08:00",
        ),
        (
            "pm-week-initial-m04",
            "周一下午",
            "今天学习了JTBD，即Jobs to Be Done。理解重点是：用户在什么情境下，想取得什么进展，目前有什么阻碍。访谈时可以问“上次出现这个问题是什么时候”“当时怎么处理的”，不能仅凭用户说想要一个功能就认定需求已被验证。",
            "2026-09-14T15:00:00+08:00",
        ),
        (
            "pm-week-initial-m05",
            "周二上午",
            "9月15日访谈青禾王敏。王敏表示，上周18名销售中有4人反馈发生过漏跟进，但没有统计漏跟进总次数，也无法确认造成多少收入损失。目前通过主管在群里提醒解决。王敏优先希望销售每天看到自己到期未跟进的客户，不希望每条事项都发邮件。她愿意看原型，没有承诺付费。",
            "2026-09-15T10:00:00+08:00",
        ),
        (
            "pm-week-initial-m11",
            "周三下午",
            "9月16日客户跟进提醒设计评审。陈晨已完成提醒方案草图，该待办完成。评审认为用户需要先看到“今天该跟进谁”，不宜默认开启逐条邮件。新增待办：陈晨周五中午前完成站内清单交互稿；林悦周四前向青禾确认可以接受的提醒频率。邮件是否纳入首版仍待周四决定。",
            "2026-09-16T15:00:00+08:00",
        ),
        (
            "pm-week-initial-m17",
            "周五上午",
            "9月18日青禾王敏表示，下周愿意安排3名销售试用原型，具体时间尚未约定。新增待办：林悦下周一前联系王敏确定试用时间。客户仍未确认采购或付费。",
            "2026-09-18T10:00:00+08:00",
        ),
    ]
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, title, content, memory_type, source, tags_json, project, visibility,
             created_at, updated_at, source_weight, quality_weight, citations)
            VALUES (?, ?, ?, '经验型', '测试记录', ?, '客户跟进提醒', 'visible',
                    ?, ?, 1.0, 0.8, 0)
            """,
            (
                row[0],
                row[1],
                row[2],
                json.dumps(["青禾", "产品经理闭环评测"], ensure_ascii=False),
                row[3],
                row[3],
            ),
        )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "明天见青禾之前，帮我回顾下这个客户，以及需要重点确认什么。",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    connection.execute("DELETE FROM memories WHERE id LIKE 'pm-week-initial-%'")
    connection.commit()
    return (
        "pm-week-initial-m01" in selected_ids
        and "pm-week-initial-m02" in selected_ids
        and "pm-week-initial-m05" not in selected_ids
        and "pm-week-initial-m11" not in selected_ids
        and "pm-week-initial-m17" not in selected_ids,
        f"selected={selected_ids}",
    )


def check_customer_visit_excludes_cross_project_order_need(server: Any, connection: Any) -> tuple[bool, str]:
    rows = [
        (
            "qinghe-visit",
            "青禾公司销售跟进需求背景",
            "青禾公司是一家工业设备经销商，有18名销售，联系人王敏，销售主管。团队目前主要使用表格管理跟进。王敏希望减少销售忘记回访的问题，但不希望频繁收到通知。客户尚未确认预算，也没有作出购买承诺。",
            "客户跟进提醒",
        ),
        (
            "qinghe-order-export",
            "星河订单台｜客户需要固定字段CSV",
            "青禾每天需要导出订单号、付款金额、订单状态三个字段，用于财务核对。CSV即可，不要求自定义列顺序或复杂筛选。不能据这一次访谈代表其他客户。",
            "星河订单台",
        ),
    ]
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, title, content, memory_type, source, tags_json, project, visibility,
             created_at, updated_at, source_weight, quality_weight, citations)
            VALUES (?, ?, ?, '经验型', '测试记录', ?, ?, 'visible',
                    '2026-09-14T10:00:00+08:00', '2026-09-14T10:00:00+08:00', 1.0, 0.8, 0)
            """,
            (
                row[0],
                row[1],
                row[2],
                json.dumps(["青禾"], ensure_ascii=False),
                row[3],
            ),
        )
    connection.commit()
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "明天见青禾之前，帮我回顾下这个客户，以及需要重点确认什么。",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    return (
        "qinghe-visit" in selected_ids and "qinghe-order-export" not in selected_ids,
        f"selected={selected_ids}",
    )


def check_hybrid_recall_keeps_keyword_fallback(server: Any, connection: Any) -> tuple[bool, str]:
    rows = [
        (
            "qinghe-visit",
            "青禾公司销售跟进需求背景",
            "青禾公司是一家工业设备经销商，有18名销售，联系人王敏，销售主管。团队目前主要使用表格管理跟进。王敏希望减少销售忘记回访的问题，但不希望频繁收到通知。客户尚未确认预算，也没有作出购买承诺。",
            "客户跟进提醒",
        ),
        (
            "qinghe-order-export",
            "星河订单台｜客户需要固定字段CSV",
            "青禾每天需要导出订单号、付款金额、订单状态三个字段，用于财务核对。CSV即可，不要求自定义列顺序或复杂筛选。不能据这一次访谈代表其他客户。",
            "星河订单台",
        ),
    ]
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, title, content, memory_type, source, tags_json, project, visibility,
             created_at, updated_at, source_weight, quality_weight, citations,
             embedding_json, embedding_model, embedding_source)
            VALUES (?, ?, ?, '经验型', '测试记录', ?, ?, 'visible',
                    '2026-09-14T10:00:00+08:00', '2026-09-14T10:00:00+08:00',
                    1.0, 0.8, 0, '', '', '')
            """,
            (
                row[0],
                row[1],
                row[2],
                json.dumps(["青禾"], ensure_ascii=False),
                row[3],
            ),
        )
    connection.commit()

    def fake_embeddings(_settings: dict[str, Any], inputs: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in inputs:
            if "明天见青禾" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif "青禾公司是一家工业设备经销商" in text:
                vectors.append([0.7, 0.714142842, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0])
        return vectors

    original = server.call_embeddings
    server.call_embeddings = fake_embeddings
    settings = {
        "enabled": True,
        "base_url": "http://embedding.test",
        "api_key": "test",
        "embedding_model": "test-low-similarity-embedding",
    }
    diagnostics: dict[str, Any] = {}
    try:
        selected = server.retrieve(
            connection,
            "明天见青禾之前，帮我回顾下这个客户，以及需要重点确认什么。",
            [],
            settings,
            diagnostics,
        )
    finally:
        server.call_embeddings = original

    selected_ids = [item["memory"]["id"] for item in selected]
    vector_scores = {
        item["memory"]["id"]: item["vector_score"]
        for item in selected
    }
    return (
        diagnostics.get("embedding_used") is True
        and "qinghe-visit" in selected_ids
        and "qinghe-order-export" not in selected_ids
        and vector_scores.get("qinghe-visit", 1.0) < server.AUTO_RECALL_SIMILARITY_THRESHOLD,
        f"selected={selected_ids} vectors={vector_scores} diagnostics={diagnostics}",
    )


def check_explicit_export_query_keeps_order_need(server: Any, connection: Any) -> tuple[bool, str]:
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "青禾订单CSV导出需要哪些固定字段？",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    return "qinghe-order-export" in selected_ids, f"selected={selected_ids}"


def check_customer_visit_synonym_query(server: Any, connection: Any) -> tuple[bool, str]:
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "和王敏碰面前，先帮我准备一下青禾背景和要问的问题。",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    return (
        "qinghe-visit" in selected_ids and "qinghe-order-export" not in selected_ids,
        f"selected={selected_ids}",
    )


def check_export_synonym_query(server: Any, connection: Any) -> tuple[bool, str]:
    settings = {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "embedding_model": "",
    }
    selected = server.retrieve(
        connection,
        "青禾财务对账需要哪几项信息？",
        [],
        settings,
    )
    selected_ids = [item["memory"]["id"] for item in selected]
    return "qinghe-order-export" in selected_ids, f"selected={selected_ids}"


def check_customer_visit_model_payload_excludes_order_need(server: Any, connection: Any) -> tuple[bool, str]:
    for key, value in {
        "enabled": "true",
        "base_url": "http://model.test",
        "api_key": "test",
        "model": "test-chat",
    }.items():
        connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
            (f"legacy:{key}", value),
        )
    connection.commit()
    captured_memory_ids: list[str] = []
    captured_special_request = ""

    def fake_model(_settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal captured_memory_ids, captured_special_request
        captured_memory_ids = [item["id"] for item in payload["memories"]]
        captured_special_request = str(payload.get("special_request", ""))
        return {
            "summary": "青禾是一家工业设备经销商，联系人王敏，当前重点是回访提醒和预算确认。",
            "recommendation": "重点确认预算、决策链、提醒频率和试点范围。",
            "reasoning_points": [
                {"text": "来自青禾背景", "memory_ids": ["qinghe-visit"]},
            ],
            "action_items": [],
            "uncertainties": [],
            "_mode": "model",
        }

    original = server.call_model
    server.call_model = fake_model
    try:
        response = server.handle_chat(
            connection,
            {
                "query": "明天见青禾之前，帮我回顾下这个客户，以及需要重点确认什么。",
                "manual_memory_ids": [],
            },
            user_id="legacy",
        )
    finally:
        server.call_model = original

    answer_text = json.dumps(response["answer"], ensure_ascii=False)
    forbidden_fragments = ("订单", "导出", "CSV", "三字段", "财务核对")
    passed = (
        "qinghe-visit" in captured_memory_ids
        and "qinghe-order-export" not in captured_memory_ids
        and "订单导出" in captured_special_request
        and all(fragment not in answer_text for fragment in forbidden_fragments)
    )
    return passed, f"model_memories={captured_memory_ids} special_request={captured_special_request}"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memory-master-retrieval-") as directory:
        os.environ["MEMORY_MASTER_DB"] = str(Path(directory) / "retrieval.db")
        server = load_server()
        connection = server.db()
        cases = json.loads(DATASET.read_text(encoding="utf-8"))
        passed = 0
        for case in cases:
            ok, details = check_case(server, connection, case)
            passed += int(ok)
            print(f'{"PASS" if ok else "FAIL"} {case["name"]}')
            print(f"  {details}")

        cache_ok, cache_details = check_embedding_cache(server, connection)
        passed += int(cache_ok)
        print(f'{"PASS" if cache_ok else "FAIL"} Embedding 缓存')
        print(f"  {cache_details}")
        dedupe_ok, dedupe_details = check_duplicate_deduplication(server, connection)
        passed += int(dedupe_ok)
        print(f'{"PASS" if dedupe_ok else "FAIL"} 重复记忆去重')
        print(f"  {dedupe_details}")
        similar_recent_ok, similar_recent_details = check_similar_memory_prefers_recent(server, connection)
        passed += int(similar_recent_ok)
        print(f'{"PASS" if similar_recent_ok else "FAIL"} 正文完全相同的记忆优选版本')
        print(f"  {similar_recent_details}")
        manual_only_ok, manual_only_details = check_manual_only_boundary(server, connection)
        passed += int(manual_only_ok)
        print(f'{"PASS" if manual_only_ok else "FAIL"} 严格指定不自动补卡')
        print(f"  {manual_only_details}")
        cutoff_ok, cutoff_details = check_cutoff_date_filter(server, connection)
        passed += int(cutoff_ok)
        print(f'{"PASS" if cutoff_ok else "FAIL"} 截至日期过滤未来卡')
        print(f"  {cutoff_details}")
        simulated_date_ok, simulated_date_details = check_simulated_current_date_filter(server, connection)
        passed += int(simulated_date_ok)
        print(f'{"PASS" if simulated_date_ok else "FAIL"} 测试当前日期过滤未来卡')
        print(f"  {simulated_date_details}")
        scope_ok, scope_details = check_retrieval_scope_hard_filters(server, connection)
        passed += int(scope_ok)
        print(f'{"PASS" if scope_ok else "FAIL"} 作用域和可用时间硬过滤')
        print(f"  {scope_details}")
        initial_visit_ok, initial_visit_details = check_initial_qinghe_visit_prefers_monday_scope(server, connection)
        passed += int(initial_visit_ok)
        print(f'{"PASS" if initial_visit_ok else "FAIL"} 青禾会前准备优先周一范围')
        print(f"  {initial_visit_details}")
        customer_visit_ok, customer_visit_details = check_customer_visit_excludes_cross_project_order_need(server, connection)
        passed += int(customer_visit_ok)
        print(f'{"PASS" if customer_visit_ok else "FAIL"} 会前客户回顾不混入跨项目订单导出')
        print(f"  {customer_visit_details}")
        hybrid_fallback_ok, hybrid_fallback_details = check_hybrid_recall_keeps_keyword_fallback(server, connection)
        passed += int(hybrid_fallback_ok)
        print(f'{"PASS" if hybrid_fallback_ok else "FAIL"} Hybrid 召回保留关键词兜底')
        print(f"  {hybrid_fallback_details}")
        explicit_export_ok, explicit_export_details = check_explicit_export_query_keeps_order_need(server, connection)
        passed += int(explicit_export_ok)
        print(f'{"PASS" if explicit_export_ok else "FAIL"} 明确订单导出问题保留导出卡')
        print(f"  {explicit_export_details}")
        customer_visit_synonym_ok, customer_visit_synonym_details = check_customer_visit_synonym_query(server, connection)
        passed += int(customer_visit_synonym_ok)
        print(f'{"PASS" if customer_visit_synonym_ok else "FAIL"} 会前客户回顾支持口语化说法')
        print(f"  {customer_visit_synonym_details}")
        export_synonym_ok, export_synonym_details = check_export_synonym_query(server, connection)
        passed += int(export_synonym_ok)
        print(f'{"PASS" if export_synonym_ok else "FAIL"} 订单导出支持对账口语化说法')
        print(f"  {export_synonym_details}")
        model_payload_ok, model_payload_details = check_customer_visit_model_payload_excludes_order_need(server, connection)
        passed += int(model_payload_ok)
        print(f'{"PASS" if model_payload_ok else "FAIL"} 会前客户回顾不向模型发送跨线订单导出')
        print(f"  {model_payload_details}")
        total = len(cases) + 14
        print(f"{passed}/{total} retrieval checks passed")
        connection.close()
        return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
