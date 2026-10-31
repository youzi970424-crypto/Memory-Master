#!/usr/bin/env python3
"""Run the PM one-week closed-loop evaluation in an isolated local database."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "storage" / "memory_master.db"
RUN_DIR = Path(tempfile.mkdtemp(prefix="memory-master-pm-week-"))
TEST_DB = RUN_DIR / "pm_week_eval.db"
RESULT_JSON = ROOT / "mvp_test_pm_week_results.json"

os.environ["MEMORY_MASTER_DB"] = str(TEST_DB)

spec = importlib.util.spec_from_file_location("memory_master_server", ROOT / "services/api/server.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot load services/api/server.py")
server = importlib.util.module_from_spec(spec)
sys.modules["memory_master_server"] = server
spec.loader.exec_module(server)


MEMORIES = {
    "M01": ("周一上午", "9月21日，客户回访提醒项目周会。目前收到销售反馈，部分用户容易忘记回访客户，本轮先验证问题，不承诺上线。待办：产品经理林悦周二前完成澄岳机电和柏川咨询两家客户访谈；设计师陈晨周三前完成提醒方案草图；工程师周航周四前评估实现成本。暂定首版支持站内提醒和邮件提醒，最终范围周四确定。"),
    "M02": ("周一上午", "澄岳机电是一家工业设备经销商，有18名销售，联系人刘倩，销售主管。团队目前主要使用表格管理客户跟进。刘倩希望减少销售忘记回访的问题，但不希望频繁收到通知。客户尚未确认预算，也没有作出购买承诺。"),
    "M03": ("周一下午", "柏川咨询是一家咨询服务公司，有6名客户经理，联系人陈望。团队使用共享日历安排回访。陈望主要关心未完成事项能否在周会上集中查看，目前没有提出邮件提醒需求。"),
    "M04": ("周一下午", "今天学习了JTBD，即Jobs to Be Done。理解重点是：用户在什么情境下，想取得什么进展，目前有什么阻碍。访谈时可以问“上次出现这个问题是什么时候”“当时怎么处理的”，不能仅凭用户说想要一个功能就认定需求已被验证。"),
    "M05": ("周二上午", "9月22日访谈澄岳机电刘倩。刘倩表示，上周18名销售中有4人反馈发生过漏跟进，但没有统计漏跟进总次数，也无法确认造成多少收入损失。目前通过主管在群里提醒解决。刘倩优先希望销售每天看到自己到期未跟进的客户，不希望每条事项都发邮件。她愿意看原型，没有承诺付费。"),
    "M06": ("周二下午", "9月22日访谈柏川咨询陈望。共享日历基本能满足回访提醒，当前更麻烦的是主管周会前要手工整理“哪些事项还没完成”。希望有团队未完成清单。陈望认为新提醒功能暂时不是必须的。至此，澄岳机电和柏川咨询两家访谈均已完成。"),
    "M07": ("周二下午", "9月22日查看虚构竞品TrailCRM的产品演示。观察到支持设置客户下次跟进日期，首页展示今日待跟进清单，支持站内提醒。公开说明显示专业版99元/人/月。未找到邮件提醒、团队周报或提醒频率配置的明确信息。以上仅为当日观察。"),
    "M08": ("周二下午", "9月22日查看虚构竞品NotePilot帮助文档。文档说明支持邮件提醒，可设为每日摘要或逐条发送，支持导出团队待办列表。公开说明显示团队版79元/人/月。没有实际试用，设置流程是否顺畅尚未验证。"),
    "M09": ("周三上午", "9月23日查看虚构竞品LoopDesk演示。观察到客户跟进看板、逾期任务标记和每周团队摘要。没有查到公开价格，也没有找到邮件提醒说明。尚未试用，不能判断实际使用体验。"),
    "M10": ("周三上午", "今天学习了RICE：用覆盖人数Reach、影响程度Impact、信心Confidence与投入Effort辅助比较优先级，常见计算方式为前三项相乘后除以投入。比较时应统一口径。缺少覆盖人数、影响或投入依据时，不应编造数字算出看似精确的排名。"),
    "M11": ("周三下午", "9月23日客户回访提醒设计评审。陈晨已完成提醒方案草图，该待办完成。评审认为用户需要先看到“今天该跟进谁”，不宜默认开启逐条邮件。新增待办：陈晨周四中午前完成站内清单交互稿；林悦周四前向澄岳机电确认可以接受的提醒频率。邮件是否纳入首版仍待周四决定。"),
    "M12": ("周四上午", "9月24日澄岳机电刘倩补充反馈：优先接受进入系统后查看今日待跟进清单，如果以后提供邮件，希望按日汇总，不能逐条轰炸。林悦的提醒频率确认待办已完成。刘倩同时澄清，上周提到的4人是反映发生过漏跟进的销售人数，不是只漏跟进了4次。"),
    "M13": ("周四下午", "9月24日技术评估完成，周航的评估待办完成。估算站内待跟进清单需要3个开发人日，邮件每日摘要额外需要4个开发人日，以上不含测试。本次首版开发预算为4个人日。"),
    "M14": ("周四下午", "9月24日客户回访提醒范围决策：首版只做个人今日待跟进清单和站内提醒，邮件提醒暂不纳入，团队周报也不纳入。此决定替代9月21日暂定的“站内提醒和邮件提醒”范围。依据是当前客户反馈及开发预算。新增待办：林悦周五前更新需求文档；周航周五前补充埋点方案。"),
    "M15": ("周四傍晚", "9月24日补录9月18日售前沟通：当时讨论过客户回访提醒可能同时支持站内提醒和邮件提醒，仅为早期设想，尚未承诺。"),
    "M16": ("周五上午", "9月25日，陈晨已交付站内清单交互稿；林悦已更新需求文档，首版范围按9月24日决定执行。周航反馈埋点方案尚未完成，预计下周一提交。"),
    "M17": ("周五上午", "9月25日澄岳机电刘倩表示，下周愿意安排3名销售试用原型，具体时间未定。新增待办：林悦下周一前联系刘倩确定试用时间。客户仍未确认采购或付费。"),
    "M18": ("周五下午", "9月25日导出报表项目例会。该项目邮件通知功能继续开发，工程师刘洋负责，下周二前完成。这是导出报表项目的安排，与客户回访提醒项目无关。"),
}


CASES = [
    ("T01", "2026年9月21日", "M04", [], "明天见澄岳机电之前，帮我回顾下这个客户，以及需要重点确认什么。", ["M01", "M02"], ["M04"], ["M05", "M18"], [["工业设备", "18名销售"], ["刘倩"], ["表格"], ["预算", "购买承诺"]]),
    ("T02", "2026年9月21日", "M04", [], "客户回访提醒项目本轮有哪些待办？谁负责，什么时候完成？", ["M01"], [], ["M05", "M14"], [["林悦"], ["陈晨"], ["周航"]]),
    ("T03", "2026年9月22日", "M04", [], "我学过一个关注用户想完成什么事的方法，能用它准备澄岳机电访谈吗？", ["M04", "M02"], [], ["M05"], [["JTBD"], ["情境"], ["阻碍"]]),
    ("T04", "2026年9月22日", "M08", [], "澄岳机电和柏川咨询都需要客户提醒吗？", ["M05", "M06"], [], ["M14"], [["澄岳机电"], ["柏川咨询"], ["团队未完成清单"]]),
    ("T05", "2026年9月22日", "M08", [], "澄岳机电上周漏跟进了多少次，损失了多少钱？", ["M05"], [], ["M12"], [["4人", "4名"], ["没有统计", "未知"], ["收入损失"]]),
    ("T06", "2026年9月23日", "M11", [], "周一安排的事情，目前哪些已经完成，哪些还没有完成记录？", ["M01", "M06", "M11"], [], ["M13"], [["访谈"], ["草图"], ["技术评估"]]),
    ("T07", "2026年9月23日", "M11", ["M07", "M08", "M09"], "只根据这三张卡，对比提醒方式、团队能力、价格，并列出还需要验证的内容。", ["M07", "M08", "M09"], [], ["M05", "M10"], [["TrailCRM"], ["NotePilot"], ["LoopDesk"], ["99"], ["79"]]),
    ("T08", "2026年9月23日", "M11", ["M07", "M08"], "只比较所选的这两款竞品，哪一款已明确支持每日邮件摘要？", ["M07", "M08"], [], ["M09"], [["NotePilot"], ["每日摘要"], ["TrailCRM"]]),
    ("T09", "2026年9月23日", "M11", [], "用RICE给个人提醒和团队周报排个优先级。", ["M10", "M05", "M06"], ["M11"], ["M13"], [["RICE"], ["覆盖", "Reach"], ["投入", "Effort"], ["缺少", "不应编造"]]),
    ("T10", "2026年9月24日", "M15", [], "第一版还做邮件提醒吗？为什么？", ["M14", "M12", "M13"], ["M01", "M15"], ["M18"], [["暂不纳入", "不纳入"], ["3个开发人日"], ["4个开发人日"], ["预算"]]),
    ("T11", "2026年9月24日", "M15", [], "我们周一是怎么考虑邮件提醒的，后来为什么改了？", ["M01", "M12", "M13", "M14"], [], ["M18"], [["周一"], ["周四"], ["预算"], ["客户反馈"]]),
    ("T12", "2026年9月24日", "M15", [], "澄岳机电说的4，到底是4人还是4次？", ["M12"], ["M05"], ["M18"], [["4人", "4名"], ["不是", "不是只漏跟进了4次"], ["总次数", "未知"]]),
    ("T13", "2026年9月25日", "M18", [], "客户回访提醒项目还有哪些待办没完成？", ["M14", "M16", "M17"], [], ["M18"], [["埋点"], ["下周一", "9月28日"], ["联系刘倩", "试用时间"]]),
    ("T14", "2026年9月25日", "M18", [], "客户回访提醒项目的邮件功能谁在开发？", ["M14", "M18"], [], [], [["不纳入", "没有"], ["刘洋"], ["导出报表"]]),
    ("T15", "2026年9月25日", "M18", [], "澄岳机电已经答应购买了吗？下周安排是什么？", ["M17"], ["M02", "M05"], ["M18"], [["未承诺", "没有承诺", "未确认采购"], ["3名销售"], ["具体时间", "未定"]]),
    ("T16", "2026年9月25日", "M18", [], "帮我整理这个项目的阶段复盘：本轮完成、最新范围、决策依据、剩余待办和待验证问题。", ["M05", "M06", "M11", "M12", "M13", "M14", "M16", "M17"], ["M01"], ["M18"], [["访谈"], ["邮件提醒", "团队周报"], ["4个人日"], ["埋点"], ["试用"]]),
]


def connect() -> sqlite3.Connection:
    connection = server.db()
    connection.row_factory = sqlite3.Row
    return connection


def copy_settings(connection: sqlite3.Connection) -> dict[str, str]:
    if os.getenv("MEMORY_MASTER_COPY_MODEL_CONFIG", "0") == "0":
        connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('enabled', 'false')")
        connection.commit()
        return {"enabled": "false", "mode": "local-only"}
    copied: dict[str, str] = {}
    if not SOURCE_DB.exists():
        return copied
    source = sqlite3.connect(SOURCE_DB)
    try:
        for key, value in source.execute("SELECT key, value FROM settings").fetchall():
            if key in {"provider", "base_url", "api_key", "model", "embedding_model", "enabled"}:
                copied[key] = value
                connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))
        connection.commit()
        return copied
    finally:
        source.close()


def reset_cards(connection: sqlite3.Connection, until: str) -> None:
    connection.execute("DELETE FROM memories")
    connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('seed_initialized', '1')")
    ids = list(MEMORIES.keys())
    for index, memory_id in enumerate(ids[: ids.index(until) + 1], start=1):
        title, content = MEMORIES[memory_id]
        day = "21"
        if "M05" <= memory_id <= "M08":
            day = "22"
        elif "M09" <= memory_id <= "M11":
            day = "23"
        elif "M12" <= memory_id <= "M15":
            day = "24"
        elif memory_id >= "M16":
            day = "25"
        timestamp = f"2026-09-{day}T{8 + index % 10:02d}:00:00+08:00"
        connection.execute(
            """
            INSERT INTO memories
            (id, title, content, memory_type, source, tags_json, project, workspace_id, project_id, snapshot_id,
             visibility, folder_id, created_at, updated_at, event_at, available_at, last_used_at, source_weight, quality_weight)
            VALUES (?, ?, ?, '经验型', '手动添加', ?, '客户回访提醒', 'default',
                    '客户回访提醒', 'pm-week-20260921', 'visible', NULL, ?, ?, ?, ?, NULL, 1.0, 0.8)
            """,
            (memory_id, title, content, json.dumps(["产品经理闭环评测", memory_id], ensure_ascii=False), timestamp, timestamp, timestamp, timestamp),
        )
    connection.commit()


def flatten_answer(answer: dict[str, Any]) -> str:
    parts = [str(answer.get("summary", "")), str(answer.get("recommendation", ""))]
    for key in ("reasoning_points", "action_items", "uncertainties"):
        for item in answer.get(key, []):
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def group_hit(text: str, group: list[str]) -> bool:
    return any(fragment in text for fragment in group)


def simulated_as_of(simulated_date: str) -> str:
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", simulated_date)
    if not match:
        return "2026-09-21T23:59:59+08:00"
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}T23:59:59+08:00"


def run_case(raw_case: tuple[Any, ...]) -> dict[str, Any]:
    case_id, simulated_date, until, manual, query, expected, allowed, forbidden, answer_groups = raw_case
    connection = connect()
    try:
        copy_settings(connection)
        reset_cards(connection, until)
        started = time.time()
        payload = server.handle_chat(
            connection,
            {
                "query": f"本次测试视当前日期为{simulated_date}。{query}",
                "manual_memory_ids": manual,
                "conversation_history": [],
                "retrieval_scope": {
                    "workspace_id": "default",
                    "project_id": "客户回访提醒",
                    "snapshot_id": "pm-week-20260921",
                    "as_of": simulated_as_of(simulated_date),
                },
            },
        )
        elapsed_ms = round((time.time() - started) * 1000)
    finally:
        connection.close()

    selected = payload.get("memory_usage", {}).get("selected_memory_ids", [])
    answer_text = flatten_answer(payload.get("answer", {}))
    retrieval_ok = all(item in selected for item in expected) and all(item not in selected for item in forbidden)
    answer_ok = all(group_hit(answer_text, group) for group in answer_groups)
    return {
        "case_id": case_id,
        "mode": payload.get("meta", {}).get("generation_mode"),
        "retrieval_mode": payload.get("meta", {}).get("retrieval_mode"),
        "elapsed_ms": elapsed_ms,
        "selected": selected,
        "expected": expected,
        "forbidden": forbidden,
        "unexpected_selected": [item for item in selected if item not in set(expected) | set(allowed) | set(manual)],
        "retrieval_ok": retrieval_ok,
        "answer_ok": answer_ok,
        "overall_ok": retrieval_ok and answer_ok,
        "warnings": payload.get("meta", {}).get("warnings", []),
        "error": payload.get("meta", {}).get("error", ""),
        "answer_excerpt": answer_text[:900],
    }


def main() -> int:
    connection = connect()
    try:
        settings = copy_settings(connection)
    finally:
        connection.close()

    results = [run_case(case) for case in CASES]
    stability_ids = {"T07", "T10", "T13", "T16"}
    stability_runs = []
    for case in CASES:
        if case[0] in stability_ids:
            for run in range(1, 4):
                item = run_case(case)
                item["run"] = run
                stability_runs.append(item)

    summary = {
        "run_dir": str(RUN_DIR),
        "test_db": str(TEST_DB),
        "source_settings": {
            key: ("***" if key == "api_key" and value else value)
            for key, value in settings.items()
        },
        "case_count": len(results),
        "retrieval_passed": sum(1 for item in results if item["retrieval_ok"]),
        "answer_passed": sum(1 for item in results if item["answer_ok"]),
        "overall_passed": sum(1 for item in results if item["overall_ok"]),
        "modes": sorted({str(item.get("mode")) for item in results}),
        "stability": {
            "run_count": len(stability_runs),
            "all_pass_case_count": sum(
                1
                for case_id in stability_ids
                if all(item["overall_ok"] for item in stability_runs if item["case_id"] == case_id)
            ),
            "retrieval_all_pass_case_count": sum(
                1
                for case_id in stability_ids
                if all(item["retrieval_ok"] for item in stability_runs if item["case_id"] == case_id)
            ),
        },
        "results": results,
        "stability_runs": stability_runs,
    }
    RESULT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "result_json": str(RESULT_JSON),
        "case_count": summary["case_count"],
        "retrieval": f"{summary['retrieval_passed']}/{summary['case_count']}",
        "answer": f"{summary['answer_passed']}/{summary['case_count']}",
        "overall": f"{summary['overall_passed']}/{summary['case_count']}",
        "modes": summary["modes"],
        "stability": summary["stability"],
        "failed": [
            {
                "case_id": item["case_id"],
                "retrieval_ok": item["retrieval_ok"],
                "answer_ok": item["answer_ok"],
                "selected": item["selected"],
                "warnings": item["warnings"],
            }
            for item in results
            if not item["overall_ok"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0 if summary["overall_passed"] == summary["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
