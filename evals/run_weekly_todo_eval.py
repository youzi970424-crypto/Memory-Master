#!/usr/bin/env python3
"""Offline checks for weekly todo extraction from stored memories."""

from __future__ import annotations

import importlib.util
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "services" / "api" / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("memory_master_server_weekly_todo", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载本地 API 模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def insert_memory(connection, server, memory_id: str, content: str, timestamp: str) -> None:
    connection.execute(
        """
        INSERT INTO memories
        (id, title, content, memory_type, source, tags_json, visibility,
         created_at, updated_at, source_weight, quality_weight, citations)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            memory_id,
            content,
            "经验型",
            "手动添加",
            "[]",
            "visible",
            timestamp,
            timestamp,
            1.0,
            0.8,
            0,
        ),
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memory-master-weekly-todo-") as directory:
        os.environ["MEMORY_MASTER_DB"] = str(Path(directory) / "weekly-todo.db")
        server = load_server()
        connection = server.db()
        original_config = server.config
        try:
            server.config = lambda _connection, _user_id: {
                "provider": "",
                "base_url": "",
                "api_key": "",
                "model": "",
                "embedding_model": "",
                "enabled": False,
            }
            current = datetime.now(timezone.utc)
            this_week = current.isoformat()
            last_week = (current - timedelta(days=10)).isoformat()
            insert_memory(
                connection,
                server,
                "week-task",
                "本周待办：周三前完成评审材料，负责人是产品经理；周五前同步实验结果。",
                this_week,
            )
            insert_memory(
                connection,
                server,
                "old-task",
                "上周待办：完成历史版本复盘。",
                last_week,
            )
            connection.commit()

            response = server.handle_chat(
                connection,
                {"query": "这周的待办事项有哪些？", "manual_memory_ids": []},
                "legacy",
            )
            todos = response["memory_usage"]["weekly_todos"]
            todo_ids = {item["memory_id"] for item in todos}
            answer = response["answer"]
            checks = [
                (
                    "本周待办被提取",
                    "week-task" in todo_ids and len(todos) >= 1,
                    str(todos),
                ),
                (
                    "上周待办不混入",
                    "old-task" not in todo_ids,
                    str(todo_ids),
                ),
                (
                    "本地兜底返回清单",
                    response["meta"]["generation_mode"] == "local_fallback"
                    and "本周待办" in answer["recommendation"],
                    str(response["meta"]),
                ),
                (
                    "来源记忆进入引用",
                    "week-task" in response["memory_usage"]["selected_memory_ids"]
                    and "week-task" in response["memory_usage"]["manual_memory_ids"] + [
                        item["id"] for item in response["memory_usage"]["citations"]
                    ],
                    str(response["memory_usage"]),
                ),
            ]
        finally:
            server.config = original_config
            connection.close()

        passed = 0
        for name, ok, details in checks:
            passed += int(ok)
            print(f'{"PASS" if ok else "FAIL"} {name}')
            print(f"  {details}")
        print(f"{passed}/{len(checks)} weekly todo checks passed")
        return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
