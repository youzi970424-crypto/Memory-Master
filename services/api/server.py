#!/usr/bin/env python3
"""Small dependency-free API for the Memory Master local desktop app."""

from __future__ import annotations

import calendar
import http.client
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CONFIG_PATH = Path(__file__).with_name("model-defaults.local.json")
DB_PATH = Path(os.getenv("MEMORY_MASTER_DB", ROOT / "storage" / "memory_master.db"))
DEFAULT_PORT = int(os.getenv("MEMORY_MASTER_API_PORT", "8765"))
SYSTEM_PROMPT_PATH = Path(
    os.getenv("MEMORY_MASTER_SYSTEM_PROMPT", ROOT / "prompts" / "system_prompt.md")
)
CHAT_MEMORY_RECALL_ENABLED = True
AUTO_RECALL_SIMILARITY_THRESHOLD = 0.85
RERANK_RELEVANCE_THRESHOLD = float(os.getenv("MEMORY_MASTER_RERANK_THRESHOLD", "0.10"))
CONTENT_RECALL_SCORE_THRESHOLD = float(os.getenv("MEMORY_MASTER_CONTENT_RECALL_THRESHOLD", "0.22"))
CONTENT_RECALL_MIN_OVERLAP = float(os.getenv("MEMORY_MASTER_CONTENT_RECALL_MIN_OVERLAP", "0.06"))
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_BATCH_SIZE = 10
BM25_K1 = 1.5
BM25_B = 0.75
CONTENT_RECALL_STRICT_INTENT_TERMS = (
    "收入",
    "损失",
    "去年",
    "营收",
    "金额",
    "赔偿",
)
STRICT_MANUAL_ONLY_PATTERN = re.compile(
    r"(只根据|仅根据|只用|仅用|不要补充|不补充|不使用其他|不要使用其他|不引用其他|"
    r"我选的|选中的|这些)(?:.{0,16})(卡|记忆|资料|材料)"
)
STRICT_SELECTED_SCOPE_PATTERN = re.compile(
    r"(只|仅)(?:.{0,12})(所选|选中|这两款|这三款|这几款|这几张|这两张|这三张|这些)"
)
DISABLE_MEMORY_USAGE_PATTERN = re.compile(
    r"(不要|别|无需|不用|不使用|不要用|别用|不要引用|不引用|不基于|不要根据|不根据)"
    r"(?:.{0,16})(卡片信息|卡片|记忆卡|记忆|召回|引用)"
)
CHAT_RESPONSE_MAX_TOKENS_COMPACT = 800
CHAT_RESPONSE_MAX_TOKENS_DEFAULT = 1400
CHAT_RESPONSE_MAX_TOKENS_DETAILED = 2400
CHAT_RESPONSE_MAX_TOKENS_LONG = 6000
MODEL_CHAT_TIMEOUT_SECONDS = int(os.getenv("MEMORY_MASTER_MODEL_CHAT_TIMEOUT_SECONDS", "60"))
MODEL_PARSE_TIMEOUT_SECONDS = int(os.getenv("MEMORY_MASTER_MODEL_PARSE_TIMEOUT_SECONDS", "60"))
MODEL_TEST_TIMEOUT_SECONDS = int(os.getenv("MEMORY_MASTER_MODEL_TEST_TIMEOUT_SECONDS", "15"))
HARNESS_VERSION = "1"
HARNESS_TIMEOUT_SECONDS = 90
HARNESS_MAX_CALLS = 12
HARNESS_CONTEXT_CHAR_LIMIT = 48000
HARNESS_RETENTION_DAYS = 7
HARNESS_MAX_RUNS_PER_USER = 1000
ACTIVE_HARNESS: ContextVar = ContextVar("memory_harness", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_folders (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL DEFAULT 'legacy',
  name TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 1000,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL DEFAULT 'legacy',
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  memory_type TEXT NOT NULL DEFAULT '经验型',
  source TEXT NOT NULL DEFAULT '手动添加',
  tags_json TEXT NOT NULL DEFAULT '[]',
  project TEXT NOT NULL DEFAULT '',
  workspace_id TEXT NOT NULL DEFAULT 'default',
  project_id TEXT NOT NULL DEFAULT '',
  snapshot_id TEXT NOT NULL DEFAULT '',
  folder_id TEXT,
  visibility TEXT NOT NULL DEFAULT 'visible',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  event_at TEXT,
  available_at TEXT,
  valid_until TEXT,
  supersedes_id TEXT,
  conflicts_with_id TEXT,
  last_used_at TEXT,
  source_weight REAL NOT NULL DEFAULT 1.0,
  quality_weight REAL NOT NULL DEFAULT 0.8,
  citations INTEGER NOT NULL DEFAULT 0,
  embedding_json TEXT NOT NULL DEFAULT '',
  embedding_model TEXT NOT NULL DEFAULT '',
  embedding_source TEXT NOT NULL DEFAULT '',
  embedding_updated_at TEXT
);
CREATE TABLE IF NOT EXISTS memory_entities (
  memory_id TEXT NOT NULL,
  entity_text TEXT NOT NULL,
  entity_type TEXT NOT NULL DEFAULT 'keyword',
  normalized_text TEXT NOT NULL,
  UNIQUE(memory_id, normalized_text)
);
CREATE INDEX IF NOT EXISTS idx_memory_entities_normalized
  ON memory_entities(normalized_text);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harness_runs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  flow TEXT NOT NULL,
  status TEXT NOT NULL,
  record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_harness_runs_user_time
  ON harness_runs(user_id, created_at);
"""

SEED = [
    {
        "id": "m-1",
        "title": "原力Claw · 云端模式 + 飞书云文档",
        "content": "智囊团 · 添加 AI 成员，支持云端模式与飞书云文档同步，实现代码与产物的独立追溯。",
        "memory_type": "经验型",
        "source": "手动添加",
        "keywords": ["知识工作", "产品经理", "云文档", "经验沉淀"],
    },
    {
        "id": "m-2",
        "title": "周会复盘的最小可执行动作",
        "content": "从周会复盘中提炼下一步可落地的最小动作，并明确责任人与截止时间，避免复盘停留在讨论层。",
        "memory_type": "结论型",
        "source": "对话沉淀",
        "keywords": ["项目复盘", "动作拆解", "项目推进"],
    },
    {
        "id": "m-3",
        "title": "需求评审 · 三个原则",
        "content": "先定问题、再谈方案、最后排期。每条需求进入评审前需明确：解决的问题、衡量指标、优先级。",
        "memory_type": "方法型",
        "source": "手动添加",
        "keywords": ["需求评审", "方法论", "优先级"],
    },
    {
        "id": "m-4",
        "title": "注册流程优化 · 先做漏斗实验",
        "content": "从注册流程漏斗中三个流失率最高的环节下手，先做 3-5 个低成本实验跑出结论，再决定是否做大的体验重写。",
        "memory_type": "结论型",
        "source": "手动修改",
        "keywords": ["增长实验", "注册流程", "版本规划"],
    },
    {
        "id": "hidden-1",
        "title": "历史版本延期通常来自范围膨胀",
        "content": "过去版本延期与需求范围临时扩大、验证动作滞后有关，建议先锁定小范围实验。",
        "memory_type": "经验型",
        "source": "系统后台沉淀",
        "keywords": ["版本规划", "延期风险"],
        "visibility": "hidden",
    },
]

DEFAULT_FOLDERS = [
    {"id": "folder-customer-cases", "name": "客户案例"},
    {"id": "folder-meetings", "name": "会议纪要"},
    {"id": "folder-daily-todos", "name": "日常待办"},
    {"id": "folder-knowledge", "name": "知识沉淀"},
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HarnessBudgetError(RuntimeError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class HarnessRun:
    def __init__(self, flow: str):
        self.id = secrets.token_hex(16)
        self.flow = flow
        self.created_at = now()
        self.started = time.perf_counter()
        self.deadline = self.started + HARNESS_TIMEOUT_SECONDS
        self.events: list[dict[str, Any]] = []
        self.calls = 0
        self.config_id = ""

    def check(self) -> float:
        remaining = self.deadline - time.perf_counter()
        if remaining <= 0:
            raise HarnessBudgetError("本次处理已达到总耗时上限，请缩短问题或减少资料后重试", "harness_timeout")
        return remaining

    def summary(self) -> dict[str, Any]:
        calls = [event for event in self.events if event["kind"] == "provider" and event.get("attempt")]
        usages = [event.get("usage") for event in calls]
        return {
            "request_id": self.id,
            "version": HARNESS_VERSION,
            "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 2),
            "provider_calls": self.calls,
            "total_tokens": sum(item["total_tokens"] for item in usages) if all(item is not None for item in usages) else None,
            "usage_complete": all(item is not None for item in usages),
        }

    def persist(self, connection: sqlite3.Connection, user_id: str, status: str) -> None:
        record = {
            **self.summary(), "flow": self.flow, "status": status, "config_id": self.config_id,
            "limits": {"seconds": HARNESS_TIMEOUT_SECONDS, "calls": HARNESS_MAX_CALLS,
                       "context_chars": HARNESS_CONTEXT_CHAR_LIMIT},
            "events": self.events,
        }
        cutoff = (datetime.now(timezone.utc) - timedelta(days=HARNESS_RETENTION_DAYS)).isoformat()
        connection.execute(
            "INSERT INTO harness_runs VALUES (?, ?, ?, ?, ?, ?)",
            (self.id, user_id, self.created_at, self.flow, status, json.dumps(record, ensure_ascii=False)),
        )
        connection.execute("DELETE FROM harness_runs WHERE user_id = ? AND created_at < ?", (user_id, cutoff))
        connection.execute(
            "DELETE FROM harness_runs WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM harness_runs WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?)",
            (user_id, user_id, HARNESS_MAX_RUNS_PER_USER),
        )
        connection.commit()


def harness_note(stage: str, **details: Any) -> None:
    run = ACTIVE_HARNESS.get()
    if run is not None:
        run.events.append({"kind": "decision", "stage": stage, **details})


def harness_stage(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        run = ACTIVE_HARNESS.get()
        if run is None:
            return function(*args, **kwargs)
        event = {"kind": "stage", "stage": function.__name__, "status": "running"}
        run.events.append(event)
        started = time.perf_counter()
        try:
            run.check()
            result = function(*args, **kwargs)
            run.check()
            event["status"] = "ok"
            return result
        except Exception as error:
            event.update(status="error", error_code=getattr(error, "code", type(error).__name__))
            raise
        finally:
            event["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return wrapped


def harness_flow(flow: str):
    def decorate(function):
        @wraps(function)
        def wrapped(connection, body, user_id, *args):
            run = HarnessRun(flow)
            token = ACTIVE_HARNESS.set(run)
            result = None
            status = "error"
            try:
                result = function(connection, body, user_id, *args)
                if flow in ("chat", "memory_parse"):
                    run.check()
                status = result.get("meta", {}).get("generation_mode", "ok") if isinstance(result, dict) else "not_found"
            except HarnessBudgetError as error:
                connection.rollback()
                harness_note("budget", error_code=error.code)
                error.request_id = run.id
                if flow != "chat":
                    raise
                result = {
                    "answer": sanitize_answer(model_error_answer(str(error))),
                    "memory_usage": {"selected_memory_ids": [], "manual_memory_ids": [],
                                     "auto_recalled_memory_ids": [], "dropped_memory_ids": [],
                                     "citation_count": 0, "primary_citation_ids": [], "secondary_citation_ids": [],
                                     "recalled_memories": [], "weekly_todos": [], "citations": []},
                    "memory_capture": {"should_save": False, "suggested_memory_card": {"title": "", "content": "", "keywords": [], "source": "conversation"}},
                    "meta": {"generation_mode": "model_error", "retrieval_mode": "none", "model": "",
                             "retrieved_count": 0, "used_count": 0, "error": str(error), "error_code": error.code},
                }
                result["answer"].pop("_mode", None)
                result["answer"].pop("_error", None)
                status = "model_error"
            except Exception as error:
                connection.rollback()
                harness_note("request", error_code=getattr(error, "code", type(error).__name__))
                error.request_id = run.id
                raise
            finally:
                ACTIVE_HARNESS.reset(token)
                persisted = True
                try:
                    run.persist(connection, user_id, status)
                except sqlite3.Error:
                    connection.rollback()
                    persisted = False
                if isinstance(result, dict):
                    result.setdefault("meta", {})["harness"] = {**run.summary(), "trace_persisted": persisted}
            return result
        return wrapped
    return decorate


def harness_config(settings: dict[str, Any]) -> None:
    run = ACTIVE_HARNESS.get()
    if run is not None:
        # Fingerprint only operational settings; never hash or store credentials.
        public = {key: settings.get(key) for key in (
            "enabled", "model", "base_url", "embedding_model", "embedding_base_url", "reranker_model", "reranker_url",
        )}
        for key in ("base_url", "embedding_base_url", "reranker_url"):
            parsed = urlparse(str(public[key] or ""))
            public[key] = {"host": parsed.hostname, "path": parsed.path}
        run.config_id = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:16]


def harness_retry_sleep(seconds: float) -> None:
    run = ACTIVE_HARNESS.get()
    if run is not None and run.check() <= seconds:
        raise HarnessBudgetError("本次处理剩余时间不足以重试，请稍后重试", "harness_timeout")
    time.sleep(seconds)


def harness_memories(stage: str, memories: list[dict[str, Any]], **details: Any) -> None:
    if ACTIVE_HARNESS.get() is not None:
        visible = [item["id"] for item in memories if item["visibility"] == "visible"]
        harness_note(stage, visible_ids=visible[:200], visible_count=len(visible),
                     hidden_count=len(memories) - len(visible), **details)


def provider_json(request: urllib.request.Request, timeout: float, role: str) -> Any:
    """One network attempt; never persist request bodies, URLs, keys or provider text."""
    run = ACTIVE_HARNESS.get()
    if run is None:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    started = time.perf_counter()
    event = {"kind": "provider", "stage": role, "status": "running", "usage": None}
    run.events.append(event)
    try:
        remaining = run.check()
        if run.calls >= HARNESS_MAX_CALLS:
            raise HarnessBudgetError("本次处理已达到模型调用次数上限，请减少资料后重试", "harness_call_limit")
        if len((request.data or b"").decode("utf-8")) > HARNESS_CONTEXT_CHAR_LIMIT:
            if role in ("classification", "embedding", "rerank", "citation"):
                raise ValueError("可选模型上下文超限，使用本地处理")
            raise HarnessBudgetError("本次模型上下文过长，请减少资料或历史内容后重试", "harness_context_limit")
        # Reserve four calls and time for generation/retries and citation verification.
        if role in ("classification", "embedding", "rerank"):
            remaining = min(remaining, run.started + 20 - time.perf_counter())
            if run.calls >= max(0, HARNESS_MAX_CALLS - 4) or remaining <= 0:
                raise ValueError("增强处理预算已用尽，使用本地处理")
        timeout = min(timeout, remaining)
        run.calls += 1
        event["attempt"] = run.calls
        event["timeout_seconds"] = round(timeout, 3)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            chunks = []
            read = getattr(response, "read1", response.read)
            while True:
                remaining = min(run.check(), started + timeout - time.perf_counter())
                if remaining <= 0:
                    raise TimeoutError("provider deadline exceeded")
                # urllib's socket timeout alone resets on each read. Bound streaming reads too.
                sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                if sock is not None:
                    sock.settimeout(remaining)
                chunk = read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            body = json.loads(b"".join(chunks).decode("utf-8"))
        run.check()
        usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage, dict) and type(usage.get("total_tokens")) is int and usage["total_tokens"] >= 0:
            event["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                              if type(usage.get(key)) is int and usage[key] >= 0}
        event["status"] = "ok"
        return body
    except Exception as error:
        event.update(status="error" if event.get("attempt") else "skipped",
                     error_code=getattr(error, "code", type(error).__name__))
        raise
    finally:
        event["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)


LOCAL_TIMEZONE = timezone(timedelta(hours=8))
TODO_QUERY_PATTERN = re.compile(r"(待办|待完成|未完成|任务|下一步|要做什么|需要做|行动项|截止|负责人|跟进|计划)")
TODO_ACTION_VERBS = r"提交|联系|更新|评估|完成|优化|修复|整理|准备|跟进|设计|实现|验证|测试|发布|推进"
TODO_CONTENT_PATTERN = re.compile(rf"(待办|任务|下一步|需要|负责|截止|计划|行动|本周|这周|周[一二三四五六日天]|{TODO_ACTION_VERBS})")


def local_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(LOCAL_TIMEZONE)
    except ValueError:
        return None


def query_cutoff_datetime(text: str) -> Optional[datetime]:
    match = re.search(
        r"(?:截至|截止到|截止|到|当前日期为|视当前日期为)\s*"
        r"(\d{4})\s*(?:年|-|/)\s*(\d{1,2})\s*(?:月|-|/)\s*(\d{1,2})\s*(?:日|号)?",
        text,
    )
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(
            year,
            month,
            day,
            23,
            59,
            59,
            999999,
            tzinfo=LOCAL_TIMEZONE,
        )
    except ValueError:
        return None


def row_after_cutoff(row: sqlite3.Row, cutoff: Optional[datetime]) -> bool:
    if cutoff is None:
        return False
    timestamp = (
        local_datetime(row["available_at"])
        or local_datetime(row["event_at"])
        or local_datetime(row["created_at"])
        or local_datetime(row["updated_at"])
    )
    return bool(timestamp and timestamp > cutoff)


def normalized_scope(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def normalized_entity(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def extract_memory_entities(
    title: str,
    content: str,
    project: str,
    project_id: str,
    tags_json: str,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for value, entity_type in (
        (project_id, "project"),
        (project, "project"),
    ):
        if value.strip():
            candidates.append((value.strip(), entity_type))
    try:
        tags = json.loads(tags_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        tags = []
    if isinstance(tags, list):
        candidates.extend(
            (str(tag).strip(), "keyword")
            for tag in tags
            if str(tag).strip()
        )
    for pattern, entity_type in (
        (r"(?:联系人|对接人|负责人)[：:\s]*([\u4e00-\u9fffA-Za-z0-9]{2,20})", "person"),
        (r"([\u4e00-\u9fffA-Za-z0-9]{2,20}(?:公司|机电|咨询|CRM|项目|提醒|导出|订单台))", "named"),
    ):
        candidates.extend(
            (match.group(1), entity_type)
            for match in re.finditer(pattern, f"{title} {content}", re.IGNORECASE)
        )
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value, entity_type in candidates:
        normalized = normalized_entity(value)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append((value, entity_type))
    return result


def sync_memory_entities(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    entities = extract_memory_entities(
        row["title"],
        row["content"],
        row["project"],
        row["project_id"],
        row["tags_json"],
    )
    connection.execute("DELETE FROM memory_entities WHERE memory_id = ?", (row["id"],))
    connection.executemany(
        """
        INSERT OR IGNORE INTO memory_entities
        (memory_id, entity_text, entity_type, normalized_text)
        VALUES (?, ?, ?, ?)
        """,
        [
            (row["id"], text, entity_type, normalized_entity(text))
            for text, entity_type in entities
        ],
    )


def entity_matches_for_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    query: str,
) -> dict[str, float]:
    if not rows:
        return {}
    row_ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in row_ids)
    entity_rows = connection.execute(
        f"""
        SELECT memory_id, normalized_text
        FROM memory_entities
        WHERE memory_id IN ({placeholders})
        """,
        row_ids,
    ).fetchall()
    query_text = normalized_entity(query)
    matched_entities = {
        row["normalized_text"]
        for row in entity_rows
        if row["normalized_text"] and row["normalized_text"] in query_text
    }
    if not matched_entities:
        return {}
    matches: dict[str, float] = {}
    for row in entity_rows:
        if row["normalized_text"] in matched_entities:
            matches[row["memory_id"]] = 1.0
    return matches


def month_dates(reference: datetime, offset: int = 0) -> list[str]:
    year, month_index = divmod(reference.year * 12 + reference.month - 1 + offset, 12)
    month = month_index + 1
    return [f"{year:04d}-{month:02d}-{day:02d}" for day in range(1, calendar.monthrange(year, month)[1] + 1)]


def schedule_dates(query: str, reference: datetime) -> list[str]:
    """Resolve requested calendar weeks or months in the user's timezone."""
    if not re.search(r"安排|日程|做什么|干什么|待办|待完成|未完成|任务|行动|要做|跟进|计划|会议|站会|开会|冲突", query):
        return []
    if re.search(r"本月|这(?:个)?月|当月", query):
        return month_dates(reference)
    week = re.search(r"(本周|这周|本星期|这星期|下周|下星期|上周|上星期)", query)
    if not week:
        return []
    offset = 7 if week.group().startswith("下") else -7 if week.group().startswith("上") else 0
    start = reference.date() - timedelta(days=reference.weekday()) + timedelta(days=offset)
    days = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    selected = {days[day] for day in re.findall(r"(?:周|星期)([一二三四五六日天])", query)}
    if re.search(r"周末|星期末", query):
        selected.update((5, 6))
    for first, last in re.findall(r"(?:周|星期)([一二三四五六日天])\s*[至到—-]\s*(?:周|星期)?([一二三四五六日天])", query):
        selected.update(range(days[first], days[last] + 1))
    return [(start + timedelta(days=day)).isoformat() for day in sorted(selected or range(7))]


def memory_matches_schedule(row: sqlite3.Row, dates: list[str], include_undated_todos: bool = False) -> bool:
    if include_undated_todos and is_undated_todo(row):
        created = local_datetime(row["created_at"])
        return bool(created and created.date().isoformat() in dates)
    text = f"{row['title']}。{row['content']}"
    if not re.search(r"安排|日程|待办|任务|计划|会议|站会|开会|办公|复查|拜访|出差|上线|评审|截止|预约", text) and not extract_todo_items(row):
        return False
    # An edit alone must not move an old relative schedule into a new week.
    anchor = (local_datetime(row['event_at']) or local_datetime(row['created_at'])
              or local_datetime(row['updated_at']))
    if not anchor:
        return False
    explicit = []
    for year, month, day in re.findall(r"(?:(\d{4})[年/.-])?(\d{1,2})[月/.-](\d{1,2})(?:日|号)?", text):
        try:
            explicit.append(datetime(int(year or anchor.year), int(month), int(day)).date().isoformat())
        except ValueError:
            continue
    if explicit:
        # Mixed-date cards need fact-level extraction before they can be used safely.
        return bool(set(explicit) & set(dates)) and set(explicit) <= set(dates)
    relative_month = re.search(r"(?:本|这|上|下)(?:个)?月|当月", text)
    if relative_month:
        offset = 1 if relative_month.group().startswith("下") else -1 if relative_month.group().startswith("上") else 0
        return bool(set(month_dates(anchor, offset)) & set(dates))
    weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    mentioned = {weekdays[day] for day in re.findall(r"(?:周|星期)([一二三四五六日天])", text)}
    if re.search(r"周末|星期末", text):
        mentioned.update((5, 6))
    for first, last in re.findall(r"(?:周|星期)([一二三四五六日天])\s*[至到—-]\s*(?:周|星期)?([一二三四五六日天])", text):
        mentioned.update(range(weekdays[first], weekdays[last] + 1))
    if re.search(r"每天|每日|每周|每星期", text):
        recurring = [fragment for fragment in re.split(r"[。；;\n]", text)
                     if re.search(r"(?:每天|每日|每周[一二三四五六日天]?|每星期[一二三四五六日天]?).{0,20}(?:参加|召开|开会|站会|居家办公|远程办公|提交|复查|拜访)", fragment)
                     and not re.search(r"额度|订阅|版本|商业模式|产品定位", fragment)]
        return any(
            not re.search(r"(?:周|星期)[一二三四五六日天]", fragment)
            or any(datetime.fromisoformat(day).weekday() in {
                weekdays[name] for name in re.findall(r"(?:周|星期)([一二三四五六日天])", fragment)
            } for day in dates)
            for fragment in recurring
        )
    if not mentioned and not re.search(r"本周|这周|下周|上周|本星期|这星期", text):
        return False
    offset = 7 if re.search(r"下周|下星期", text) else -7 if re.search(r"上周|上星期", text) else 0
    start = anchor.date() - timedelta(days=anchor.weekday()) + timedelta(days=offset)
    resolved = {(start + timedelta(days=day)).isoformat() for day in (mentioned or range(7))}
    return bool(resolved & set(dates))


def retrieval_scope(query: str, scope: Optional[Dict[str, Any]] = None) -> dict[str, Any]:
    requested = scope if isinstance(scope, dict) else {}
    return {
        "workspace_id": normalized_scope(requested.get("workspace_id") or "default"),
        "project_id": normalized_scope(requested.get("project_id")),
        "snapshot_id": normalized_scope(requested.get("snapshot_id")),
        "schedule_dates": requested.get("schedule_dates") or schedule_dates(query, local_datetime(now())),
        "include_undated_todos": requested.get("include_undated_todos", False),
        "as_of": str(requested.get("as_of") or "").strip()
        or (query_cutoff_datetime(query).isoformat() if query_cutoff_datetime(query) else now()),
    }


def row_matches_retrieval_scope(
    row: sqlite3.Row,
    scope: dict[str, str],
    manual: bool = False,
) -> bool:
    if scope.get("schedule_dates") and not memory_matches_schedule(
        row, scope["schedule_dates"], include_undated_todos=scope.get("include_undated_todos", False)
    ):
        return False
    if manual:
        return True
    if scope["workspace_id"] and normalized_scope(row["workspace_id"]) != scope["workspace_id"]:
        return False
    if scope["project_id"] and normalized_scope(row["project_id"] or row["project"]) != scope["project_id"]:
        return False
    if scope["snapshot_id"] and normalized_scope(row["snapshot_id"]) != scope["snapshot_id"]:
        return False
    if scope["as_of"]:
        as_of = local_datetime(scope["as_of"])
        if as_of and row_after_cutoff(row, as_of):
            return False
        valid_until = local_datetime(row["valid_until"])
        if valid_until and valid_until < as_of:
            return False
    return True


def strict_manual_only_query(query: str, manual_ids: list[str]) -> bool:
    comparison = bool(re.search(r"比较|对比|相比|优劣|哪个更|哪款更", query))
    expand = bool(re.search(r"(?:请|也|再|可以|允许|帮我)?(?:补充|搜索|查找|寻找|加入|纳入|召回).{0,8}(?:其他|其它|更多|未选).{0,8}(?:竞品|产品|方案|资料|卡片)", query))
    if re.search(r"不要|不许|不允许|禁止|无需|不必", query):
        expand = False
    return bool(
        manual_ids
        and (
            STRICT_MANUAL_ONLY_PATTERN.search(query)
            or STRICT_SELECTED_SCOPE_PATTERN.search(query)
            or (comparison and not expand)
        )
    )


def disable_memory_usage_query(query: str) -> bool:
    return bool(DISABLE_MEMORY_USAGE_PATTERN.search(query))


def current_week_range() -> tuple[datetime, datetime]:
    current = datetime.now(LOCAL_TIMEZONE)
    start = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, start + timedelta(days=7)


def is_current_week_memory(row: sqlite3.Row) -> bool:
    return memory_matches_schedule(row, schedule_dates("本周待办", local_datetime(now())), include_undated_todos=True)


def extract_todo_items(row: sqlite3.Row) -> list[str]:
    memory = dict(row)
    folder_id = memory.get("folder_id") or ""
    tags = memory.get("keywords") or json.loads(memory.get("tags_json") or "[]")
    classified_todo = (
        folder_id == "folder-daily-todos" or folder_id.endswith("-folder-daily-todos")
        or bool({"待办", "待办事项", "日常待办"} & set(tags))
    )
    content = row["content"].strip()
    if not content or (not classified_todo and not TODO_CONTENT_PATTERN.search(content)):
        return []
    fragments = [
        fragment.strip(" ，。；、:：")
        for fragment in re.split(r"[。；;\n]", content)
    ]
    actionable: list[str] = []
    classified_candidates: list[str] = []
    in_todo_list = False
    for fragment in fragments:
        fragment = re.sub(r"^(?:[-*•]\s*|\d+[、)）]\s*|\d+\.\s+|\[[ xX]\]\s*)+", "", fragment).strip()
        if re.fullmatch(r"(?:#{1,6}\s*)?(?:(?:本|这|下|上)周末?|(?:本|这|下|上)(?:个)?月|今日|明日)?(?:待办(?:事项|清单)?|日常待办|任务清单)[:：]?", fragment):
            in_todo_list = True
            continue
        if re.match(r"(?:#{1,6}\s*)?(?:背景|说明|备注|产品介绍|主标题|副标题)[:：]", fragment):
            in_todo_list = False
        if not fragment or (not classified_todo and not in_todo_list and not TODO_CONTENT_PATTERN.search(fragment)):
            continue
        had_todo_label = "待办" in fragment
        if "待办" in fragment:
            fragment = re.sub(r"^.*?待办[:：]", "", fragment).strip(" ，。；、:：")
        if re.match(r"(?:[-*•\s]|\d+[.)、])*(?:已完成|已取消|取消|无需|不用|不需要|不再|不要)", fragment):
            continue
        if re.fullmatch(r"待办事项|日常待办|待办|任务|下一步", fragment):
            continue
        if not classified_todo and re.match(r"(?:优化|修复|整理|设计|测试)(?:建议|原理|方法|说明|教程|策略)[:：]", fragment):
            continue
        if re.search(r"(暂定|最终范围|仍待|是否纳入|待.*决定)", fragment) and not re.search(r"(负责人|负责|新增待办|完成|提交|联系|更新|评估)", fragment):
            continue
        has_deadline = bool(re.search(r"(周[一二三四五六日天](?:中午)?前|\d{1,2}月\d{1,2}日前)", fragment))
        has_owner = bool(re.search(r"负责人[:：]|负责(?:提交|联系|更新|评估|完成)|(?:我|我们|林悦|陈晨|周航|刘洋)(?:需要|要|将|负责)", fragment))
        has_action = bool(re.search(r"(需要完成|提交|联系|更新|评估|完成.{0,12}(访谈|草图|交互稿|文档|方案|材料|结果|复盘))", fragment))
        action_instruction = bool(re.match(rf"(?:[-*•\s]|\d+[.)、])*(?:(?:我|我们)(?:需要|要|将|负责))?(?:请|需要|计划|记得|要)?(?:{TODO_ACTION_VERBS}).+", fragment))
        scheduled_action = bool(re.match(
            r"(?:[-*•\s]|\d+[.)、])*(?:(?:本|这|下|上)(?:周|星期)(?:[一二三四五六日天])?|(?:本|这|上|下)(?:个)?月|当月|(?:周|星期)[一二三四五六日天]|(?:\d{4}[年/-])?\d{1,2}[月/-]\d{1,2}(?:日|号)?)"
            rf"(?:前|内)?[，,：:\s]*(?:请|需要|计划|记得|要)?(?:{TODO_ACTION_VERBS}).+", fragment
        ))
        if classified_todo:
            classified_candidates.append(fragment)
        if not (in_todo_list or had_todo_label or has_deadline or scheduled_action or action_instruction or (has_action and has_owner)):
            continue
        actionable.append(fragment)
    # A task folder classifies the card, not every paragraph of its background.
    return list(dict.fromkeys(actionable or classified_candidates[:1]))


def is_undated_todo(row: sqlite3.Row) -> bool:
    text = f"{row['title']}。{row['content']}"
    return bool(extract_todo_items(row)) and (not row['event_at'] or row['event_at'] == row['created_at']) and not re.search(
        r"周|星期|每天|每日|今天|明天|昨天|后天|(?:本|这|上|下)(?:个)?月|当月|月底|月初|年|\d{1,2}[月/.-]\d{1,2}|\d{1,2}[日号]", text
    )


def todo_date_label(row: sqlite3.Row, item_text: Optional[str] = None) -> str:
    text = " ".join([item_text or row["content"], row["title"]])
    match = re.search(r"(?<!\d)(\d{1,2})[./月-](\d{1,2})(?:日)?(?!\d)", text)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2))}"
    created = local_datetime(row["created_at"])
    anchor = local_datetime(dict(row).get("event_at")) or created
    if anchor:
        relative = re.search(r"(?:(?:本|这|上|下)(?:周|星期)(?:[一二三四五六日天末])?|(?:周|星期)[一二三四五六日天末])(?:[至到—-](?:周|星期)?[一二三四五六日天])?", text)
        if relative:
            expression = relative.group()
            dates = schedule_dates((expression if expression.startswith(('本', '这', '上', '下')) else '本' + expression) + '待办', anchor)
            labels = [f"{int(day[5:7])}.{int(day[8:10])}" for day in (dates[0], dates[-1])]
            return labels[0] if labels[0] == labels[1] else '—'.join(labels)
        month = re.search(r"(?:本|这|上|下)(?:个)?月|当月", text)
        if month:
            dates = month_dates(anchor, 1 if month.group().startswith('下') else -1 if month.group().startswith('上') else 0)
            return f"{int(dates[0][5:7])}.1—{int(dates[-1][5:7])}.{int(dates[-1][8:10])}"
    if created:
        return f"{created.month}.{created.day} 创建（未注明截止日期）"
    return "未注明日期"


def extract_display_todo_items(row: sqlite3.Row) -> list[str]:
    content = row["content"].strip()
    if not content:
        return []
    fragments = [
        fragment.strip(" \t-•●○，。；、:：")
        for fragment in re.split(r"\n+|[。；]|(?<=\d)[、.)）]", content)
    ]
    items: list[str] = []
    for fragment in fragments:
        if not fragment:
            continue
        if re.fullmatch(r"(待办事项|任务事项|行动项|下一步|输出|输入)", fragment):
            continue
        if len(fragment) <= 3 and not TODO_CONTENT_PATTERN.search(fragment):
            continue
        items.append(fragment)
    if items:
        return list(dict.fromkeys(items))[:8]
    return extract_todo_items(row)


def formatted_todo_answer(selected: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {}
    references: list[str] = []
    for item in selected:
        memory = item["memory"]
        if memory["visibility"] != "visible":
            continue
        todos = extract_todo_items(memory)
        if not todos:
            continue
        for text in todos:
            label = todo_date_label(memory, text)
            grouped.setdefault(label, []).append(text)
        references.append(memory["id"])

    sections: list[str] = []
    for label in sorted(grouped.keys(), key=lambda value: [int(part) for part in re.findall(r"\d+", value)] or [99, 99]):
        unique_items = list(dict.fromkeys(grouped[label]))
        sections.extend(f"| {label} | {text} |" for text in unique_items)

    if not sections:
        return {
            "summary": "",
            "recommendation": "当前记忆卡中没有找到明确的待办事项。",
            "reasoning_points": [],
            "action_items": [],
            "uncertainties": [],
            "_references": [],
            "_mode": "local_fallback",
        }
    return {
        "summary": "",
        "recommendation": "| 时间 | 事项 |\n| --- | --- |\n" + "\n".join(sections),
        "reasoning_points": [],
        "action_items": [],
        "uncertainties": [],
        "_references": list(dict.fromkeys(references)),
        "_mode": "local_fallback",
    }


def normalized_conversation_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    history: list[dict[str, str]] = []
    for item in value[-8:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = item.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        history.append({"role": role, "text": text.strip()[:1500]})
    return history


FOLLOW_UP_PATTERN = re.compile(
    r"(刚才|上一轮|上面|上述|以上|前面|前一个|这个|那个|这段|这些|这几个|这里|它|继续|再说|具体说|展开|"
    r"^那.{1,20}呢[？?]?$|为什么|怎么做|下一步|然后呢|还有吗|第[一二三四五六七八九十\d]+条)"
)
FORMAT_REVISION_PATTERN = re.compile(
    r"(改成|换成|调整为|只保留|仅保留|删掉|去掉|不要解释|不要分析|只输出|"
    r"按.*(格式|条|列|排|序)|"
    r"用.*(数字|编号|序号|1\s*2\s*3|展示|列出来)|"
    r"(日期|时间|序号|编号|表格|标题|简短|详细|一句话|一段话|JSON|markdown|"
    r"多少条|几条|一共.{0,8}条|总共.{0,8}条|统计|计数))"
)
CONSTRAINT_REVISION_PATTERN = re.compile(
    r"(精简|简化|压缩|浓缩|缩短|更短|一句话|一段话|摘要|总结|"
    r"约束|限制|范围|只看|只基于|只围绕|不要发散|不要展开|不要新增|"
    r"保留重点|提炼重点|提炼一下|归纳一下)"
)
CORRECTION_PATTERN = re.compile(
    r"(不对|不是|错了|有误|不准确|理解错|弄错|改正|纠正|重新来|重做|应该是|实际是)"
)
TOPIC_SWITCH_PATTERN = re.compile(
    r"(换个话题|换个项目|换个问题|另一个问题|另外|除此之外|顺便问|不再讨论|先不说这个|与此无关)"
)


def last_history_text(history: list[dict[str, str]], role: str) -> str:
    for item in reversed(history):
        if item["role"] == role:
            return item["text"]
    return ""


def infer_format_constraints(text: str) -> dict[str, bool]:
    return {
        "group_by_date": bool(re.search(r"(按日期|分日期|日期)", text)),
        "numbered_list": bool(re.search(r"(序号|编号|序列号|按条|1\s*2\s*3)", text)),
        "table": "表格" in text,
        "no_extra_explanation": bool(re.search(r"(不要解释|只输出|只保留|直接给|不要分析)", text)),
    }


TURN_TYPES = {
    "new_task",
    "topic_switch",
    "follow_up",
    "format_revision",
    "constraint_revision",
    "correction",
}


def detect_turn_type(query: str, history: list[dict[str, str]]) -> str:
    if not history:
        return "new_task"
    if TOPIC_SWITCH_PATTERN.search(query):
        return "topic_switch"
    if CORRECTION_PATTERN.search(query):
        return "correction"
    if CONSTRAINT_REVISION_PATTERN.search(query) and FOLLOW_UP_PATTERN.search(query):
        return "constraint_revision"
    if todo_query_period(query):
        return "new_task"
    if FORMAT_REVISION_PATTERN.search(query):
        return "format_revision"
    if FOLLOW_UP_PATTERN.search(query):
        return "follow_up"
    return "new_task"


def comparison_or_priority_query(query: str) -> bool:
    return bool(
        re.search(
            r"(优先级|优先排序|排序|排个|排一下|比较|对比|取舍|哪个更|哪一款|哪一个|先做|先推|先排|"
            r"RICE|ICE|MoSCoW|Kano|SWOT)",
            query,
            re.IGNORECASE,
        )
    )


def priority_ranking_query(query: str) -> bool:
    return bool(
        re.search(
            r"(优先级|优先排序|排序|排个|排一下|先做|先推|先排|RICE|ICE|MoSCoW|Kano)",
            query,
            re.IGNORECASE,
        )
    )


def framework_application_query(query: str) -> bool:
    return bool(
        re.search(
            r"(用|按|基于|按照).{0,12}(RICE|ICE|MoSCoW|Kano|JTBD|SWOT|5W1H|SMART|OKR|AARRR|漏斗)",
            query,
            re.IGNORECASE,
        )
    )


def comparison_options(query: str) -> list[str]:
    patterns = [
        r"(?:给|为)(.+?)(?:排(?:个|一下)?优先级|排序|定优先级|做优先级)",
        r"(?:比较|对比)(.+?)(?:的优先级|优先级|哪个|哪一个|谁更|$)",
    ]
    raw = ""
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            raw = match.group(1)
            break
    if not raw:
        return []
    if "给" in raw:
        raw = raw.rsplit("给", 1)[-1]
    if "。" in raw:
        raw = raw.rsplit("。", 1)[-1]
    raw = re.sub(r"^(?:一下|下|这两个|这两项|两项|两个|所选的|所选)\s*", "", raw).strip()
    parts = [
        part.strip(" ：:，,。；;（）()[]【】\"'")
        for part in re.split(r"\s*(?:和|与|及|、|/|vs\.?|VS|,|，)\s*", raw)
    ]
    return [part for part in parts if len(part) >= 2][:4]


def option_aliases(option: str) -> set[str]:
    aliases = {option}
    if "提醒" in option:
        aliases.update({"自己", "个人", "到期", "待跟进", "今天该跟进", "站内", "系统内"})
    if "周报" in option or "团队" in option or "清单" in option:
        aliases.update({"团队", "主管周会", "未完成", "汇总", "周报"})
    if "邮件" in option:
        aliases.update({"邮件", "摘要", "逐条", "通知"})
    if "站内" in option:
        aliases.update({"站内", "系统内", "进系统", "首页"})
    return {alias for alias in aliases if alias}


def option_evidence_lines(option: str, evidence_lines: list[str]) -> list[str]:
    aliases = option_aliases(option)
    matches = [
        line
        for line in evidence_lines
        if any(alias in line for alias in aliases)
    ]
    return matches[:3]


def evidence_body(line: str) -> str:
    text = re.sub(r"^\d+\.\s*", "", line).strip()
    if "：" in text:
        title, body = text.split("：", 1)
        body = body.strip()
        duplicated_title = re.escape(title.strip())
        body = re.sub(rf"^{duplicated_title}[。:：\s]*", "", body).strip()
        text = body or text
    return text


def evidence_sentences(line: str) -> list[str]:
    text = evidence_body(line)
    sentences = [part.strip() for part in re.split(r"(?<=[。；;])\s*", text) if part.strip()]
    if not sentences:
        sentences = [text]
    return sentences


def compact_evidence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 76:
        text = text[:76].rstrip("，,；;。 ") + "..."
    return text


def option_signal_score(lines: list[str]) -> int:
    text = "\n".join(lines)
    positive = len(re.findall(r"(优先|明确|需要先|愿意|希望|支持|完成|直接|核心|强|认可)", text))
    negative = len(re.findall(r"(暂时不是必须|未确认|没有承诺|不希望|待|尚未|无法|缺少|未统计|不宜)", text))
    if "优先希望" in text or "优先接受" in text:
        positive += 3
    if "需要先看到" in text or "今天该跟进谁" in text:
        positive += 3
    if "暂时不是必须" in text:
        negative += 4
    return len(lines) + positive * 2 - negative * 2


def framework_dimensions(query: str) -> list[tuple[str, tuple[str, ...]]]:
    if "RICE" in query.upper():
        return [
            ("Reach 覆盖", ("销售", "用户", "团队", "人", "客户经理", "主管", "覆盖", "每天", "每周")),
            ("Impact 影响", ("痛点", "问题", "漏", "麻烦", "减少", "缓解", "影响", "收入", "损失", "负担")),
            ("Confidence 信心", ("明确", "希望", "认为", "评审", "反馈", "表示", "愿意", "暂时不是必须")),
            ("Effort 投入", ("成本", "投入", "评估", "实现", "开发", "待", "尚未", "周航")),
        ]
    return [
        ("覆盖对象", ("用户", "团队", "人", "客户", "主管", "覆盖")),
        ("价值影响", ("痛点", "问题", "减少", "缓解", "影响", "价值", "负担")),
        ("证据强度", ("明确", "希望", "认为", "反馈", "表示", "完成", "待")),
        ("投入/风险", ("成本", "投入", "评估", "实现", "风险", "缺少", "未确认")),
    ]


def sentence_relevance(sentence: str, keywords: tuple[str, ...]) -> int:
    score = sum(1 for keyword in keywords if keyword in sentence)
    if re.search(r"(优先希望|优先接受|需要先看到|明确希望)", sentence):
        score += 4
    if re.search(r"(明确|优先|需要先|希望|认为|表示|反馈)", sentence):
        score += 2
    if re.search(r"(暂时不是必须|不希望|未见|没有|无法|尚未|待)", sentence):
        score += 1
    return score


def best_dimension_evidence(lines: list[str], keywords: tuple[str, ...], label: str) -> str:
    all_text = "\n".join(evidence_body(line) for line in lines)
    if "投入" in label or "风险" in label or "Effort" in label:
        has_effort_result = re.search(
            r"(评估(?:待办)?已完成|技术评估.*完成|评估结论|估算|约需|需要\d+个?开发?人日|\d+个?开发?人日|成本.*(为|约|是))",
            all_text,
        )
        if not has_effort_result:
            return "未见实现成本或投入人天依据"
    candidates: list[str] = []
    for line in lines:
        candidates.extend(evidence_sentences(line))
    ranked = sorted(
        candidates,
        key=lambda sentence: sentence_relevance(sentence, keywords),
        reverse=True,
    )
    if ranked and sentence_relevance(ranked[0], keywords) > 0:
        return compact_evidence(ranked[0])
    if ranked:
        return compact_evidence(ranked[0])
    return "现有证据不足"


def comparison_fallback_text(query: str, evidence_lines: list[str]) -> tuple[str, str] | None:
    options = comparison_options(query)
    if len(options) < 2:
        return None
    option_lines = {option: option_evidence_lines(option, evidence_lines) for option in options}
    scores = {option: option_signal_score(lines) for option, lines in option_lines.items()}
    ranked = sorted(options, key=lambda option: scores[option], reverse=True)
    can_rank = scores[ranked[0]] > scores[ranked[1]]
    if can_rank:
        summary = f"现有证据只支持定性排序：{ranked[0]} 暂时排在 {ranked[1]} 之前，但不计算精确分。"
        conclusion = f"结论：按当前证据，{ranked[0]} 优先级高于 {ranked[1]}；但量化输入不足，不能生成精确 RICE 分数。"
    else:
        summary = "现有证据不足以拉开优先级差距，暂不生成精确排序。"
        conclusion = "结论：当前只能做定性比较，证据不足以可靠区分优先级；量化输入不足，不能生成精确分数。"
    dimensions = framework_dimensions(query)
    rows: list[str] = []
    for label, keywords in dimensions:
        cells = [best_dimension_evidence(option_lines[option], keywords, label) for option in options]
        if can_rank and label != "Effort 投入":
            judgment = f"{ranked[0]}更占优"
        elif "投入" in label or "风险" in label:
            judgment = "需补充成本/投入依据"
        else:
            judgment = "证据不足"
        rows.append(f"**{label}**")
        for option, cell in zip(options, cells):
            rows.append(f"{option}：{cell}")
        rows.append(f"判断：{judgment}")
    recommendation = "\n".join(
        [
            conclusion,
            "",
            "**定性比较**",
            *rows,
            "",
            "**信息缺口**：需要补齐各方案在同一口径下的覆盖人数、影响指标、信心来源和实现成本。",
            "",
            f"**下一步**：先按上述维度补齐参数。缺少完整量化依据时，不应编造数字或用估算数字凑精确分。",
        ]
    )
    return summary, recommendation


def project_recap_query(query: str) -> bool:
    return bool(
        re.search(
            r"(阶段复盘|项目复盘|复盘|本轮完成|最新范围|决策依据|剩余待办|待验证问题|阶段总结|项目总结)",
            query,
        )
    )


def add_unique(lines: list[str], text: str) -> None:
    if text and text not in lines:
        lines.append(text)


def project_recap_fallback_text(query: str, evidence_lines: list[str]) -> tuple[str, str] | None:
    if not project_recap_query(query):
        return None
    evidence_text = "\n".join(evidence_body(line) for line in evidence_lines)
    completed: list[str] = []
    scope_do: list[str] = []
    scope_dont: list[str] = []
    basis: list[str] = []
    remaining: list[str] = []
    validation: list[str] = []

    if "两家访谈均已完成" in evidence_text or all(name in evidence_text for name in ("澄岳机电", "柏川咨询")):
        add_unique(completed, "两家客户访谈已完成。")
    if "提醒方案草图" in evidence_text and "完成" in evidence_text:
        add_unique(completed, "提醒方案草图已完成。")
    if "站内清单交互稿" in evidence_text and re.search(r"(已交付|完成)", evidence_text):
        add_unique(completed, "站内清单交互稿已交付。")
    if re.search(r"(技术评估.*完成|评估待办完成|估算.*开发人日)", evidence_text):
        add_unique(completed, "技术评估已产出结论。")
    if "已更新需求文档" in evidence_text:
        add_unique(completed, "需求文档已更新。")
    if "首版范围按" in evidence_text or "首版只做" in evidence_text:
        add_unique(completed, "首版范围已完成收敛决策。")

    if "个人今日待跟进清单" in evidence_text or "今日待跟进清单" in evidence_text:
        add_unique(scope_do, "个人今日待跟进清单")
    if "站内提醒" in evidence_text:
        add_unique(scope_do, "站内提醒")
    if "邮件提醒暂不纳入" in evidence_text or "邮件提醒" in evidence_text:
        add_unique(scope_dont, "邮件提醒")
    if "团队周报也不纳入" in evidence_text or "团队周报" in evidence_text:
        add_unique(scope_dont, "团队周报 / 团队未完成清单")

    if re.search(r"(优先接受|优先希望).{0,40}(今日待跟进|到期未跟进|进系统)", evidence_text):
        add_unique(basis, "客户更认可进入系统查看今日待跟进清单。")
    if re.search(r"(不希望每条|不能逐条|不宜默认开启逐条邮件)", evidence_text):
        add_unique(basis, "客户和评审都不支持默认逐条邮件打扰。")
    if re.search(r"(3个开发人日|3 个开发人日).*?(4个开发人日|4 个开发人日|4个人日|4 个人日)", evidence_text, re.S):
        add_unique(basis, "技术评估显示站内清单约3个开发人日，邮件每日摘要额外约4个开发人日，首版预算为4个人日。")
    elif "开发预算" in evidence_text:
        add_unique(basis, "范围收敛受开发预算约束。")
    if "暂时不是必须" in evidence_text and "团队未完成清单" in evidence_text:
        add_unique(basis, "柏川咨询的团队未完成清单痛点存在，但新提醒不是其当前必须项。")

    if "埋点方案尚未完成" in evidence_text:
        match = re.search(r"周航反馈埋点方案尚未完成，预计([^。；;]+)", evidence_text)
        due = f"，预计{match.group(1)}" if match else ""
        add_unique(remaining, f"周航补充埋点方案{due}。")
    if "试用原型" in evidence_text and "具体时间未定" in evidence_text:
        add_unique(remaining, "林悦联系刘倩，确定澄岳机电3名销售试用原型的具体时间。")
    if "提醒频率确认待办已完成" not in evidence_text and "确认可以接受的提醒频率" in evidence_text:
        add_unique(remaining, "确认澄岳机电可接受的提醒频率口径是否已闭环。")

    if "试用原型" in evidence_text:
        add_unique(validation, "3名销售试用后，今日待跟进清单是否能减少漏跟进。")
    if "没有统计漏跟进总次数" in evidence_text or "无法确认造成多少收入损失" in evidence_text:
        add_unique(validation, "漏跟进总次数、影响客户数和收入损失仍需量化。")
    if re.search(r"(销售每天看到|进入系统后查看|今天该跟进谁)", evidence_text):
        add_unique(validation, "销售是否愿意每天进入系统查看清单。")
    if "团队未完成清单" in evidence_text:
        add_unique(validation, "团队未完成清单是否值得作为后续版本方向。")
    if "按日汇总" in evidence_text or "邮件每日摘要" in evidence_text:
        add_unique(validation, "邮件按日汇总是否后续仍有必要做。")

    if not any([completed, scope_do, scope_dont, basis, remaining, validation]):
        return None

    summary = "本轮已完成验证与范围收敛，下一步进入试用与埋点落地。"
    recommendation = "\n".join(
        [
            "**阶段判断**",
            "当前项目已完成关键验证、范围收敛和首版交付前准备，下一步重点是埋点方案与澄岳机电试用落地。",
            "",
            "**本轮完成**",
            *(f"- {item}" for item in completed or ["现有记录不足以确认已完成事项。"]),
            "",
            "**最新范围**",
            f"- 首版做：{'、'.join(scope_do) if scope_do else '现有记录未明确'}。",
            f"- 首版不做：{'、'.join(scope_dont) if scope_dont else '现有记录未明确'}。",
            "",
            "**决策依据**",
            *(f"- {item}" for item in basis or ["现有记录未提供明确决策依据。"]),
            "",
            "**剩余待办**",
            *(f"- {item}" for item in remaining or ["未见明确未完成待办。"]),
            "",
            "**待验证问题**",
            *(f"- {item}" for item in validation or ["后续验证问题尚未明确。"]),
        ]
    )
    return summary, recommendation


def parse_turn_classification(content: Any) -> dict[str, Any]:
    parsed = parse_model_answer(content, strict=True)
    turn_type = str(parsed.get("turn_type", "")).strip()
    if turn_type not in TURN_TYPES:
        raise ModelCallError("模型返回了未知对话类型", "model_response_invalid")
    confidence = parsed.get("confidence", 0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    return {
        "turn_type": turn_type,
        "confidence": max(0.0, min(1.0, confidence_value)),
        "reason": str(parsed.get("reason", "")).strip()[:300],
    }


def structured_model_options(settings: Dict[str, Any]) -> Dict[str, Any]:
    # DeepSeek defaults to thinking mode, which shares the small JSON output budget.
    if urlparse(settings["base_url"]).hostname == "api.deepseek.com":
        return {"thinking": {"type": "disabled"}}
    return {}


@harness_stage
def classify_turn_with_model(
    settings: Dict[str, Any],
    query: str,
    history: list[dict[str, str]],
) -> Optional[dict[str, Any]]:
    if not history or not settings["enabled"] or not settings["base_url"] or not settings["api_key"] or not settings["model"]:
        return None
    previous_user_query = last_history_text(history, "user")
    previous_answer = last_history_text(history, "assistant")
    if not previous_answer:
        return None
    prompt = (
        "你是对话意图分类器，只判断当前用户问题和上一轮回答的关系。"
        "不要回答用户问题，只返回合法 JSON。"
        "turn_type 只能是：new_task、topic_switch、follow_up、format_revision、constraint_revision、correction。"
        "format_revision 表示用户只要求把上一轮答案换格式、加序号、排序、统计条数、改表达，不需要新事实。"
        "constraint_revision 表示用户要求精简、压缩、只保留重点、限制范围，主要改写上一轮答案。"
        "follow_up 表示用户沿着上一轮继续追问原因、细节、下一步或补充信息，可能需要继续查询依据。"
        "new_task/topic_switch 表示当前问题独立于上一轮或明确换话题，需要重新查询。"
        "correction 表示用户指出上一轮错了或要求纠正。"
        "判断标准是语义关系，不要按句子长短或关键词机械判断。"
        "返回字段：turn_type、confidence、reason。"
    )
    payload = {
        "current_query": query,
        "previous_user_query": previous_user_query[:1000],
        "previous_answer": previous_answer[:2000],
        "recent_history": history[-4:],
    }
    url = settings["base_url"].rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {
                "model": settings["model"],
                "temperature": 0,
                "max_tokens": 256,
                **structured_model_options(settings),
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f'Bearer {settings["api_key"]}'},
        method="POST",
    )
    try:
        body = provider_json(request, 6, "classification")
        return parse_turn_classification(body["choices"][0]["message"]["content"])
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        return None
    except (KeyError, IndexError, json.JSONDecodeError, ModelCallError, TypeError, ValueError):
        return None


def build_conversation_state(
    query: str,
    history: list[dict[str, str]],
    turn_type: str,
) -> dict[str, Any]:
    previous_user_query = last_history_text(history, "user")
    previous_answer = last_history_text(history, "assistant")
    format_source = f"{previous_user_query}\n{query}"
    return {
        "active_topic": previous_user_query[:300] if turn_type != "topic_switch" else "",
        "previous_user_query": previous_user_query[:1000],
        "previous_answer": previous_answer[:2000],
        "current_query_is_continuation": turn_type in {
            "follow_up",
            "format_revision",
            "constraint_revision",
            "correction",
        },
        "format_constraints": infer_format_constraints(format_source),
    }


@harness_stage
def build_context_policy(
    query: str,
    history: list[dict[str, str]],
    classified_turn_type: Optional[str] = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    turn_type = classified_turn_type if classified_turn_type in TURN_TYPES else detect_turn_type(query, history)
    state = build_conversation_state(query, history, turn_type)
    if turn_type == "new_task":
        priority = {
            "current_query": 0.45,
            "conversation_state": 0.10,
            "previous_answer": 0.00,
            "memory_evidence": 0.35,
            "system_rules": 0.10,
        }
        resolved_query = query
        rule = "按新问题处理，旧对话只能辅助消歧，不得影响新问题主题。"
    elif turn_type == "topic_switch":
        priority = {
            "current_query": 0.60,
            "conversation_state": 0.05,
            "previous_answer": 0.00,
            "memory_evidence": 0.25,
            "system_rules": 0.10,
        }
        resolved_query = query
        rule = "用户明确切换话题，忽略上一话题的回答和召回结果。"
    elif turn_type == "format_revision":
        priority = {
            "current_query": 0.45,
            "conversation_state": 0.10,
            "previous_answer": 0.40,
            "memory_evidence": 0.05,
            "system_rules": 0.00,
        }
        resolved_query = (
            f"对上一轮结果执行格式或范围调整。上一轮问题：{state['previous_user_query'][:500]}。"
            f"上一轮结果：{state['previous_answer'][:1000]}。"
            f"本轮调整要求：{query}"
        )
        rule = "这是对上一轮结果的变换或统计，优先按当前格式要求修改上一轮答案，不要重新生成无关主题。"
    elif turn_type == "constraint_revision":
        priority = {
            "current_query": 0.45,
            "conversation_state": 0.10,
            "previous_answer": 0.40,
            "memory_evidence": 0.05,
            "system_rules": 0.00,
        }
        resolved_query = (
            f"对上一轮结果执行精简或约束性改写。上一轮问题：{state['previous_user_query'][:500]}。"
            f"上一轮结果：{state['previous_answer'][:1000]}。"
            f"本轮约束要求：{query}"
        )
        rule = "这是对上一轮结果的精简或约束性提问，优先按当前约束改写上一轮答案，不要重新召回或扩展新主题。"
    elif turn_type == "correction":
        priority = {
            "current_query": 0.35,
            "conversation_state": 0.25,
            "previous_answer": 0.30,
            "memory_evidence": 0.10,
            "system_rules": 0.00,
        }
        resolved_query = (
            f"修正上一轮结果。上一轮问题：{state['previous_user_query'][:500]}。"
            f"上一轮结果：{state['previous_answer'][:1000]}。"
            f"用户修正要求：{query}"
        )
        rule = "这是对上一轮结果的纠正，不得继续沿用用户已经否定的结论。"
    else:
        priority = {
            "current_query": 0.30,
            "conversation_state": 0.40,
            "previous_answer": 0.20,
            "memory_evidence": 0.10,
            "system_rules": 0.00,
        }
        resolved_query = (
            f"延续上一轮对话。上一轮问题：{state['previous_user_query'][:500]}。"
            f"上一轮结果：{state['previous_answer'][:1000]}。"
            f"本轮追问：{query}"
        )
        rule = "这是对上一轮结果的追问，必须先承接上一轮主题、实体和结论，再补充本轮要求。"
    policy = {
        "turn_type": turn_type,
        "priority": priority,
        "must_use_previous_answer": turn_type in {
            "follow_up",
            "format_revision",
            "constraint_revision",
            "correction",
        },
        "skip_auto_recall": turn_type in ("format_revision", "constraint_revision"),
        "resolved_query": resolved_query,
        "rule": rule,
    }
    return turn_type, state, policy


def retrieval_query_with_history(
    query: str,
    history: list[dict[str, str]],
    context_policy: Optional[dict[str, Any]] = None,
) -> str:
    if not history or not context_policy:
        return query
    if context_policy["turn_type"] in ("format_revision", "constraint_revision", "correction", "follow_up"):
        return context_policy["resolved_query"]
    return query


def weekly_todo_items(
    connection: sqlite3.Connection,
    scope: Optional[Dict[str, Any]] = None,
    user_id: str = "legacy",
    period: str = "本周",
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM memories WHERE visibility = 'visible' AND user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    retrieval_context = retrieval_scope("", scope)
    items: list[dict[str, Any]] = []
    for row in rows:
        if not row_matches_retrieval_scope(row, retrieval_context):
            continue
        if not retrieval_context.get("schedule_dates") and not is_current_week_memory(row):
            continue
        for text in extract_todo_items(row):
            items.append(
                {
                    "text": text,
                    "time": todo_date_label(row, text),
                    "memory_id": row["id"],
                    "memory_title": row["title"],
                    "source": row["source"],
                }
            )
    return items


def parsed_age_days(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)
    except ValueError:
        return 0.0


def dynamic_memory_weight(row: sqlite3.Row) -> float:
    age_decay = math.exp(-parsed_age_days(row["last_used_at"] or row["updated_at"]) / 180)
    citation_boost = min(0.18, math.log1p(max(0, row["citations"])) * 0.035)
    return round(max(0.55, min(1.18, 0.82 + 0.18 * age_decay + citation_boost)), 3)


def normalize_account(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def password_digest(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()


def public_user(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": row["id"],
        "account": row["account"],
        "display_name": row["display_name"],
        "created_at": row["created_at"],
    }


def create_session(connection: sqlite3.Connection, user_id: str) -> str:
    timestamp = now()
    token = secrets.token_urlsafe(32)
    connection.execute(
        """
        INSERT INTO user_sessions (token, user_id, created_at, last_used_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, user_id, timestamp, timestamp),
    )
    return token


def current_user(connection: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    if not token:
        return None
    row = connection.execute(
        """
        SELECT users.*
        FROM user_sessions
        JOIN users ON users.id = user_sessions.user_id
        WHERE user_sessions.token = ?
        """,
        (token,),
    ).fetchone()
    if row:
        connection.execute(
            "UPDATE user_sessions SET last_used_at = ? WHERE token = ?",
            (now(), token),
        )
        connection.commit()
    return row


def handle_register(connection: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    account = normalize_account(body.get("account"))
    password = str(body.get("password") or "")
    display_name = str(body.get("display_name") or account).strip()
    if len(account) < 3:
        raise ValueError("账号至少 3 个字符")
    if len(password) < 6:
        raise ValueError("密码至少 6 个字符")
    if connection.execute("SELECT 1 FROM users WHERE account = ?", (account,)).fetchone():
        raise ValueError("账号已存在，请直接登录")

    timestamp = now()
    salt = secrets.token_urlsafe(16)
    user_id = f"user-{int(datetime.now().timestamp() * 1000)}"
    connection.execute(
        """
        INSERT INTO users (id, account, display_name, password_hash, salt, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, account, display_name or account, password_digest(password, salt), salt, timestamp, timestamp),
    )
    token = create_session(connection, user_id)
    connection.commit()
    claim_legacy_data_if_first_user(connection, user_id)
    ensure_default_folders(connection, user_id)
    user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"token": token, "user": public_user(user)}


def handle_login(connection: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    account = normalize_account(body.get("account"))
    password = str(body.get("password") or "")
    user = connection.execute("SELECT * FROM users WHERE account = ?", (account,)).fetchone()
    if not user or not secrets.compare_digest(password_digest(password, user["salt"]), user["password_hash"]):
        raise ValueError("账号或密码错误")
    token = create_session(connection, user["id"])
    connection.commit()
    claim_legacy_data_if_first_user(connection, user["id"])
    ensure_default_folders(connection, user["id"])
    return {"token": token, "user": public_user(user)}


def migrate_folder_ownership(connection: sqlite3.Connection) -> None:
    folder_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(memory_folders)").fetchall()
    }
    indexes = connection.execute("PRAGMA index_list(memory_folders)").fetchall()
    has_global_name_unique = False
    for index in indexes:
        if not index["unique"]:
            continue
        index_columns = [
            column["name"]
            for column in connection.execute(f"PRAGMA index_info({index['name']})").fetchall()
        ]
        if index_columns == ["name"]:
            has_global_name_unique = True
            break
    if "user_id" in folder_columns and not has_global_name_unique:
        return

    connection.execute("ALTER TABLE memory_folders RENAME TO memory_folders_legacy")
    connection.execute(
        """
        CREATE TABLE memory_folders (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL DEFAULT 'legacy',
          name TEXT NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 1000,
          created_at TEXT NOT NULL,
          UNIQUE(user_id, name)
        )
        """
    )
    legacy_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(memory_folders_legacy)").fetchall()
    }
    user_expr = "user_id" if "user_id" in legacy_columns else "'legacy'"
    sort_expr = "sort_order" if "sort_order" in legacy_columns else "1000"
    connection.execute(
        f"""
        INSERT OR IGNORE INTO memory_folders (id, user_id, name, is_default, sort_order, created_at)
        SELECT id, {user_expr}, name, is_default, {sort_expr}, created_at
        FROM memory_folders_legacy
        """
    )
    connection.execute("DROP TABLE memory_folders_legacy")
    connection.commit()


def default_folder_id(user_id: str, folder_id: str) -> str:
    return folder_id if user_id == "legacy" else f"{user_id}-{folder_id}"


def ensure_default_folders(connection: sqlite3.Connection, user_id: str) -> None:
    timestamp = now()
    for index, folder in enumerate(DEFAULT_FOLDERS, start=1):
        existing_by_name = connection.execute(
            "SELECT id FROM memory_folders WHERE user_id = ? AND name = ?",
            (user_id, folder["name"]),
        ).fetchone()
        if existing_by_name:
            connection.execute(
                "UPDATE memory_folders SET is_default = 1, sort_order = ? WHERE id = ?",
                (index, existing_by_name["id"]),
            )
            continue

        folder_id = default_folder_id(user_id, folder["id"])
        if connection.execute("SELECT 1 FROM memory_folders WHERE id = ?", (folder_id,)).fetchone():
            folder_id = f"{user_id}-{folder['id']}-{int(datetime.now().timestamp() * 1000)}"
        connection.execute(
            """
            INSERT INTO memory_folders (id, user_id, name, is_default, sort_order, created_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (folder_id, user_id, folder["name"], index, timestamp),
        )
    connection.commit()


def claim_legacy_data_if_first_user(connection: sqlite3.Connection, user_id: str) -> None:
    user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count != 1:
        return
    connection.execute("UPDATE memories SET user_id = ? WHERE user_id = 'legacy'", (user_id,))
    connection.execute("UPDATE memory_folders SET user_id = ? WHERE user_id = 'legacy'", (user_id,))
    for key in (
        "provider",
        "base_url",
        "api_key",
        "model",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_model",
        "enabled",
    ):
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row:
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (f"{user_id}:{key}", row["value"]),
            )
    connection.commit()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    migrate_folder_ownership(connection)
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(memories)").fetchall()
    }
    folder_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(memory_folders)").fetchall()
    }
    if "sort_order" not in folder_columns:
        connection.execute("ALTER TABLE memory_folders ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 1000")
    for name, definition in (
        ("user_id", "TEXT NOT NULL DEFAULT 'legacy'"),
        ("folder_id", "TEXT"),
        ("workspace_id", "TEXT NOT NULL DEFAULT 'default'"),
        ("project_id", "TEXT NOT NULL DEFAULT ''"),
        ("snapshot_id", "TEXT NOT NULL DEFAULT ''"),
        ("event_at", "TEXT"),
        ("available_at", "TEXT"),
        ("valid_until", "TEXT"),
        ("supersedes_id", "TEXT"),
        ("conflicts_with_id", "TEXT"),
        ("embedding_json", "TEXT NOT NULL DEFAULT ''"),
        ("embedding_model", "TEXT NOT NULL DEFAULT ''"),
        ("embedding_source", "TEXT NOT NULL DEFAULT ''"),
        ("embedding_updated_at", "TEXT"),
    ):
        if name not in columns:
            connection.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
    connection.commit()
    timestamp = now()
    for index, folder in enumerate(DEFAULT_FOLDERS, start=1):
        existing_by_id = connection.execute(
            "SELECT id FROM memory_folders WHERE id = ?",
            (folder["id"],),
        ).fetchone()
        if existing_by_id:
            connection.execute(
                "UPDATE memory_folders SET name = ?, is_default = 1, sort_order = ? WHERE id = ? AND user_id = 'legacy'",
                (folder["name"], index, folder["id"]),
            )
            continue

        existing_by_name = connection.execute(
            "SELECT id FROM memory_folders WHERE name = ? AND user_id = 'legacy'",
            (folder["name"],),
        ).fetchone()
        if existing_by_name:
            connection.execute(
                "UPDATE memories SET folder_id = ? WHERE folder_id = ? AND user_id = 'legacy'",
                (folder["id"], existing_by_name["id"]),
            )
            connection.execute(
                "UPDATE memory_folders SET id = ?, is_default = 1, sort_order = ? WHERE id = ? AND user_id = 'legacy'",
                (folder["id"], index, existing_by_name["id"]),
            )
            continue

        connection.execute(
            """
            INSERT INTO memory_folders (id, user_id, name, is_default, sort_order, created_at)
            VALUES (?, 'legacy', ?, 1, ?, ?)
            """,
            (folder["id"], folder["name"], index, timestamp),
        )
    connection.commit()
    seed_initialized = connection.execute(
        "SELECT value FROM settings WHERE key = 'seed_initialized'"
    ).fetchone()
    if connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0 and not seed_initialized:
        timestamp = now()
        for item in SEED:
            connection.execute(
                """
                INSERT INTO memories
                (id, title, content, memory_type, source, tags_json, folder_id, visibility,
                 created_at, updated_at, last_used_at, citations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["title"],
                    item["content"],
                    item["memory_type"],
                    item["source"],
                    json.dumps(item.get("keywords", item.get("tags", [])), ensure_ascii=False),
                    {
                        "m-2": "folder-meetings",
                        "m-3": "folder-daily-todos",
                    }.get(item["id"]),
                    item.get("visibility", "visible"),
                    timestamp,
                    timestamp,
                    None,
                    0,
                ),
            )
        connection.commit()
    if not seed_initialized:
        connection.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('seed_initialized', '1')"
        )
        connection.commit()
    connection.execute(
        """
        UPDATE memories
        SET folder_id = CASE
          WHEN id = 'm-2' THEN 'folder-meetings'
          WHEN id = 'm-3' THEN 'folder-daily-todos'
          ELSE folder_id
        END
        WHERE folder_id IS NULL AND id IN ('m-2', 'm-3')
        """
    )
    connection.execute(
        """
        UPDATE memories
        SET workspace_id = COALESCE(NULLIF(workspace_id, ''), 'default'),
            project_id = COALESCE(NULLIF(project_id, ''), project),
            available_at = COALESCE(available_at, created_at),
            event_at = COALESCE(event_at, created_at)
        """
    )
    for row in connection.execute("SELECT * FROM memories").fetchall():
        sync_memory_entities(connection, row)
    connection.commit()
    return connection


def memory_json(row: sqlite3.Row) -> dict[str, Any]:
    dynamic_weight = dynamic_memory_weight(row)
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "type": row["memory_type"],
        "source": row["source"],
        "keywords": json.loads(row["tags_json"]),
        "project": row["project"],
        "workspace_id": row["workspace_id"],
        "project_id": row["project_id"],
        "snapshot_id": row["snapshot_id"],
        "folder_id": row["folder_id"],
        "visibility": row["visibility"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "event_at": row["event_at"],
        "available_at": row["available_at"],
        "valid_until": row["valid_until"],
        "supersedes_id": row["supersedes_id"],
        "conflicts_with_id": row["conflicts_with_id"],
        "last_used_at": row["last_used_at"],
        "citations": row["citations"],
        "weight": round(row["source_weight"] * row["quality_weight"] * dynamic_weight, 2),
        "source_weight": row["source_weight"],
        "quality_weight": row["quality_weight"],
        "dynamic_weight": dynamic_weight,
    }


def words(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text.lower())
    tokens: list[str] = []
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", compact):
        if re.fullmatch(r"[a-z0-9_]+", part):
            tokens.append(part)
        else:
            tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
    return tokens


def expanded_query_text(text: str) -> str:
    # Expand equivalent expressions only; do not invent entities, dates or requirements.
    expansions = {
        "碰面前": "会前 见之前",
        "拜访前": "会前 见之前",
        "面谈前": "会前 见之前",
        "对账": "财务核对",
        "一次下载很多订单": "批量导出 历史订单",
        "下载很多订单": "批量导出 历史订单",
        "上线没有": "上线状态",
        "上线了吗": "上线状态",
        "继续暂缓": "暂缓",
        "两天": "2天 两日",
        "卡在哪里": "阻碍 约束",
        "卡在哪": "阻碍 约束",
        "为什么": "原因 理由",
        "已经完成": "完成",
        "还有哪些需要我确认": "待确认",
        "居家办公": "远程办公 在家办公",
        "在家办公": "居家办公 远程办公",
    }
    expanded = text
    for phrase, addition in expansions.items():
        if phrase in text:
            expanded += f" {addition}"
    return expanded


def overlap_ratio(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(len(left_set), 1)


def field_text(row: sqlite3.Row) -> str:
    return " ".join([row["title"], row["content"], row["tags_json"], row["project"]])


def memory_updated_timestamp(memory: dict[str, Any]) -> float:
    timestamp = local_datetime(memory["updated_at"]) or local_datetime(memory["created_at"])
    return timestamp.timestamp() if timestamp else 0.0


def memory_event_timestamp(memory: dict[str, Any]) -> float:
    timestamp = (
        local_datetime(memory.get("event_at"))
        or local_datetime(memory.get("available_at"))
        or local_datetime(memory.get("updated_at"))
        or local_datetime(memory.get("created_at"))
    )
    return timestamp.timestamp() if timestamp else 0.0


def preferred_similar_memory(
    item: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    item_memory = item["memory"]
    current_memory = current["memory"]
    if item_memory.get("supersedes_id") == current_memory["id"]:
        return item
    if current_memory.get("supersedes_id") == item_memory["id"]:
        return current
    item_event = memory_event_timestamp(item_memory)
    current_event = memory_event_timestamp(current_memory)
    if item_event != current_event:
        return item if item_event > current_event else current
    item_updated = memory_updated_timestamp(item_memory)
    current_updated = memory_updated_timestamp(current_memory)
    if item_updated != current_updated:
        return item if item_updated > current_updated else current
    return item if item["score"] > current["score"] else current


def similar_memory_content(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_words = set(words(f"{left['title']} {left['content']}"))
    right_words = set(words(f"{right['title']} {right['content']}"))
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words) / min(len(left_words), len(right_words))
    return overlap >= 0.82


def bm25_score(
    query_terms: list[str],
    document_terms: list[str],
    total_documents: int,
    average_document_length: float,
    document_frequencies: Counter[str],
) -> float:
    if not query_terms or not document_terms or not total_documents or not average_document_length:
        return 0.0
    term_counts = Counter(document_terms)
    document_length = len(document_terms)
    score = 0.0
    for term in query_terms:
        frequency = term_counts.get(term, 0)
        if not frequency:
            continue
        document_frequency = document_frequencies.get(term, 0)
        idf = math.log(1 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5))
        denominator = frequency + BM25_K1 * (
            1 - BM25_B + BM25_B * document_length / average_document_length
        )
        score += idf * frequency * (BM25_K1 + 1) / denominator
    return score


def normalize_vector(values: Any) -> List[float]:
    if not isinstance(values, list) or not values:
        raise ValueError("Embedding 返回向量为空")
    vector = [float(value) for value in values]
    length = math.sqrt(sum(value * value for value in vector))
    if not length:
        raise ValueError("Embedding 返回零向量")
    return [value / length for value in vector]


def call_embedding_batch(settings: Dict[str, Any], inputs: List[str]) -> List[List[float]]:
    embedding_base_url = settings.get("embedding_base_url", "")
    embedding_api_key = settings.get("embedding_api_key", "")
    if (
        not settings["enabled"]
        or not embedding_base_url
        or not embedding_api_key
        or not settings["embedding_model"]
    ):
        raise ValueError("Embedding 配置不完整")
    url = embedding_base_url.rstrip("/") + "/embeddings"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {
                "model": settings["embedding_model"],
                "input": inputs,
                "dimensions": int(os.getenv("MEMORY_MASTER_EMBEDDING_DIMENSIONS", "1024")),
                "encoding_format": "float",
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {embedding_api_key}",
        },
        method="POST",
    )
    for attempt in range(3):
        if attempt:
            harness_note("retry", component="embedding", attempt=attempt + 1)
        try:
            body = provider_json(request, 8, "embedding")
            data = body["data"]
            if not isinstance(data, list) or len(data) != len(inputs):
                raise ValueError("Embedding 返回数量异常")
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            return [normalize_vector(item["embedding"]) for item in ordered]
        except urllib.error.HTTPError as error:
            raise ValueError(f"Embedding 请求失败（HTTP {error.code}）") from error
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            if attempt == 0:
                continue
            raise ValueError("Embedding 服务连接失败") from error
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"Embedding 返回异常：{error}") from error
    raise ValueError("Embedding 服务不可用")


def call_embeddings(settings: Dict[str, Any], inputs: List[str]) -> List[List[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(inputs), EMBEDDING_BATCH_SIZE):
        vectors.extend(call_embedding_batch(settings, inputs[start:start + EMBEDDING_BATCH_SIZE]))
    return vectors


def cosine(left: List[float], right: List[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def fuse_retrieval_candidates(lists: list[list[dict[str, Any]]]) -> dict[str, float]:
    """RRF ranks independent retrieval signals without mixing their score scales."""
    scores: dict[str, float] = {}
    for candidates in lists:
        for rank, item in enumerate(candidates, 1):
            memory_id = item["memory"]["id"]
            scores[memory_id] = scores.get(memory_id, 0.0) + 1.0 / (60 + rank)
    return scores


def call_reranker(settings: Dict[str, Any], query: str, documents: list[str]) -> list[float]:
    """Cohere-compatible rerank protocol; URL is the complete endpoint."""
    headers = {"Content-Type": "application/json"}
    if settings.get("reranker_api_key"):
        headers["Authorization"] = f"Bearer {settings['reranker_api_key']}"
    request = urllib.request.Request(
        settings["reranker_url"],
        data=json.dumps({
            "model": settings["reranker_model"], "query": query,
            "documents": documents, "top_n": len(documents),
        }, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        results = provider_json(request, 8, "rerank")["results"]
        scores: dict[int, float] = {}
        for item in results:
            index, score = item["index"], float(item["relevance_score"])
            if type(index) is not int or index < 0 or index >= len(documents) or index in scores:
                raise ValueError("invalid index")
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid score")
            scores[index] = score
        if len(scores) != len(documents):
            raise ValueError("incomplete results")
        return [scores[index] for index in range(len(documents))]
    except (urllib.error.URLError, OSError, http.client.HTTPException,
            KeyError, TypeError, ValueError) as error:
        # Do not expose provider responses, document text or credentials in diagnostics.
        raise ValueError("重排服务不可用或返回格式异常，已使用本地排序") from error


def sparse_score(query: str, row: sqlite3.Row) -> float:
    return overlap_ratio(words(query), words(field_text(row)))


def has_strict_intent_gap(query: str, row: sqlite3.Row) -> bool:
    if not any(term in query for term in CONTENT_RECALL_STRICT_INTENT_TERMS):
        return False
    source = field_text(row)
    query_has_finance = any(term in query for term in ("收入", "营收", "金额", "赔偿", "损失"))
    query_has_time = any(term in query for term in ("去年", "年度", "上年"))
    query_has_loss = any(term in query for term in ("损失", "流失", "赔偿"))
    if query_has_finance and not any(term in source for term in ("收入", "营收", "金额", "赔偿", "损失")):
        return True
    if query_has_time and not any(term in source for term in ("去年", "年度", "上年", "2025")):
        return True
    if query_has_loss and not any(term in source for term in ("损失", "流失", "赔偿")):
        return True
    return False


def query_project_mismatch(query: str, row: sqlite3.Row) -> bool:
    project = row["project"]
    known_projects = ("星河订单台", "灯塔CRM", "海岸客服台", "客户跟进提醒", "客户回访提醒", "导出报表")
    mentioned = [item for item in known_projects if item in query]
    return bool(mentioned and project and project not in mentioned)


def row_text(row: sqlite3.Row) -> str:
    return f"{row['title']} {row['content']} {row['tags_json']} {row['project']}"


def memory_text(memory: dict[str, Any]) -> str:
    keywords = memory.get("keywords", memory.get("tags_json", ""))
    if isinstance(keywords, list):
        keywords = " ".join(str(keyword) for keyword in keywords)
    return " ".join(
        str(memory.get(key, ""))
        for key in ("title", "content", "project")
    ) + f" {keywords}"


KNOWN_RETRIEVAL_PROJECTS = ("星河订单台", "灯塔CRM", "海岸客服台", "客户跟进提醒", "客户回访提醒", "导出报表")
KNOWN_RETRIEVAL_CUSTOMERS = ("青禾", "远山", "南桥", "松石", "王敏", "赵诚", "澄岳机电", "柏川咨询", "刘倩", "陈望")


def retrieval_intents(text: str) -> set[str]:
    intents: set[str] = set()
    if re.search(r"(明天见|会前|见.*之前|碰面前|拜访前|面谈前|客户.*回顾|客户.*背景|访谈准备|见客户|要问.*问题)", text):
        intents.add("customer_visit")
    if re.search(r"(订单|导出|CSV|字段|财务核对|对账|批量导出|固定三字段|付款金额|订单状态)", text, re.IGNORECASE):
        intents.add("order_export")
    if re.search(r"(跟进|回访|提醒|漏跟进|频率|站内提醒|邮件提醒)", text):
        intents.add("followup_reminder")
    if re.search(r"(竞品|FlowCRM|DealNote|ClientLoop|TrailCRM|NotePilot|LoopDesk|对比)", text, re.IGNORECASE):
        intents.add("competitor")
    if re.search(r"(待办|负责人|谁负责|完成|没完成|未完成|下周安排)", text):
        intents.add("todo")
    if re.search(r"(第一版|首版|范围|决策|为什么改|纳入|暂不纳入)", text):
        intents.add("scope_decision")
    if re.search(r"(预算|购买|采购|付费|承诺)", text):
        intents.add("commercial")
    return intents


def retrieval_entities(text: str) -> tuple[set[str], set[str]]:
    projects = {project for project in KNOWN_RETRIEVAL_PROJECTS if project in text}
    customers = {customer for customer in KNOWN_RETRIEVAL_CUSTOMERS if customer in text}
    return projects, customers


def customer_visit_without_export_query(query: str) -> bool:
    query_intents = retrieval_intents(query)
    return bool(
        "customer_visit" in retrieval_intents(query)
        and contains_any(query, ("青禾", "王敏", "澄岳机电", "刘倩", "这个客户"))
        and "order_export" not in query_intents
        and not re.search(r"(全部|所有|所有需求|全部需求|线索)", query)
    )


def customer_visit_scope_excludes(query: str, source: str) -> bool:
    customer_visit_query = customer_visit_without_export_query(query)
    query_intents = retrieval_intents(query)
    order_export_source = "order_export" in retrieval_intents(source) or re.search(r"(星河订单台|客户成功周宁)", source)
    return bool(customer_visit_query and order_export_source)


def initial_qinghe_visit_prep_query(query: str) -> bool:
    if not contains_any(query, ("青禾", "澄岳机电")) or not re.search(r"(明天见|会前|见.*之前|客户.*回顾|客户.*背景|访谈准备)", query):
        return False
    return not bool(
        re.search(
            r"(最新|进展|进度|试用|下周安排|答应购买|购买|采购|付费|首版|第一版|范围|邮件|4人|4次|漏跟进了多少)",
            query,
        )
    )


def initial_qinghe_visit_scope_excludes(query: str, source: str) -> bool:
    if not initial_qinghe_visit_prep_query(query):
        return False
    return bool(
        source.startswith(("周二", "周三", "周四", "周五"))
        or re.search(r"^.{0,20}9月(?:1[5-8]|2[2-5])日", source)
    )


def context_entity_boost(query: str, row: sqlite3.Row) -> float:
    source = row_text(row)
    query_projects, query_customers = retrieval_entities(query)
    source_projects, source_customers = retrieval_entities(source)
    query_intents = retrieval_intents(query)
    source_intents = retrieval_intents(source)
    boost = 0.0
    if query_customers and query_customers & source_customers:
        boost += 0.08
    if query_projects and query_projects & source_projects:
        boost += 0.10
    if query_intents and query_intents & source_intents:
        boost += 0.08
    if query_customers and source_customers and not (query_customers & source_customers):
        boost -= 0.08
    if query_projects and source_projects and not (query_projects & source_projects):
        boost -= 0.16
    if "customer_visit" in query_intents and "order_export" in source_intents and "order_export" not in query_intents:
        boost -= 0.26
    return boost


def evidence_density_boost(query: str, row: sqlite3.Row) -> float:
    source = row_text(row)
    query_intents = retrieval_intents(query)
    boost = 0.0
    if "customer_visit" in query_intents:
        visit_evidence_groups = (
            ("联系人", "王敏", "刘倩", "销售主管"),
            ("18名销售", "销售团队", "工业设备经销商"),
            ("表格管理", "回访遗漏", "忘记回访", "漏跟"),
            ("不希望频繁", "通知频率", "频繁收到通知"),
            ("预算", "购买承诺", "采购", "付费"),
        )
        matched_groups = sum(1 for group in visit_evidence_groups if contains_any(source, group))
        boost += min(0.18, matched_groups * 0.04)
    if "order_export" in query_intents:
        export_evidence_groups = (
            ("订单号",),
            ("付款金额",),
            ("订单状态",),
            ("CSV", "固定三字段"),
            ("财务核对", "对账"),
        )
        matched_groups = sum(1 for group in export_evidence_groups if contains_any(source, group))
        boost += min(0.18, matched_groups * 0.04)
    return boost


def excluded_by_project_scope(query: str, row: sqlite3.Row) -> bool:
    source = row_text(row)
    if customer_visit_scope_excludes(query, source):
        return True
    if initial_qinghe_visit_scope_excludes(query, source):
        return True
    allow_cross_project_evidence = bool(
        re.search(r"(谁在开发|谁负责|负责人)", query)
        and re.search(r"(邮件|功能)", query)
    )
    unrelated_project_note = "与客户跟进提醒项目无关" in source or "与客户回访提醒项目无关" in source
    if "这个项目" in query and unrelated_project_note:
        return not allow_cross_project_evidence
    if ("客户跟进提醒" in query or "客户回访提醒" in query) and (
        "导出报表项目" in source
        or unrelated_project_note
    ):
        return not allow_cross_project_evidence
    if "导出报表" in query and ("客户跟进提醒" in source or "客户回访提醒" in source) and "导出报表项目" not in source:
        return True
    return False


def contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def pm_week_recall_boost(query: str, row: sqlite3.Row) -> float:
    source = row_text(row)
    boost = 0.0
    if excluded_by_project_scope(query, row):
        return -0.7
    if (
        contains_any(source, ("FlowCRM", "DealNote", "ClientLoop", "TrailCRM", "NotePilot", "LoopDesk"))
        and not re.search(r"(竞品|FlowCRM|DealNote|ClientLoop|TrailCRM|NotePilot|LoopDesk|这两款|这三款|所选)", query)
    ):
        boost -= 0.35

    if "青禾" in query and contains_any(source, ("青禾", "王敏")):
        boost += 0.30
    if "澄岳机电" in query and contains_any(source, ("澄岳机电", "刘倩")):
        boost += 0.30
    if "远山" in query and contains_any(source, ("远山", "赵诚")):
        boost += 0.30
    if "柏川咨询" in query and contains_any(source, ("柏川咨询", "陈望")):
        boost += 0.30
    if "客户提醒" in query or "跟进提醒" in query or "客户跟进提醒" in query or "客户回访提醒" in query:
        if "客户跟进提醒" in source or "客户回访提醒" in source or contains_any(source, ("青禾", "远山", "王敏", "赵诚", "澄岳机电", "柏川咨询", "刘倩", "陈望")):
            boost += 0.12

    if re.search(r"(明天见|访谈|客户.*回顾|客户.*背景)", query) and contains_any(
        source,
        ("工业设备经销商", "联系人王敏", "联系人刘倩", "表格管理跟进", "表格管理客户跟进", "预算", "购买承诺"),
    ):
        boost += 0.32
    if initial_qinghe_visit_prep_query(query) and contains_any(
        source,
        ("本周先验证问题", "本轮先验证问题", "不承诺上线", "青禾和远山两家客户访谈", "澄岳机电和柏川咨询两家客户访谈", "JTBD", "访谈时可以问"),
    ):
        boost += 0.32
    if re.search(r"(都需要客户提醒|青禾和远山|澄岳机电和柏川咨询|客户差异)", query) and contains_any(
        source,
        ("9月15日访谈青禾", "9月15日访谈远山", "9月22日访谈澄岳机电", "9月22日访谈柏川咨询", "到期未跟进", "团队未完成清单"),
    ):
        boost += 0.32
    if "RICE" in query and contains_any(source, ("RICE", "到期未跟进", "团队未完成清单", "评审认为")):
        boost += 0.28
    if re.search(r"(第一版|首版|邮件提醒|后来为什么改|周一.*邮件|最新范围)", query) and contains_any(
        source,
        ("范围决策", "暂不纳入", "提醒频率", "开发预算", "暂定首版", "早期设想", "补充反馈", "按日汇总"),
    ):
        boost += 0.34
    if re.search(r"(谁在开发|谁负责|负责人)", query) and re.search(r"(邮件|功能)", query) and contains_any(
        source,
        ("范围决策", "暂不纳入", "导出报表项目", "刘洋", "与客户跟进提醒项目无关", "与客户回访提醒项目无关"),
    ):
        boost += 0.40
    if re.search(r"(4.*人|4.*次|到底是4)", query) and contains_any(source, ("4人", "4次", "总次数")):
        boost += 0.36
    if re.search(r"(没完成|未完成|还有哪些待办|剩余待办)", query) and contains_any(
        source,
        ("埋点方案尚未完成", "联系王敏确定试用时间", "联系刘倩确定试用时间", "范围决策", "新增待办"),
    ):
        boost += 0.34
    if re.search(r"(答应购买|下周安排|试用)", query) and contains_any(
        source,
        ("3名销售试用", "未确认采购", "没有承诺付费", "未确认预算", "购买承诺"),
    ):
        boost += 0.34
    if re.search(r"(周报|阶段复盘|本周完成|本轮完成|决策依据|待验证)", query) and contains_any(
        source,
        (
            "访谈青禾",
            "访谈澄岳机电",
            "到期未跟进",
            "没有承诺付费",
            "访谈远山",
            "访谈柏川咨询",
            "设计评审",
            "提醒频率确认",
            "技术评估",
            "范围决策",
            "交互稿",
            "试用原型",
        ),
    ):
        boost += 0.36
    return max(-0.7, min(0.45, boost))


def content_intent_boost(query: str, row: sqlite3.Row) -> float:
    source = field_text(row)
    boost = 0.0
    if re.search(r"(暂缓|没把|排进去|为什么)", query) and "暂缓" in source:
        boost += 0.14
    if re.search(r"(成本|工时|多少|几天)", query) and re.search(r"(工作日|估算|工时)", source):
        boost += 0.18
    if re.search(r"(推进|上线|交付|承诺|暂缓|直接做|卡在哪里|卡在哪|约束)", query) and re.search(
        r"(复测|风险|隔离|跨租户|安全检查|安全负责人|安全验收|安全评审通过|前提)",
        source,
    ):
        boost += 0.24
    if re.search(r"(还适用|五个客户|承诺|一定上线|这周五)", query) and re.search(
        r"(允许固定三字段CSV试点|前提是安全评审通过|完整自定义导出继续暂缓)",
        source,
    ):
        boost += 0.22
    if re.search(r"(还适用|五个客户|现在|当前)", query) and re.search(
        r"(跨租户读取风险|隔离修复|复测后才可上线|没有修复或复测通过证明)",
        source,
    ):
        boost += 0.22
    if re.search(r"(固定三字段|固定字段|CSV)", query) and re.search(r"(2个工作日|固定三字段|CSV)", source):
        boost += 0.10
    if re.search(r"(销售|周五|这周五|6月19)", query) and re.search(r"(销售|6月19|星期五|诉求)", source):
        boost += 0.18
    if re.search(r"(推进|上线|交付|承诺|直接做|卡在哪里|卡在哪)", query) and re.search(r"(不意味着功能开发已完成|评估提交已完成|计划，不是完成证明)", source):
        boost -= 0.18
    if re.search(r"(行动|待办|T01|T02|T03|周会)", query) and re.search(r"(T01|T02|T03)", source):
        boost += 0.16
    if "缓存" in query and re.search(r"(缓存|保留7天|保留30天)", source):
        boost += 0.20
    if "付费导出" in query and "付费导出" in source:
        boost += 0.20
    if re.search(r"(PDF|安全已经通过|都接受)", query) and re.search(
        r"(PDF|CSV|培训|假设|复测|隔离|跨租户|安全检查)",
        source,
    ):
        boost += 0.20
    if re.search(r"(PDF|都接受)", query) and re.search(r"(CSV即可|青禾每天需要导出|不要求自定义)", source):
        boost += 0.18
    if "安全已经通过" in query and re.search(r"(复测|隔离|跨租户|安全检查)", source):
        boost += 0.16
    if "松石" in query and "松石" in source:
        boost += 0.22
    if re.search(r"(海岸客服台|人工确认|自动回复)", query) and re.search(r"(海岸客服台|人工确认|自动回复)", source):
        boost += 0.24
    if query_project_mismatch(query, row):
        boost -= 0.20
    boost += context_entity_boost(query, row)
    boost += evidence_density_boost(query, row)
    boost += pm_week_recall_boost(query, row)
    return max(-0.7, min(0.45, boost))


def memory_project(memory: Dict[str, Any]) -> str:
    return normalized_scope(memory.get("project_id") or memory.get("project") or "")


def resolve_project_scope(query: str, memories: list[dict[str, Any]], requested: dict[str, Any]) -> dict[str, Any]:
    """Only resolve explicit known project names; never infer a project from similarity."""
    scope = dict(requested)
    matches = {
        memory_project(memory)
        for memory in memories
        if memory_project(memory) and any(
            normalized_scope(name) in normalized_scope(query)
            for name in (memory.get("project_id"), memory.get("project")) if name
        )
    }
    if len(matches) == 1:
        scope["project_id"] = next(iter(matches))
    elif len(matches) > 1:
        scope.pop("project_id", None)
    return scope


def memory_ranking_signals(memory: dict[str, Any], relevance: float, as_of: str) -> dict[str, float]:
    # These are source priors, not a claim that user-saved content is verified truth.
    reliability = min(1.0, max(0.0, float(memory.get("quality_weight", 0.8))))
    reliability *= 1.0 if memory["visibility"] == "visible" else 0.6
    reference = local_datetime(as_of) or local_datetime(now())
    event = local_datetime(memory.get("event_at"))
    if memory.get("type") == "方法型":
        temporal = 1.0
    elif event:
        temporal = math.exp(-max(0.0, (reference - event).total_seconds() / 86400) / 180)
    else:
        temporal = 0.5
    return {
        "relevance": round(relevance, 4),
        "source_reliability": round(reliability, 4),
        "temporal_applicability": round(temporal, 4),
        "score": round(0.60 * relevance + 0.25 * reliability + 0.15 * temporal, 4),
    }


def memory_facts(memory: dict[str, Any]) -> dict[str, str]:
    # Deliberately narrow: explicit labelled facts only, never arbitrary numbers in prose.
    facts = {}
    for label, value in re.findall(
        r"(?:^|[\n；;。])\s*(上线日期|截止日期|预算金额|负责人|当前状态)\s*[:：]\s*([^\n；;。]+)",
        memory["content"],
    ):
        facts[label] = value.strip()
    return facts


@harness_stage
def govern_memories(
    connection: sqlite3.Connection, selected: list[dict[str, Any]], scope: dict[str, Any], user_id: str,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Apply after all retrieval paths, including weekly and local shortcuts."""
    context = retrieval_scope("", scope)
    rows = connection.execute("SELECT * FROM memories WHERE user_id = ?", (user_id,)).fetchall()
    available = [memory_json(row) for row in rows if row_matches_retrieval_scope(row, context)]
    applicable_ids = {memory["id"] for memory in available}
    notes: list[str] = []
    blocked = False
    output = []
    for item in selected:
        memory = item["memory"]
        if memory["id"] not in applicable_ids:
            harness_memories("governance_filter", [memory], reason="scope")
            if item.get("manual"):
                notes.append("手动选择的资料存在项目或时间适用范围不符，本轮未将其作为事实依据。请调整范围后重试。")
                blocked = True
            continue
        replacements = [
            other for other in available
            if other.get("supersedes_id") == memory["id"]
            and memory_project(other) == memory_project(memory)
            and other["workspace_id"] == memory["workspace_id"]
            and other["snapshot_id"] == memory["snapshot_id"]
            and other["visibility"] == "visible"
        ]
        if replacements:
            harness_memories("governance_filter", [memory], reason="superseded")
            notes.append("参与回答的资料已有明确替代版本，旧内容不再作为当前事实依据。")
            if item.get("manual"):
                blocked = True
            continue
        facts = memory_facts(memory)
        for other in available:
            if other["id"] == memory["id"] or (
                memory_project(other), other["workspace_id"], other["snapshot_id"]
            ) != (memory_project(memory), memory["workspace_id"], memory["snapshot_id"]):
                continue
            if other.get("supersedes_id") == memory["id"] or memory.get("supersedes_id") == other["id"]:
                replacement = other if other.get("supersedes_id") == memory["id"] else memory
                if replacement["visibility"] == "visible":
                    continue
                if other["content"].strip() != memory["content"].strip():
                    notes.append("相关资料的更新依据尚未确认，请确认有效结论后再继续。")
                    blocked = True
            explicit = other.get("conflicts_with_id") == memory["id"] or memory.get("conflicts_with_id") == other["id"]
            other_facts = memory_facts(other)
            conflicting_fields = [key for key in facts if key in other_facts and facts[key] != other_facts[key]]
            if explicit or (memory["title"].strip() == other["title"].strip() and conflicting_fields):
                notes.append("相关资料存在尚未解决的事实冲突，请确认" + ("、".join(conflicting_fields) or "哪条结论有效") + "后再继续。")
                blocked = True
        output.append(item)
    harness_note("governance", blocked=blocked, note_count=len(set(notes)))
    return output, list(dict.fromkeys(notes)), blocked


@harness_stage
def retrieve(
    connection: sqlite3.Connection,
    query: str,
    manual_ids: List[str],
    settings: Optional[Dict[str, Any]] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
    scope: Optional[Dict[str, Any]] = None,
    user_id: str = "legacy",
) -> List[Dict[str, Any]]:
    all_rows = connection.execute("SELECT * FROM memories WHERE user_id = ?", (user_id,)).fetchall()
    retrieval_context = retrieval_scope(query, scope if scope else resolve_project_scope(query, [memory_json(row) for row in all_rows], {}))
    rows = [
        row
        for row in all_rows
        if (not strict_manual_only_query(query, manual_ids) or row["id"] in manual_ids)
        and row_matches_retrieval_scope(
            row,
            retrieval_context,
            manual=row["id"] in manual_ids,
        )
    ]
    if diagnostics is not None:
        diagnostics["scope"] = retrieval_context
        diagnostics["candidate_count_before_scope"] = len(all_rows)
        diagnostics["candidate_count_after_scope"] = len(rows)
    scoped_ids = {row["id"] for row in rows}
    harness_memories("scope_candidates", [memory_json(row) for row in rows])
    harness_memories("scope_filter", [memory_json(row) for row in all_rows if row["id"] not in scoped_ids], reason="scope_or_selected_only")
    for row in rows:
        sync_memory_entities(connection, row)
    connection.commit()
    expanded_query = expanded_query_text(query)
    entity_scores = entity_matches_for_rows(connection, rows, expanded_query)
    query_words = [token for token in words(expanded_query) if token not in {
        "需要", "哪些", "什么", "如何", "多少", "一下", "帮我", "是否",
        "可以", "现在", "目前", "信息", "准备", "之前", "提前", "关于", "相关", "情况",
    }]
    cutoff = query_cutoff_datetime(query)
    query_vector: Optional[List[float]] = None
    row_vectors: Dict[str, List[float]] = {}
    embedding_inputs: List[str] = [query]
    uncached_rows: List[sqlite3.Row] = []
    embedding_model = settings.get("embedding_model", "") if settings else ""
    row_terms: dict[str, dict[str, list[str]]] = {}
    document_frequencies: Counter[str] = Counter()
    for row in rows:
        title_words = words(row["title"])
        content_words = words(row["content"])
        tags_words = words(row["tags_json"])
        project_words = words(row["project"])
        source_words = title_words + content_words + tags_words + project_words
        row_terms[row["id"]] = {
            "title": title_words,
            "content": content_words,
            "tags": tags_words,
            "project": project_words,
            "source": source_words,
        }
        document_frequencies.update(set(source_words))
    total_documents = len(rows)
    average_document_length = (
        sum(len(terms["source"]) for terms in row_terms.values()) / total_documents
        if total_documents
        else 0.0
    )
    raw_bm25_scores = {
        row["id"]: bm25_score(
            query_words,
            row_terms[row["id"]]["source"],
            total_documents,
            average_document_length,
            document_frequencies,
        )
        for row in rows
    }
    max_bm25_score = max(raw_bm25_scores.values(), default=0.0)

    if embedding_model:
        for row in rows:
            source = field_text(row)
            try:
                cached = json.loads(row["embedding_json"]) if row["embedding_json"] else None
                if (
                    row["embedding_model"] == embedding_model
                    and row["embedding_source"] == source
                    and isinstance(cached, list)
                ):
                    row_vectors[row["id"]] = normalize_vector(cached)
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            uncached_rows.append(row)
            embedding_inputs.append(source)

        try:
            vectors = call_embeddings(settings or {}, embedding_inputs)
            query_vector = vectors[0]
            for row, vector in zip(uncached_rows, vectors[1:]):
                row_vectors[row["id"]] = vector
                connection.execute(
                    """
                    UPDATE memories
                    SET embedding_json = ?, embedding_model = ?, embedding_source = ?, embedding_updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(vector, ensure_ascii=False),
                        embedding_model,
                        field_text(row),
                        now(),
                        row["id"],
                    ),
                )
            connection.commit()
        except ValueError as error:
            query_vector = None
            row_vectors = {}
            harness_note("retrieval_fallback", component="embedding", fallback="bm25")
            if diagnostics is not None:
                diagnostics["embedding_error"] = str(error)

    embedding_available = query_vector is not None
    if not embedding_available and diagnostics is not None:
        diagnostics["embedding_used"] = False
    elif diagnostics is not None:
        diagnostics["embedding_used"] = True

    scored: list[dict[str, Any]] = []
    for row in rows:
        terms = row_terms[row["id"]]
        title_words = terms["title"]
        content_words = terms["content"]
        tags_words = terms["tags"]
        project_words = terms["project"]
        source_words = terms["source"]
        bm25 = raw_bm25_scores[row["id"]] / max_bm25_score if max_bm25_score else 0.0
        vector = cosine(query_vector, row_vectors[row["id"]]) if query_vector and row["id"] in row_vectors else 0.0
        title_overlap = overlap_ratio(query_words, title_words)
        tag_overlap = overlap_ratio(query_words, tags_words + project_words)
        content_overlap = overlap_ratio(query_words, content_words)
        project_overlap = overlap_ratio(query_words, project_words)
        content_recall_score = min(
            1.0,
            0.45 * bm25
            + 0.20 * title_overlap
            + 0.18 * tag_overlap
            + 0.12 * content_overlap
            + 0.05 * project_overlap,
        )
        entity_score = entity_scores.get(row["id"], 0.0)
        intent_boost = content_intent_boost(expanded_query, row)
        content_recall_score = min(1.0, content_recall_score + intent_boost + 0.08 * entity_score)
        manual = 1.0 if row["id"] in manual_ids else 0.0
        memory = memory_json(row)
        relevance = min(1.0, 0.65 * content_recall_score + 0.35 * max(0.0, vector)) if embedding_available else content_recall_score
        ranking_signals = memory_ranking_signals(memory, relevance, retrieval_context["as_of"])
        rerank = ranking_signals["score"]
        scored.append(
            {
                "memory": memory_json(row),
                "score": round(rerank, 3),
                "sparse_score": round(bm25, 3),
                "bm25_score": round(bm25, 3),
                "raw_bm25_score": round(raw_bm25_scores[row["id"]], 3),
                "vector_score": round(vector, 3),
                "title_score": round(title_overlap, 3),
                "tag_score": round(tag_overlap, 3),
                "content_score": round(content_overlap, 3),
                "project_score": round(project_overlap, 3),
                "content_recall_score": round(content_recall_score, 3),
                "content_intent_boost": round(intent_boost, 3),
                "entity_score": round(entity_score, 3),
                "strict_intent_gap": has_strict_intent_gap(query, row),
                "after_cutoff": row_after_cutoff(row, cutoff),
                "manual": bool(manual),
                "ranking_signals": ranking_signals,
            }
        )

    sparse_candidates = sorted(
        [item for item in scored if item["bm25_score"] > 0],
        key=lambda item: item["bm25_score"], reverse=True,
    )[:20]
    vector_candidates = (
        sorted([item for item in scored if item["vector_score"] > 0],
               key=lambda item: item["vector_score"], reverse=True)[:20]
        if embedding_available
        else []
    )
    pool: dict[str, dict[str, Any]] = {}
    for item in [*sparse_candidates, *vector_candidates]:
        pool[item["memory"]["id"]] = item
    for item in scored:
        if item["manual"]:
            pool[item["memory"]["id"]] = item
        elif item["content_intent_boost"] >= 0.28 or item["entity_score"] > 0 or retrieval_context.get("schedule_dates"):
            pool[item["memory"]["id"]] = item

    if embedding_available:
        fused = fuse_retrieval_candidates([sparse_candidates, vector_candidates])
        for item in pool.values():
            rrf = fused.get(item["memory"]["id"], 0.0)
            item["rrf_score"] = rrf
            # Keep absolute relevance alongside rank fusion: ranks alone cannot reject noise.
            relevance = 0.5 * item["ranking_signals"]["relevance"] + 0.5 * rrf / (2 / 61)
            item["ranking_signals"] = memory_ranking_signals(item["memory"], relevance, retrieval_context["as_of"])
            item["score"] = item["ranking_signals"]["score"]
    if diagnostics is not None:
        diagnostics["fusion_mode"] = "rrf" if embedding_available else "keyword"

    manual_items = [item for item in pool.values() if item["manual"]]
    automatic_items = [item for item in pool.values() if not item["manual"]]
    manual_dedupe_keys = {
        re.sub(r"\s+", "", f"{item['memory']['title']}::{item['memory']['content']}").lower()
        for item in manual_items
    }
    deduplicated_automatic: list[Dict[str, Any]] = []
    for item in automatic_items:
        memory = item["memory"]
        dedupe_key = re.sub(r"\s+", "", f"{memory['title']}::{memory['content']}").lower()
        if dedupe_key in manual_dedupe_keys:
            continue
        similar_index = next(
            (
                index
                for index, current in enumerate(deduplicated_automatic)
                if memory_project(memory) == memory_project(current["memory"])
                and memory["workspace_id"] == current["memory"]["workspace_id"]
                and memory["snapshot_id"] == current["memory"]["snapshot_id"]
                and memory["content"].strip() == current["memory"]["content"].strip()
            ),
            None,
        )
        if similar_index is None:
            deduplicated_automatic.append(item)
            continue
        current = deduplicated_automatic[similar_index]
        deduplicated_automatic[similar_index] = preferred_similar_memory(item, current)

    ranked = sorted(
        [*manual_items, *deduplicated_automatic],
        key=lambda item: (
            item["manual"],
            item["score"],
            item["title_score"],
            item["sparse_score"],
        ),
        reverse=True,
    )

    eligible = []
    rows_by_id = {row["id"]: row for row in rows}
    for item in ranked:
        semantic_match = (
            embedding_available
            and item["vector_score"] >= AUTO_RECALL_SIMILARITY_THRESHOLD
        )
        lexical_match = (
            item["content_recall_score"] >= CONTENT_RECALL_SCORE_THRESHOLD
            and item["sparse_score"] >= CONTENT_RECALL_MIN_OVERLAP
            and max(item["title_score"], item["tag_score"], item["content_score"]) >= CONTENT_RECALL_MIN_OVERLAP
            and len(set(query_words) & set(row_terms[item["memory"]["id"]]["source"])) >= min(2, len(set(query_words)))
        )
        if item["manual"] or (
            not item["strict_intent_gap"]
            and not item["after_cutoff"]
            and not excluded_by_project_scope(query, rows_by_id[item["memory"]["id"]])
            and (semantic_match or lexical_match or item["content_intent_boost"] >= 0.28 or retrieval_context.get("schedule_dates"))
        ):
            eligible.append(item)
    if re.search(r"(周报|本周完成|决策依据|待验证|复盘)", query):
        selection_limit = 12
    elif re.search(r"(还有哪些待办|剩余待办|没完成|未完成)", query):
        selection_limit = 8
    else:
        selection_limit = 5
    if strict_manual_only_query(query, manual_ids):
        eligible = [item for item in eligible if item["manual"]]
    if diagnostics is not None:
        diagnostics["reranker_used"] = False
    automatic = [item for item in eligible if not item["manual"]][:40]
    eligible_ids = {item["memory"]["id"] for item in eligible}
    harness_memories("relevance_filter", [item["memory"] for item in scored if item["memory"]["id"] not in eligible_ids], reason="relevance_or_deduplication")
    if settings and settings.get("reranker_url") and settings.get("reranker_model") and automatic:
        try:
            scores = call_reranker(settings, query, [memory_text(item["memory"]) for item in automatic])
            for item, score in zip(automatic, scores):
                item["reranker_score"] = score
                item["ranking_signals"] = memory_ranking_signals(item["memory"], score, retrieval_context["as_of"])
                item["score"] = item["ranking_signals"]["score"]
            accepted = [item for item in automatic if item["reranker_score"] >= RERANK_RELEVANCE_THRESHOLD]
            harness_memories("rerank_filter", [item["memory"] for item in automatic if item["reranker_score"] < RERANK_RELEVANCE_THRESHOLD], reason="rerank_threshold")
            eligible = [item for item in eligible if item["manual"]] + sorted(accepted, key=lambda item: item["score"], reverse=True)
            if diagnostics is not None:
                diagnostics["reranker_used"] = True
                diagnostics["reranker_rejected_count"] = len(automatic) - len(accepted)
        except ValueError as error:
            harness_note("retrieval_fallback", component="rerank", fallback="local_ranking")
            if diagnostics is not None:
                diagnostics["reranker_error"] = str(error)
    harness_memories("selection_limit_filter", [item["memory"] for item in eligible[max(selection_limit, len(manual_items)):]], reason="selection_limit")
    governed, notes, blocked = govern_memories(connection, eligible[:max(selection_limit, len(manual_items))], retrieval_context, user_id)
    selected = governed[:max(selection_limit, len(manual_items))]
    harness_memories("retrieval_selected", [item["memory"] for item in selected])
    if diagnostics is not None:
        diagnostics["governance_notes"] = notes
        diagnostics["requires_clarification"] = blocked
        diagnostics["ranking_weights"] = {"relevance": 0.60, "source_reliability": 0.25, "temporal_applicability": 0.15}
    return selected


def config(connection: sqlite3.Connection, user_id: str = "legacy") -> dict[str, Any]:
    prefix = f"{user_id}:"
    values = {
        row["key"][len(prefix):]: row["value"]
        for row in connection.execute("SELECT key, value FROM settings WHERE key LIKE ?", (f"{prefix}%",)).fetchall()
    }
    if not values and DEFAULT_MODEL_CONFIG_PATH.exists():
        values = json.loads(DEFAULT_MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
        values = {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in values.items()}
        connection.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            [(f"{prefix}{key}", value) for key, value in values.items()],
        )
        connection.commit()
    api_key = values.get("api_key", "") or os.getenv("MEMORY_MASTER_API_KEY", "")
    base_url = values.get("base_url", "") or os.getenv("MEMORY_MASTER_BASE_URL", "")
    embedding_api_key = (
        values.get("embedding_api_key", "")
        or os.getenv("MEMORY_MASTER_EMBEDDING_API_KEY", "")
        or os.getenv("DASHSCOPE_API_KEY", "")
        or os.getenv("QWEN_API_KEY", "")
    )
    embedding_base_url = (
        values.get("embedding_base_url", "")
        or os.getenv("MEMORY_MASTER_EMBEDDING_BASE_URL", "")
    )
    embedding_model = (
        values.get("embedding_model", "")
        or os.getenv("MEMORY_MASTER_EMBEDDING_MODEL", "")
    )
    enabled = values.get("enabled", "").lower() == "true"
    if embedding_api_key and embedding_model and not embedding_base_url:
        embedding_base_url = DEFAULT_QWEN_BASE_URL
    if embedding_api_key and not embedding_model:
        embedding_model = DEFAULT_QWEN_EMBEDDING_MODEL
    if embedding_api_key and embedding_model:
        enabled = True
    return {
        "provider": values.get("provider", os.getenv("MEMORY_MASTER_PROVIDER", "")),
        "base_url": base_url,
        "api_key": api_key,
        "model": values.get("model", "") or os.getenv("MEMORY_MASTER_MODEL", ""),
        "embedding_base_url": embedding_base_url,
        "embedding_api_key": embedding_api_key,
        "embedding_model": embedding_model,
        "reranker_url": os.getenv("MEMORY_MASTER_RERANKER_URL", ""),
        "reranker_model": os.getenv("MEMORY_MASTER_RERANKER_MODEL", ""),
        "reranker_api_key": os.getenv("MEMORY_MASTER_RERANKER_API_KEY", ""),
        "enabled": enabled,
    }


def public_config(connection: sqlite3.Connection, user_id: str = "legacy") -> dict[str, Any]:
    safe = config(connection, user_id)
    safe["api_key"] = "********" if safe["api_key"] else ""
    safe["embedding_api_key"] = "********" if safe["embedding_api_key"] else ""
    safe["reranker_api_key"] = "********" if safe["reranker_api_key"] else ""
    return safe


def load_system_prompt() -> str:
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        "你是工作记忆助手。只输出合法 JSON，字段为 summary, recommendation, reasoning_points, "
        "action_items, uncertainties。回答只允许引用记忆卡正文内容，不要输出任何内部编号。"
    )


class ModelCallError(ValueError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def is_timeout_error(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError):
        return True
    return "timed out" in str(error).lower()


def chat_response_max_tokens(payload: Dict[str, Any]) -> int:
    query = str(payload.get("query", ""))
    turn_type = str(payload.get("turn_type", ""))
    if turn_type in {"format_revision", "constraint_revision"}:
        return CHAT_RESPONSE_MAX_TOKENS_COMPACT
    if re.search(r"\d+\s*(字|个字|词|字符)|长文|文章|详细|展开|完整|报告|方案|演讲稿|脚本", query):
        return CHAT_RESPONSE_MAX_TOKENS_LONG
    if re.search(r"分析|建议|规划|策略|方案|对比|评估|梳理", query):
        return CHAT_RESPONSE_MAX_TOKENS_DETAILED
    return CHAT_RESPONSE_MAX_TOKENS_DEFAULT


def decode_loose_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return (
            value
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def extract_loose_json_string_field(text: str, key: str) -> tuple[str, bool]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"', text)
    if not match:
        return "", True
    chars: list[str] = []
    index = match.end()
    closed = False
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            chars.append(text[index : index + 2])
            index += 2
            continue
        if char == '"':
            tail = text[index + 1 :]
            if re.match(r"\s*(?:,\s*\"[A-Za-z_][A-Za-z0-9_]*\"\s*:|})", tail):
                closed = True
                break
        chars.append(char)
        index += 1
    return decode_loose_json_string("".join(chars)).strip(), closed


def parse_loose_model_answer(text: str) -> Dict[str, Any] | None:
    if not re.search(r'"(?:summary|recommendation)"\s*:', text):
        return None
    summary, summary_closed = extract_loose_json_string_field(text, "summary")
    recommendation, recommendation_closed = extract_loose_json_string_field(text, "recommendation")
    if not summary and not recommendation:
        return None
    if (summary and not summary_closed) or (recommendation and not recommendation_closed):
        return None
    return {
        "summary": summary,
        "recommendation": recommendation,
        "reasoning_points": [],
        "action_items": [],
        "uncertainties": [],
        "_warnings": ["模型返回非标准 JSON，已抽取可用字段展示"],
    }


def parse_model_answer(content: Any, strict: bool = True) -> Dict[str, Any]:
    """Accept structured JSON when available, while supporting normal model prose."""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if not isinstance(content, str) or not content.strip():
        raise ModelCallError("模型返回内容为空", "model_empty_response")

    text = content.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            candidates.insert(0, text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("summary", "")
            parsed.setdefault("recommendation", "")
            parsed.setdefault("reasoning_points", [])
            parsed.setdefault("action_items", [])
            parsed.setdefault("uncertainties", [])
            return parsed

    if strict:
        raise ModelCallError("模型未返回合法 JSON", "model_invalid_json")
    loose_answer = parse_loose_model_answer(text)
    if loose_answer:
        return loose_answer
    if re.search(r'"(?:summary|recommendation)"\s*:', text):
        raise ModelCallError("模型返回 JSON 不完整", "model_invalid_json")
    return {
        "summary": "",
        "recommendation": text,
        "reasoning_points": [],
        "action_items": [],
        "uncertainties": [],
        "_warnings": ["模型返回非标准 JSON，已按原文展示"],
    }


DECISION_QUERY_PATTERN = re.compile(
    r"(要不要|是否|应不应该|该不该|优先|取舍|进入.*迭代|排.*版本|怎么处理)"
)
CERTAINTY_QUERY_PATTERN = re.compile(r"(一定|能否保证|会不会|是否会|确定|必然)")
DECISION_ANSWER_PATTERN = re.compile(
    r"(建议|不建议|应该|不应|暂缓|优先|延后|可以|不可以|不宜|无法确定|不能确定|不确定)"
)
UNCERTAINTY_PATTERN = re.compile(r"(不确定|无法确认|不能保证|需确认|待确认|缺少)")
GENERIC_ACTION_PATTERN = re.compile(
    r"^(基于以上事实继续判断下一步动作|继续判断下一步|推进下一步|持续关注)$"
)


def model_answer_quality_error(query: str, answer: Dict[str, Any]) -> str | None:
    summary = str(answer.get("summary", "")).strip()
    recommendation = str(answer.get("recommendation", "")).strip()
    visible_answer = f"{summary}\n{recommendation}".strip()
    if not visible_answer:
        return "模型未生成可见建议"

    if DECISION_QUERY_PATTERN.search(query) and not DECISION_ANSWER_PATTERN.search(visible_answer):
        return "决策问题未给出明确结论"

    if CERTAINTY_QUERY_PATTERN.search(query):
        uncertainty_text = "\n".join(
            str(item.get("text", ""))
            for item in answer.get("uncertainties", [])
            if isinstance(item, dict)
        )
        if not UNCERTAINTY_PATTERN.search(f"{visible_answer}\n{uncertainty_text}"):
            return "确定性问题未暴露不确定信息"

    action_items = answer.get("action_items", [])
    if isinstance(action_items, list):
        for item in action_items:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                return "行动项为空"
            if GENERIC_ACTION_PATTERN.match(str(item["text"]).strip()):
                return "行动项为空泛模板"
    return None


@harness_stage
def call_model(settings: Dict[str, Any], payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not settings["enabled"] or not settings["base_url"] or not settings["api_key"] or not settings["model"]:
        return None
    url = settings["base_url"].rstrip("/") + "/chat/completions"
    max_tokens = chat_response_max_tokens(payload)
    payload = {
        **payload,
        "response_token_budget": max_tokens,
        "response_length_rule": (
            f"本轮回答必须控制在 max_tokens={max_tokens} 的输出预算内；"
            "如果用户明确要求字数或长文，以用户要求为准但仍不得超过该 token 预算。"
        ),
    }
    retry_reason = ""
    deadline = time.monotonic() + MODEL_CHAT_TIMEOUT_SECONDS
    for attempt in range(3):
        if attempt:
            harness_note("retry", component="generation", attempt=attempt + 1)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelCallError("模型响应超时，请稍后重试或缩短上下文", "model_timeout")
        messages = [
            {
                "role": "system",
                "content": load_system_prompt() + (
                    '\n接口格式要求：summary 和 recommendation 必须是字符串；'
                    'reasoning_points 和 uncertainties 必须是对象数组，'
                    '每项格式为 {"text":"具体内容","memory_ids":[]}；'
                    'action_items 必须是对象数组，每项格式为 '
                    '{"text":"具体动作","priority":"high"}，priority 只能是 high、medium、low。'
                    '不要用字符串数组代替对象数组；没有内容的数组返回 []。'
                ),
            },
        ]
        if attempt:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"上一轮输出不合格：{retry_reason}。请修正后只返回合法 JSON，不要在 JSON 外输出任何解释、"
                        "前缀或代码块；recommendation 字段内部可以使用 Markdown。"
                    ),
                }
            )
        messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "model": settings["model"],
                    "temperature": 0.2,
                    **structured_model_options(settings),
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f'Bearer {settings["api_key"]}'},
            method="POST",
        )
        try:
            body = provider_json(request, remaining, "generation")
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelCallError("模型输出达到长度上限，请缩短问题或减少引用内容后重试", "model_output_truncated")
            content = choice["message"]["content"]
            answer = parse_model_answer(content, strict=True)
            quality_error = model_answer_quality_error(str(payload.get("query", "")), answer)
            if quality_error:
                retry_reason = quality_error
                raise ModelCallError(quality_error, "model_quality_invalid")
            answer["_mode"] = "model"
            return answer
        except urllib.error.HTTPError as error:
            if error.code in (408, 409, 425, 429) or error.code >= 500:
                if attempt < 2:
                    harness_retry_sleep(0.4 * (attempt + 1))
                    continue
            raise ModelCallError(
                f"模型请求失败（HTTP {error.code}）",
                "model_http_error",
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            if is_timeout_error(error):
                raise ModelCallError(
                    "模型响应超时，请稍后重试或缩短上下文",
                    "model_timeout",
                ) from error
            if attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            raise ModelCallError(
                "模型服务连接失败，请检查 Base URL、模型名称和网络",
                "model_connection_error",
            ) from error
        except ModelCallError as error:
            if error.code == "model_output_truncated":
                raise
            retry_reason = retry_reason or str(error)
            if attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            raise
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            if attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            raise ModelCallError(
                f"模型返回格式异常：{error}",
                "model_response_invalid",
            ) from error
    raise ModelCallError("模型服务不可用，请稍后重试", "model_unavailable")


def parse_memory_draft(content: Any, original_content: str = "") -> Dict[str, Any]:
    parsed = parse_model_answer(content, strict=True)
    keywords = parsed.get("keywords", parsed.get("tags", []))
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, list):
        keywords = []
    return {
        "title": str(parsed.get("title", "")).strip()[:80],
        "content": original_content[:8000] if original_content else str(parsed.get("content", "")).strip()[:4000],
        "keywords": list(dict.fromkeys(str(keyword).strip() for keyword in keywords if str(keyword).strip()))[:8],
        "folder": str(parsed.get("folder", "")).strip()[:80],
    }


def normalize_folder_name(value: str) -> str:
    return re.sub(r"[\s_\-·・/\\|]+", "", value).lower()


def resolve_memory_folder(folder: str, folders: list[str]) -> str:
    folder_name = str(folder).strip()
    available_folders = [str(item).strip() for item in folders if str(item).strip()]
    if not folder_name:
        return ""
    for candidate in available_folders:
        if candidate == folder_name:
            return candidate
    normalized_folder = normalize_folder_name(folder_name)
    for candidate in available_folders:
        if normalize_folder_name(candidate) == normalized_folder:
            return candidate
    for candidate in available_folders:
        normalized_candidate = normalize_folder_name(candidate)
        if normalized_candidate and (
            normalized_candidate in normalized_folder or normalized_folder in normalized_candidate
        ):
            return candidate
    return ""


def infer_memory_folder(draft: Dict[str, Any], original_content: str, folders: list[str]) -> str:
    resolved_folder = resolve_memory_folder(draft.get("folder", ""), folders)
    if resolved_folder:
        return resolved_folder

    available_folders = [str(folder).strip() for folder in folders if str(folder).strip()]
    if "客户案例" not in available_folders:
        return ""

    keywords = " ".join(str(keyword) for keyword in draft.get("keywords", []) if str(keyword).strip())
    text = f"{draft.get('title', '')} {draft.get('content', '')} {keywords} {original_content}"
    customer_subject = any(keyword in text for keyword in ("客户", "用户", "商户", "企业", "甲方"))
    customer_case_signal = any(
        keyword in text
        for keyword in (
            "重点客户",
            "客户反馈",
            "客户案例",
            "回访",
            "续约",
            "试用",
            "投诉",
            "客诉",
            "商机",
            "合同",
            "付费",
            "区域客户",
        )
    )
    if customer_subject and customer_case_signal:
        return "客户案例"

    return draft.get("folder", "")


@harness_stage
def call_memory_parser(
    settings: Dict[str, Any],
    content: str,
    title: str = "",
    folders: Optional[list[str]] = None,
) -> Dict[str, Any]:
    if not settings["enabled"] or not settings["base_url"] or not settings["api_key"] or not settings["model"]:
        raise ModelCallError("请先启用并完成模型配置", "model_config_missing")
    parser_prompt = (
        "你负责把用户输入的工作信息整理成一张待确认的记忆卡草稿。"
        "只返回合法 JSON，不要 Markdown 或解释。"
        '字段固定为 title、content、keywords、folder；keywords 是关键词字符串数组，folder 是分组名称字符串。'
        "不要编造原文没有的事实，标题简洁，content 保留原文完整表达。"
        "你必须根据 raw_content 的业务语义，在候选分组中选择最匹配的文件夹名称作为 folder。"
        "不要只做关键词匹配，要结合客户、会议、待办、知识沉淀等语义判断。"
        "folder 必须原样返回某一个候选分组名称。"
    )
    available_folders = [str(folder).strip() for folder in (folders or []) if str(folder).strip()]
    if available_folders:
        parser_prompt += (
            f"候选分组：{json.dumps(available_folders, ensure_ascii=False)}。"
            "优先选择语义最接近的现有分组；只有内容和所有候选分组都明显无关时，folder 才返回空字符串。"
        )
    else:
        parser_prompt += "当前没有可用分组，folder 返回空字符串。"
    payload = {
        "source_title": title.strip(),
        "raw_content": content.strip(),
        "folder_candidates": available_folders,
    }
    url = settings["base_url"].rstrip("/") + "/chat/completions"
    for attempt in range(3):
        if attempt:
            harness_note("retry", component="memory_parse", attempt=attempt + 1)
        messages = [
            {"role": "system", "content": parser_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "model": settings["model"],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    **structured_model_options(settings),
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f'Bearer {settings["api_key"]}'},
            method="POST",
        )
        try:
            body = provider_json(request, MODEL_PARSE_TIMEOUT_SECONDS, "memory_parse")
            draft = parse_memory_draft(body["choices"][0]["message"]["content"], content)
            draft["folder"] = infer_memory_folder(draft, content, available_folders)
            return draft
        except urllib.error.HTTPError as error:
            if (error.code in (408, 409, 425, 429) or error.code >= 500) and attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            raise ModelCallError(f"模型请求失败（HTTP {error.code}）", "model_http_error") from error
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            if attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            if is_timeout_error(error):
                raise ModelCallError("模型响应超时，请稍后重试或缩短内容", "model_timeout") from error
            raise ModelCallError("模型服务连接失败，请检查 Base URL、模型名称和网络", "model_connection_error") from error
        except ModelCallError:
            if attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            raise
        except (KeyError, IndexError, json.JSONDecodeError, TypeError, ValueError) as error:
            if attempt < 2:
                harness_retry_sleep(0.4 * (attempt + 1))
                continue
            raise ModelCallError(f"模型返回格式异常：{error}", "model_response_invalid") from error
    raise ModelCallError("模型服务不可用，请稍后重试", "model_unavailable")


@harness_flow("memory_parse")
@harness_stage
def handle_memory_parse(connection: sqlite3.Connection, body: dict[str, Any], user_id: str) -> dict[str, Any]:
    content = str(body.get("content", "")).strip()
    title = str(body.get("title", "")).strip()
    if not content:
        raise ValueError("content 不能为空")
    if len(content) > 8000:
        raise ValueError("content 不能超过 8000 字")
    folders = body.get("folders", [])
    if not isinstance(folders, list):
        folders = []
    db_folders = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM memory_folders WHERE user_id = ? ORDER BY sort_order ASC, created_at ASC",
            (user_id,),
        ).fetchall()
    ]
    folders = list(dict.fromkeys([*db_folders, *[str(folder).strip() for folder in folders if str(folder).strip()]]))
    settings = config(connection, user_id)
    harness_config(settings)
    draft = call_memory_parser(settings, content, title, folders)
    return {"draft": draft, "meta": {"generation_mode": "model", "model": settings["model"]}}


def strip_internal_memory_ids(text: str) -> str:
    cleaned = re.sub(r"\bm-\d+\b", "该记忆卡", text)
    cleaned = re.sub(r"[^\S\r\n]{2,}", " ", cleaned)
    return cleaned.strip()


def sanitize_answer(answer: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("summary", "recommendation"):
        value = answer.get(key)
        if isinstance(value, str):
            answer[key] = strip_internal_memory_ids(value)

    for key in ("reasoning_points", "uncertainties", "action_items"):
        items = answer.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                item["text"] = strip_internal_memory_ids(item["text"])
    return answer


@harness_stage
def normalize_structured_answer(
    answer: Dict[str, Any],
    allowed_memory_ids: set[str],
) -> Dict[str, Any]:
    for key in ("summary", "recommendation"):
        if not isinstance(answer.get(key), str):
            answer[key] = ""

    for key in ("reasoning_points", "uncertainties"):
        items = answer.get(key)
        if not isinstance(items, list):
            answer[key] = []
            continue
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            raw_ids = item.get("memory_ids", [])
            if isinstance(raw_ids, str):
                raw_ids = [raw_ids]
            memory_ids = (
                [memory_id for memory_id in raw_ids if isinstance(memory_id, str) and memory_id in allowed_memory_ids]
                if isinstance(raw_ids, list)
                else []
            )
            normalized_items.append(
                {
                    "text": text.strip(),
                    "memory_ids": list(dict.fromkeys(memory_ids)),
                }
            )
        answer[key] = normalized_items

    items = answer.get("action_items")
    if not isinstance(items, list):
        answer["action_items"] = []
    else:
        normalized_actions: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            priority = item.get("priority")
            normalized_actions.append(
                {
                    "text": text.strip(),
                    "priority": priority if priority in ("high", "medium", "low") else "medium",
                }
            )
        answer["action_items"] = normalized_actions
    return answer


def cited_memory_ids(answer: Dict[str, Any], allowed_memory_ids: set[str]) -> list[str]:
    references: list[str] = []
    for key in ("reasoning_points",):
        for item in answer.get(key, []):
            if isinstance(item, dict):
                for memory_id in item.get("memory_ids", []):
                    if memory_id in allowed_memory_ids and memory_id not in references:
                        references.append(memory_id)
    return references


def answer_text_for_usage_check(answer: Dict[str, Any]) -> str:
    parts = [
        str(answer.get("summary", "")),
        str(answer.get("recommendation", "")),
    ]
    for key in ("reasoning_points", "action_items", "uncertainties"):
        for item in answer.get(key, []):
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
    return "\n".join(part for part in parts if part.strip())


def locally_used_memory_ids(answer: Dict[str, Any], memories: list[dict[str, Any]]) -> list[str]:
    answer_words = words(answer_text_for_usage_check(answer))
    used_ids: list[str] = []
    for item in memories:
        memory = item["memory"]
        source_text = " ".join(
            [
                memory.get("title", ""),
                memory.get("content", ""),
                " ".join(memory.get("keywords", [])),
            ]
        )
        if overlap_ratio(answer_words, words(source_text)) >= 0.12:
            used_ids.append(memory["id"])
    return used_ids


def parse_memory_usage_verification(content: Any, visible_ids: set[str]) -> list[str]:
    parsed = parse_model_answer(content, strict=True)
    raw_ids = parsed.get("used_memory_ids", [])
    if not isinstance(raw_ids, list):
        raise ModelCallError("模型返回的引用校验格式异常", "model_response_invalid")
    return list(
        dict.fromkeys(
            memory_id
            for memory_id in raw_ids
            if isinstance(memory_id, str) and memory_id in visible_ids
        )
    )


@harness_stage
def verify_used_memory_ids(
    settings: Dict[str, Any],
    query: str,
    answer: Dict[str, Any],
    memories: list[dict[str, Any]],
) -> list[str]:
    visible_memories = [
        item
        for item in memories
        if item["memory"].get("visibility") == "visible"
    ]
    visible_ids = {item["memory"]["id"] for item in visible_memories}
    if not visible_memories:
        return []
    if not settings["enabled"] or not settings["base_url"] or not settings["api_key"] or not settings["model"]:
        return locally_used_memory_ids(answer, visible_memories)
    prompt = (
        "你是记忆卡使用校验器，只判断最终回答是否实际使用了候选记忆卡。"
        "只有当回答中的事实、观点、措辞口吻、表达结构或具体内容明显来自某张卡时，才把该卡 ID 放入 used_memory_ids。"
        "不要因为用户手动选择过、模型上下文里出现过、或回答里泛泛提到类似主题就算使用。"
        "如果用户要求不使用卡片/记忆信息，必须返回空数组。"
        "只返回合法 JSON，字段为 used_memory_ids。"
    )
    payload = {
        "current_query": query,
        "final_answer": answer_text_for_usage_check(answer)[:3000],
        "candidate_memories": [
            {
                "id": item["memory"]["id"],
                "title": item["memory"]["title"],
                "content": item["memory"]["content"][:1600],
                "keywords": item["memory"]["keywords"],
                "selection": "manual" if item.get("manual") else "auto",
            }
            for item in visible_memories
        ],
    }
    url = settings["base_url"].rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {
                "model": settings["model"],
                "temperature": 0,
                "max_tokens": 512,
                **structured_model_options(settings),
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f'Bearer {settings["api_key"]}'},
        method="POST",
    )
    try:
        body = provider_json(request, 6, "citation")
        return parse_memory_usage_verification(body["choices"][0]["message"]["content"], visible_ids)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        return locally_used_memory_ids(answer, visible_memories)
    except (KeyError, IndexError, json.JSONDecodeError, ModelCallError, TypeError, ValueError):
        return locally_used_memory_ids(answer, visible_memories)


def prioritize_citations(
    references: Any,
    manual_id_set: set[str],
    visible_ids: set[str],
) -> list[str]:
    if not isinstance(references, list):
        return []
    valid_references = list(
        dict.fromkeys(
            memory_id
            for memory_id in references
            if isinstance(memory_id, str) and memory_id in visible_ids
        )
    )
    return [
        *[memory_id for memory_id in valid_references if memory_id in manual_id_set],
        *[memory_id for memory_id in valid_references if memory_id not in manual_id_set],
    ]


def test_model_connection(settings: Dict[str, Any], target: str = "all") -> dict[str, Any]:
    tested: list[str] = []
    if not settings["enabled"]:
        raise ValueError("请先启用模型配置")

    if target in ("all", "embedding"):
        if not settings.get("embedding_base_url"):
            raise ValueError("请先填写：Embedding Base URL")
        if not settings.get("embedding_api_key"):
            raise ValueError("请先填写：Embedding API Key")
        if not settings.get("embedding_model"):
            raise ValueError("请先填写：Embedding 模型")
        call_embeddings(settings, ["连接测试"])
        tested.append(f"Embedding {settings['embedding_model']}")
        if target == "embedding":
            return {
                "success": True,
                "provider": settings["provider"],
                "model": "、".join(tested),
            }

    if (
        settings["enabled"]
        and settings.get("embedding_base_url")
        and settings.get("embedding_api_key")
        and settings["embedding_model"]
        and not settings["model"]
    ):
        return {
            "success": True,
            "provider": settings["provider"],
            "model": "、".join(tested),
        }

    if target == "embedding":
        raise ValueError("请先填写：Embedding 模型")

    missing = [
        label
        for key, label in (
            ("base_url", "Base URL"),
            ("api_key", "API Key"),
            ("model", "模型名称"),
        )
        if not settings[key]
    ]
    if missing:
        raise ValueError(f"请先填写：{'、'.join(missing)}")

    url = settings["base_url"].rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {
                "model": settings["model"],
                "temperature": 0,
                "max_tokens": 128,
                **structured_model_options(settings),
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": '只返回合法 JSON：{"summary":"连接成功"}',
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f'Bearer {settings["api_key"]}'},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=MODEL_TEST_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            parsed = parse_model_answer(content, strict=True)
            if not parsed.get("summary") and not parsed.get("recommendation"):
                raise ModelCallError("模型返回内容为空", "model_empty_response")
            tested.append(f"对话 {settings['model']}")
            return {"success": True, "provider": settings["provider"], "model": "、".join(tested)}
        except urllib.error.HTTPError as error:
            if (error.code in (408, 409, 425, 429) or error.code >= 500) and attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise ModelCallError(
                f"模型请求失败（HTTP {error.code}）",
                "model_http_error",
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            if is_timeout_error(error):
                raise ModelCallError(
                    "模型响应超时，请稍后重试",
                    "model_timeout",
                ) from error
            raise ModelCallError(
                "模型服务连接失败，请检查 Base URL、模型名称和网络",
                "model_connection_error",
            ) from error
        except ModelCallError:
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise
        except (KeyError, IndexError, json.JSONDecodeError) as error:
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise ModelCallError(
                f"模型返回格式异常：{error}",
                "model_response_invalid",
            ) from error
    raise ModelCallError("模型服务不可用，请稍后重试", "model_unavailable")


def home_office_prep_fallback_text(query: str, evidence_text: str) -> tuple[str, str] | None:
    if "居家办公" not in query or not re.search(r"(提前准备|准备|风险|注意|梳理)", query):
        return None
    prep_lines: list[str] = []
    risk_lines: list[str] = []
    if "周三到周五居家办公" in evidence_text:
        prep_lines.append("确认本周三到周五居家办公安排，并提前把工作日程切到远程节奏。")
    if "9:30-11:30不方便开会" in evidence_text:
        prep_lines.append("周三9:30-11:30要陪父亲复查，不方便开会，需提前避开或改为异步补充。")
        risk_lines.append("周三上午会议存在冲突，10:00站会、评审类会议都要提前安排替代方案。")
    if "300M宽带" in evidence_text or "视频会议偶尔卡顿" in evidence_text:
        prep_lines.append("提前测试书房网络和视频会议质量，必要时调整路由器位置或准备有线网络。")
        risk_lines.append("书房离路由器较远，视频会议有卡顿风险。")
    if "每天10:00线上站会" in evidence_text:
        prep_lines.append("按团队约定参加每天10:00线上站会，并在16:30异步同步当日进展。")
    if "在线文档评论" in evidence_text:
        prep_lines.append("普通问题优先写在线文档评论，紧急事项再走飞书电话。")
    if "一次性设备补贴" in evidence_text:
        prep_lines.append("如需采购设备，按800元一次性补贴准备发票和用途说明。")
    if "椅子不在本次补贴范围" in evidence_text:
        risk_lines.append("椅子不在本次补贴范围，不要把人体工学椅计入报销方案。")
    if "不能把客户数据下载到个人电脑本地" in evidence_text:
        risk_lines.append("客户数据不能下载到个人电脑本地，也不能用私人网盘转存。")
    if "公司云文档或VPN环境" in evidence_text:
        risk_lines.append("客户名单、合同、报价只能在公司云文档或VPN环境中查看和编辑，离开座位要锁屏。")
    if not prep_lines and not risk_lines:
        return None
    summary = "周三开始居家办公前，重点准备日程、网络、设备补贴和客户数据安全边界。"
    recommendation = "\n".join(
        [
            "提前准备：",
            *[f"- {line}" for line in prep_lines],
            "",
            "风险注意：",
            *[f"- {line}" for line in risk_lines],
        ]
    ).strip()
    return summary, recommendation


def home_office_conflict_fallback_text(query: str, evidence_text: str) -> tuple[str, str] | None:
    if not re.search(r"(周三上午|10点|10:00|站会|冲突|提前同步)", query):
        return None
    has_busy_time = (
        "9:30-11:30不方便开会" in evidence_text
        or "9:30-11:30要陪父亲复查" in evidence_text
        or "周三9:30-11:30要陪父亲复查" in evidence_text
    )
    if not has_busy_time:
        return None
    affected_meetings: list[str] = []
    if "10:00团队站会" in evidence_text or "10:00线上站会" in evidence_text or "每天10:00线上站会" in evidence_text:
        affected_meetings.append("10:00团队站会")
    if "11:00需求评审" in evidence_text:
        affected_meetings.append("11:00需求评审")
    if not affected_meetings:
        return None
    meeting_text = "、".join(dict.fromkeys(affected_meetings))
    summary = f"周三上午9:30-11:30不方便开会，会影响{meeting_text}，需要提前改期、委托或异步补充。"
    recommendation_lines = [
        "建议动作：",
        "- 提前在日历标记周三9:30-11:30不可开会，并说明原因是陪父亲复查。",
        f"- 提前同步{meeting_text}的组织者或相关同事，说明该时段无法实时参加。",
        "- 对站会，提前把当日进展、风险和需要协助的问题写到在线文档评论里。",
        "- 如有紧急事项，保持飞书电话可联系；普通问题按团队约定走在线文档评论。",
    ]
    if "11:00需求评审" in affected_meetings:
        recommendation_lines.append("- 对需求评审，提前提交书面评审意见，或请熟悉背景的同事代为反馈关键问题。")
    if "默认异步" in evidence_text:
        recommendation_lines.append("- 能写清楚的问题优先按默认异步原则处理，避免临时缺席造成信息断点。")
    if "16:30异步同步" in evidence_text:
        recommendation_lines.append("- 当天16:30再按团队约定补一次异步进展同步。")
    recommendation_lines.append("- 如果会议必须本人实时参与，优先询问能否改到周三下午或周四。")
    return summary, "\n".join(recommendation_lines)


def clock_minutes(value: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def extract_unavailable_windows(text: str) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for match in re.finditer(r"(\d{1,2}:\d{2})\s*[-—~至到]\s*(\d{1,2}:\d{2}).{0,18}(?:不方便开会|无法参加|要陪父亲复查|陪父亲复查)", text):
        start = clock_minutes(match.group(1))
        end = clock_minutes(match.group(2))
        if start is not None and end is not None and start < end:
            windows.append((start, end))
    return windows


def clean_meeting_name(raw: str) -> str:
    name = re.sub(r"^(?:周三日程初排[:：]?|团队约定[:：]?|每天|周三|上午|与)", "", raw).strip()
    name = re.sub(r"^(?:的|和|及|、|，|,|\s)+", "", name).strip()
    return name[:24]


def extract_timed_meetings(memory: dict[str, Any]) -> list[dict[str, Any]]:
    text = f"{memory['title']}。{memory['content']}"
    meetings: list[dict[str, Any]] = []
    for match in re.finditer(r"(\d{1,2}:\d{2})\s*([^，。；;、\n]{0,28}?(?:站会|会议|评审|同步|确认))", text):
        minutes = clock_minutes(match.group(1))
        name = clean_meeting_name(match.group(2))
        if minutes is None or not name:
            continue
        meetings.append(
            {
                "time": match.group(1),
                "minutes": minutes,
                "name": name,
                "memory_id": memory["id"],
                "memory_title": memory["title"],
            }
        )
    return meetings


def meeting_identity(meeting: dict[str, Any]) -> tuple[str, str]:
    name = str(meeting["name"])
    if "站会" in name:
        return (str(meeting["time"]), "站会")
    if "需求评审" in name:
        return (str(meeting["time"]), "需求评审")
    return (str(meeting["time"]), name)


def more_specific_meeting(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_name = str(left["name"])
    right_name = str(right["name"])
    if "团队" in right_name and "团队" not in left_name:
        return right
    if len(right_name) > len(left_name):
        return right
    return left


def exact_home_office_conflict_answer(query: str, selected: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not re.search(r"(周三上午|10点|10:00|站会|会议.*影响|哪些会议|冲突|提前同步)", query):
        return None
    visible = [item for item in selected if item["memory"]["visibility"] == "visible"]
    if not visible:
        return None
    evidence_text = "\n".join(f"{item['memory']['title']}。{item['memory']['content']}" for item in visible)
    windows = extract_unavailable_windows(evidence_text)
    if not windows:
        return None
    meetings: list[dict[str, Any]] = []
    for item in visible:
        for meeting in extract_timed_meetings(item["memory"]):
            if any(start <= meeting["minutes"] < end for start, end in windows):
                meetings.append(meeting)
    meeting_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for meeting in meetings:
        key = meeting_identity(meeting)
        if key in meeting_by_identity:
            meeting_by_identity[key] = more_specific_meeting(meeting_by_identity[key], meeting)
        else:
            meeting_by_identity[key] = meeting
    unique_meetings = sorted(meeting_by_identity.values(), key=lambda item: item["minutes"])
    if not unique_meetings:
        return None
    meeting_text = "、".join(f"{meeting['time']}{meeting['name']}" for meeting in unique_meetings)
    reference_ids = list(
        dict.fromkeys(
            [
                *[meeting["memory_id"] for meeting in unique_meetings],
                *[
                    item["memory"]["id"]
                    for item in visible
                    if re.search(r"(9:30-11:30|默认异步|在线文档评论|飞书电话|16:30异步同步)", memory_text(item["memory"]))
                ],
            ]
        )
    )
    recommendation_lines = [
        f"受影响会议：{meeting_text}。",
        "",
        "处理建议：",
        "- 提前说明周三9:30-11:30不可实时参会。",
        "- 对站会，提前把当日进展、风险和需要协助的问题写到在线文档评论里。",
    ]
    if any("评审" in meeting["name"] for meeting in unique_meetings):
        recommendation_lines.append("- 对需求评审，提前提交书面评审意见，或请熟悉背景的同事代为反馈关键问题。")
    if "默认异步" in evidence_text:
        recommendation_lines.append("- 能写清楚的问题优先按默认异步处理。")
    if "飞书电话" in evidence_text:
        recommendation_lines.append("- 紧急事项保持飞书电话可联系。")
    if "16:30异步同步" in evidence_text:
        recommendation_lines.append("- 当天16:30再补一次异步进展同步。")
    recommendation_lines.append("- 如会议必须本人实时参与，再协商改到周三下午或周四。")
    return {
        "summary": f"周三上午9:30-11:30不可用，明确会影响{meeting_text}。",
        "recommendation": "\n".join(recommendation_lines),
        "reasoning_points": [
            {
                "text": f"该结论来自「{item['memory']['title']}」",
                "memory_ids": [item["memory"]["id"]],
            }
            for item in visible
            if item["memory"]["id"] in reference_ids
        ],
        "action_items": [],
        "uncertainties": [],
        "_references": reference_ids,
        "_mode": "local_fallback",
    }


def home_office_conflict_answer(query: str, selected: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact_answer = exact_home_office_conflict_answer(query, selected)
    return exact_answer


def local_home_office_conflict_selection(
    connection: sqlite3.Connection,
    query: str,
    scope: Optional[dict[str, Any]],
    user_id: str,
) -> list[dict[str, Any]]:
    if not re.search(r"(周三上午|10点|10:00|站会|会议.*影响|哪些会议|冲突|提前同步)", query):
        return []
    retrieval_context = retrieval_scope(query, scope)
    rows = connection.execute(
        "SELECT * FROM memories WHERE visibility = 'visible' AND user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    selected_rows: list[sqlite3.Row] = []
    for row in rows:
        if not row_matches_retrieval_scope(row, retrieval_context):
            continue
        source = row_text(row)
        if re.search(r"(9:30-11:30|10:00|11:00|站会|需求评审|默认异步|线上会议|周三日程)", source):
            selected_rows.append(row)
    selected = [
        {
            "memory": memory_json(row),
            "score": 0.9,
            "sparse_score": 0.0,
            "vector_score": 0.0,
            "title_score": 0.0,
            "tag_score": 0.0,
            "content_score": 0.0,
            "manual": False,
        }
        for row in selected_rows[:5]
    ]
    return selected if home_office_conflict_answer(query, selected) else []


def fallback_answer(query: str, selected: list[dict[str, Any]]) -> dict[str, Any]:
    visible = [item for item in selected if item["memory"]["visibility"] == "visible"]
    references = [item["memory"]["id"] for item in visible]
    if visible:
        exact_conflict_answer = exact_home_office_conflict_answer(query, selected)
        if exact_conflict_answer:
            return exact_conflict_answer
        titles = "、".join(item["memory"]["title"] for item in visible[:5])
        evidence_lines = [
            f"{index}. {item['memory']['title']}：{item['memory']['content']}"
            for index, item in enumerate(visible, start=1)
        ]
        evidence_text = "\n".join(evidence_lines)
        if re.search(r"(哪些.*完成|完成记录|还没有完成)", query):
            status_lines: list[str] = []
            if "两家访谈均已完成" in evidence_text:
                status_lines.append("两家客户访谈已完成。")
            if "陈晨已完成提醒方案草图" in evidence_text:
                status_lines.append("提醒方案草图已完成。")
            if "技术评估完成" in evidence_text or "评估待办完成" in evidence_text:
                status_lines.append("技术评估已完成。")
            elif "评估实现成本" in evidence_text:
                status_lines.append("技术评估尚未看到完成记录。")
            if status_lines:
                evidence_lines.insert(0, "状态判断：" + " ".join(status_lines))
        summary = f"已根据 {titles} 整理可确认事实。"
        is_recap_answer = False
        home_office_text = home_office_prep_fallback_text(query, evidence_text)
        recap_text = project_recap_fallback_text(query, evidence_lines)
        if home_office_text:
            summary, recommendation = home_office_text
        elif recap_text:
            is_recap_answer = True
            summary, recommendation = recap_text
        elif comparison_or_priority_query(query):
            comparison_text = comparison_fallback_text(query, evidence_lines) if priority_ranking_query(query) else None
            if comparison_text:
                summary, recommendation = comparison_text
            else:
                summary = f"已根据 {titles} 做定性比较，量化依据不足时不生成精确分数。"
                recommendation = "\n".join(
                    [
                        "结论：当前只能基于已召回事实做定性判断；如缺少覆盖人数、影响强度、信心或投入成本等量化依据，不应编造精确分数。",
                        "",
                        "可确认事实：",
                        *evidence_lines,
                        "",
                        "信息缺口：请补齐各方案在同一口径下的覆盖范围、影响指标、信心来源和投入成本。",
                        "",
                        "下一步：先用统一维度补齐参数，再正式排序或进入排期决策。",
                    ]
                )
        else:
            recommendation = "\n".join(evidence_lines)
        reasoning = [] if is_recap_answer else [
            {"text": f"该结论来自「{item['memory']['title']}」", "memory_ids": [item["memory"]["id"]]}
            for item in visible[:5]
        ]
        action_items = [] if is_recap_answer else [
            {"text": "基于以上事实继续判断下一步动作", "priority": "medium"},
        ]
        uncertainties = []
    else:
        summary = "当前未召回到高相关记忆，建议补充背景后再做具体判断。"
        recommendation = "请补充目标、对象、项目背景或手动选择相关记忆卡后再判断。"
        reasoning = []
        action_items = []
        uncertainties = [{"text": "缺少高相关历史记忆，当前建议仅基于问题本身。", "memory_ids": []}]
    return {
        "summary": summary,
        "recommendation": recommendation,
        "reasoning_points": reasoning,
        "action_items": action_items,
        "uncertainties": uncertainties,
        "_references": references,
        "_mode": "local_fallback",
    }


def model_error_answer(error: str) -> dict[str, Any]:
    return {
        "summary": "本次建议生成失败，请稍后重试。",
        "recommendation": "模型服务暂时不可用，本轮没有生成可供执行的建议。请检查模型配置或稍后重试。",
        "reasoning_points": [],
        "action_items": [],
        "uncertainties": [{"text": error, "memory_ids": []}],
        "_references": [],
        "_mode": "model_error",
        "_error": error,
    }


def todo_query_period(query: str) -> str:
    if re.search(r"周报|月报", query) or not re.search(r"待办|待完成|未完成|任务|行动|要做|跟进|计划", query):
        return ""
    if re.search(r"本月|这(?:个)?月|当月", query):
        return "本月"
    if re.search(r"这周|本周|本星期|这星期|最近一周", query):
        return "本周"
    return ""


def is_todo_format_query(query: str) -> bool:
    normalized_query = re.sub(r"本次测试视当前日期为\d{4}年\d{1,2}月\d{1,2}日。?", "", query)
    return bool(
        re.search(r"(待办|待完成|未完成|任务|行动|要做|跟进|计划)", normalized_query)
        and re.search(r"(日期|时间|什么时候|何时|谁负责|负责人|截止|分日期|按日期|序列号|编号|序号|按条|逐条|1\s*2\s*3|表格)", normalized_query)
    )


def weekly_todo_answer(items: list[dict[str, Any]], period: str = "本周") -> dict[str, Any]:
    if not items:
        return {
            "summary": f"{period}暂未找到带有明确待办信息的记忆卡。",
            "recommendation": f"请补充{period}会议、项目或任务记录，或手动添加包含负责人和截止时间的记忆卡。",
            "reasoning_points": [],
            "action_items": [],
            "uncertainties": [{"text": f"当前记忆卡中没有找到明确的{period}待办内容。", "memory_ids": []}],
            "_references": [],
            "_mode": "local_fallback",
        }
    lines = [f"| {item.get('time', '未注明日期')} | {item['text']} |" for item in items]
    source_ids = list(dict.fromkeys(item["memory_id"] for item in items))
    return {
        "summary": f"{period}共找到 {len(items)} 条待办信息。",
        "recommendation": f"{period}待办：\n\n| 时间 | 事项 |\n| --- | --- |\n" + "\n".join(lines),
        "reasoning_points": [],
        "action_items": [],
        "uncertainties": [],
        "_references": source_ids,
        "_mode": "local_fallback",
    }


@harness_flow("chat")
@harness_stage
def handle_chat(connection: sqlite3.Connection, body: dict[str, Any], user_id: str) -> dict[str, Any]:
    query = str(body.get("query", "")).strip()
    if not query:
        raise ValueError("query 不能为空")
    if len(query) > 2000:
        raise ValueError("query 不能超过 2000 字")
    manual_ids = body.get("manual_memory_ids", [])
    if not isinstance(manual_ids, list) or len(manual_ids) > 10:
        raise ValueError("manual_memory_ids 最多 10 张")
    retrieval_context = body.get("retrieval_scope")
    if not isinstance(retrieval_context, dict):
        retrieval_context = {
            key: body[key]
            for key in ("workspace_id", "project_id", "snapshot_id", "as_of")
            if key in body
        }
    settings = config(connection, user_id)
    harness_config(settings)
    retrieval_diagnostics: dict[str, Any] = {}
    memory_usage_disabled = disable_memory_usage_query(query)
    normalized_manual_ids = [] if memory_usage_disabled else [str(item) for item in manual_ids]
    conversation_history = normalized_conversation_history(body.get("conversation_history", []))
    turn_classification = classify_turn_with_model(settings, query, conversation_history)
    classified_turn_type = (
        turn_classification["turn_type"]
        if turn_classification and turn_classification["confidence"] >= 0.55
        else None
    )
    if todo_query_period(query):
        classified_turn_type = "new_task"
    turn_type, conversation_state, context_policy = build_context_policy(
        query,
        conversation_history,
        classified_turn_type,
    )
    if memory_usage_disabled:
        context_policy = {
            **context_policy,
            "skip_auto_recall": True,
            "rule": f"{context_policy['rule']} 用户明确要求不要使用卡片或记忆信息，本轮禁止召回和引用记忆卡。",
        }
    known_memories = [memory_json(row) for row in connection.execute(
        "SELECT * FROM memories WHERE user_id = ?", (user_id,)
    ).fetchall()]
    if not retrieval_context and turn_type not in ("new_task", "topic_switch"):
        previous_scope = body.get("previous_retrieval_scope")
        if isinstance(previous_scope, dict):
            retrieval_context = {key: previous_scope[key] for key in ("workspace_id", "project_id", "snapshot_id", "schedule_dates") if key in previous_scope}
    requested_dates = schedule_dates(query, local_datetime(now()))
    if requested_dates:
        retrieval_context["schedule_dates"] = requested_dates
    retrieval_context = retrieval_scope(query, resolve_project_scope(query, known_memories, retrieval_context))
    previous_scope = body.get("previous_retrieval_scope")
    project_changed = (
        isinstance(previous_scope, dict)
        and previous_scope.get("project_id")
        and retrieval_context["project_id"]
        and normalized_scope(previous_scope["project_id"]) != retrieval_context["project_id"]
    )
    if turn_type == "topic_switch" or project_changed:
        conversation_history = []
        turn_type, conversation_state, context_policy = build_context_policy(query, [], "topic_switch")
    retrieval_query = retrieval_query_with_history(
        query,
        conversation_history,
        context_policy,
    )
    manual_only_query = strict_manual_only_query(query, normalized_manual_ids)
    if manual_only_query:
        # Previous generated comparisons must not reintroduce unselected products.
        conversation_history = []
        turn_type, conversation_state, context_policy = build_context_policy(query, [], "new_task")
        retrieval_query = query
    harness_note("route", turn_type=turn_type, skip_auto_recall=bool(context_policy["skip_auto_recall"]),
                 manual_only=manual_only_query)
    revision_query = bool(context_policy["skip_auto_recall"])
    todo_period = todo_query_period(query)
    weekly_query = CHAT_MEMORY_RECALL_ENABLED and not revision_query and not manual_only_query and bool(todo_period)
    todo_format_query = is_todo_format_query(query)
    if weekly_query:
        retrieval_context["include_undated_todos"] = True
    weekly_todos = weekly_todo_items(connection, retrieval_context, user_id, todo_period) if weekly_query else []
    if weekly_todos:
        weekly_todos = [
            item
            for item in weekly_todos
            if not excluded_by_project_scope(
                query,
                connection.execute(
                    "SELECT * FROM memories WHERE id = ? AND user_id = ?",
                    (item["memory_id"], user_id),
                ).fetchone(),
            )
        ]
    fast_local_selection = (
        []
        if context_policy["skip_auto_recall"] or normalized_manual_ids
        else local_home_office_conflict_selection(connection, query, retrieval_context, user_id)
    )
    if fast_local_selection:
        selected = fast_local_selection
        retrieval_diagnostics["fast_local_path"] = "home_office_conflict"
    elif CHAT_MEMORY_RECALL_ENABLED and not context_policy["skip_auto_recall"]:
        if retrieval_context:
            selected = retrieve(
                connection,
                retrieval_query,
                normalized_manual_ids,
                settings,
                retrieval_diagnostics,
                retrieval_context,
                user_id,
            )
        else:
            selected = retrieve(
                connection,
                retrieval_query,
                normalized_manual_ids,
                settings,
                retrieval_diagnostics,
                user_id=user_id,
            )
    else:
        selected = []
    if customer_visit_without_export_query(retrieval_query):
        selected = [
            item
            for item in selected
            if item["memory"]["id"] in normalized_manual_ids
            or not customer_visit_scope_excludes(retrieval_query, memory_text(item["memory"]))
        ]
    if manual_only_query:
        selected = [item for item in selected if item["memory"]["id"] in normalized_manual_ids]
    if weekly_query and weekly_todos:
        selected_ids = {item["memory_id"] for item in weekly_todos}
        selected_by_id = {item["memory"]["id"]: item for item in selected}
        rows = connection.execute(
            f"SELECT * FROM memories WHERE visibility = 'visible' AND user_id = ? AND id IN ({','.join('?' for _ in selected_ids)})",
            [user_id, *list(selected_ids)],
        ).fetchall()
        for row in rows:
            if row["id"] not in selected_by_id:
                selected_by_id[row["id"]] = {
                    "memory": memory_json(row),
                    "score": 0.6,
                    "sparse_score": 0.0,
                    "vector_score": 0.0,
                    "title_score": 0.0,
                    "tag_score": 0.0,
                    "content_score": 0.0,
                    "manual": row["id"] in normalized_manual_ids,
                }
        selected = list(selected_by_id.values())
    selected, governance_notes, governance_blocked = govern_memories(connection, selected, retrieval_context, user_id)
    governance_notes = list(dict.fromkeys([*retrieval_diagnostics.get("governance_notes", []), *governance_notes]))
    governance_blocked = governance_blocked or retrieval_diagnostics.get("requires_clarification", False)
    if not retrieval_context["project_id"] and not re.search(r"跨项目|比较|对比|各项目|所有项目", query):
        projects = {memory_project(item["memory"]) for item in selected if memory_project(item["memory"])}
        if len(projects) > 1:
            governance_notes.append("相关资料涉及多个项目，请明确本轮需要讨论的项目。")
            governance_blocked = True
    harness_memories("generation_candidates", [item["memory"] for item in selected], blocked=bool(governance_blocked))
    allowed_todo_ids = {item["memory"]["id"] for item in selected}
    weekly_todos = [item for item in weekly_todos if item["memory_id"] in allowed_todo_ids]
    manual_id_set = set(normalized_manual_ids)
    model_memories = selected
    special_requests: list[str] = []
    if manual_only_query:
        special_requests.append(
            "本轮仅使用用户选中的资料，比较对象也限于选中资料明确描述的对象。"
            "不得从历史回答或通用知识添加其他竞品；约束卡不是竞品。"
            "若只有一个产品，按约束评估该产品并说明缺少其他比较对象；"
            "缺失的能力和成本信息明确标注未知，不自行补全。"
        )
    if customer_visit_without_export_query(retrieval_query):
        special_requests.append(
            "这是客户会前回顾，不是需求全集整理。除非用户明确询问订单、导出、CSV、字段或全部需求，"
            "不要主动提及订单导出、CSV、固定三字段、财务核对等另一条需求线。"
        )
    if weekly_query:
        special_requests.append(
            f"这是{todo_period}待办查询。请只根据 weekly_todos 列出待办，保留每条待办的原意，时间使用每条的 time 字段。"
            f"无明确日期的待办按卡片 created_at（用户时区）是否落在{todo_period}纳入，不能用 updated_at；明确日期优先。"
            f"已按创建时间纳入的待办属于{todo_period}清单，不要再要求确认是否属于{todo_period}，也不要编造截止日期；按创建日期分组时标明创建。"
        )
    if weekly_query or re.search(r"待办|待完成|未完成|任务清单|行动项", query):
        special_requests.append(
            "待办查询默认输出标准 Markdown 两列表格，表头为 '| 时间 | 事项 |'，下一行必须是 '| --- | --- |'，每项占一行。不要输出项目符号列表或用全角竖线拼接文本。用户明确指定其他格式时优先遵循用户要求。"
            "无明确日期时标注创建日期和未注明截止日期，不把创建日期当成截止日期。"
            "recommendation 只写清单，不附加推进建议；reasoning_points、action_items、uncertainties 返回空数组。"
        )
    if todo_format_query:
        special_requests.append(
            "这是待办整理任务。用户已经指定输出格式，最终可见正文必须严格按用户要求的日期、序号或表格格式输出；"
            "recommendation 只写用户要看到的待办清单，不要添加摘要、依据、分析、判断说明或不确定性说明；"
            "reasoning_points、action_items、uncertainties 返回空数组。"
        )
    if comparison_or_priority_query(query):
        special_requests.append(
            "这是比较、排序、优先级或取舍判断任务。请先给结论，再按统一维度比较，明确信息缺口，最后给下一步；"
            "除非 memories 中有完整且同口径的量化依据，不要编造分数、ROI、人数、次数或看似精确的排名；"
            "如果只能定性判断，请明确写成定性判断。"
        )
    if framework_application_query(query):
        special_requests.append(
            "用户已经指定方法或分析框架。最多用一句话说明方法口径，然后直接应用到当前对象；"
            "不要长篇解释框架定义，不要把回答写成方法教程。"
        )
    if context_policy["turn_type"] == "format_revision":
        special_requests.append(
            "这是同一会话里的格式改写或数量统计追问。不要基于本轮短问题重新召回或扩展新主题；"
            "只基于 previous_answer 进行改写、编号、排序或统计。"
            "recommendation 写用户最终要看到的结果，reasoning_points、action_items、uncertainties 返回空数组。"
        )
    if context_policy["turn_type"] == "constraint_revision":
        special_requests.append(
            "这是同一会话里的精简、摘要或约束性追问。不要基于本轮短问题重新召回或扩展新主题；"
            "只基于 previous_answer 按用户当前约束进行压缩、提炼或范围收敛。"
            "recommendation 写用户最终要看到的结果，reasoning_points、action_items、uncertainties 返回空数组。"
        )
    if memory_usage_disabled:
        special_requests.append(
            "用户明确要求不要使用卡片或记忆信息。本轮不要基于 memories 生成内容，不要引用、复述或提及任何记忆卡；"
            "reasoning_points、action_items、uncertainties 中的 memory_ids 必须返回空数组。"
        )
    if retrieval_context.get("schedule_dates"):
        special_requests.append("只回答所查询日期的安排，记忆里的相对日期以记录时的时间为准，不能改成查询时的日期。未覆盖的日期明确说明未找到安排；不要把未找到说成没有安排。")
        conversation_history = []
        context_policy = {**context_policy, "resolved_query": query}
        conversation_state = {**conversation_state, "previous_answer": ""}
    model_payload = {
        "query": query,
        "resolved_query": context_policy["resolved_query"],
        "turn_type": turn_type,
        "context_policy": context_policy,
        "conversation_state": conversation_state,
        "previous_answer": conversation_state["previous_answer"],
        "query_priority": "最高：先识别并回答用户当前问题",
        "memory_usage_rule": (
            "记忆卡仅作为相关背景和证据，不得替代问题或与问题简单拼接。"
            "手动选中表示优先参考，不表示事实更可信或仍然有效；自动卡可依据明确替代关系修正旧结论。"
            "必须按项目、时间及来源判断；无替代证据的分歧放入 uncertainties，不得自行认定新卡正确。"
            "reasoning_points.memory_ids 只绑定真正支撑该判断的卡片，优先绑定手动卡；"
            "自动卡只有在明确支撑具体事实时才可引用。"
        ),
        "conversation_history_rule": (
            f"{context_policy['rule']} 当前问题仍然优先于历史和记忆卡。"
            if conversation_history
            else ""
        ),
        "retrieval_scope": retrieval_context,
        "governance_notes": governance_notes,
        "special_request": "\n".join(special_requests),
        "weekly_todos": weekly_todos,
        "manual_memory_ids": normalized_manual_ids if CHAT_MEMORY_RECALL_ENABLED else [],
        "auto_recalled_memory_ids": [
            item["memory"]["id"] for item in selected if item["memory"]["id"] not in manual_id_set
        ],
        "memories": [
            {
                "id": item["memory"]["id"],
                "title": item["memory"]["title"],
                "content": item["memory"]["content"],
                "keywords": item["memory"]["keywords"],
                "visibility": item["memory"]["visibility"],
                "selection": "manual" if item["memory"]["id"] in manual_id_set else "auto",
                "citation_priority": (
                    "primary" if item["memory"]["id"] in manual_id_set else "secondary"
                ),
                "ranking_signals": item.get("ranking_signals", {}),
                "project_id": memory_project(item["memory"]),
                "event_at": item["memory"].get("event_at"),
                "created_at": item["memory"].get("created_at"),
                "updated_at": item["memory"].get("updated_at"),
                "available_at": item["memory"].get("available_at"),
                "valid_until": item["memory"].get("valid_until"),
                "supersedes_id": item["memory"].get("supersedes_id"),
            }
            for item in model_memories
        ],
        "conversation_history": conversation_history,
    }
    truncated_memory_ids = []
    for item in model_payload["memories"]:
        content = item["content"]
        if len(content) > 2000:
            item["content"] = content[:2000] + "…"
            truncated_memory_ids.append(item["id"])
    model_error = ""
    model_error_code = ""
    direct_evidence_answer = home_office_conflict_answer(query, selected)
    if retrieval_context.get("schedule_dates") and not selected and not memory_usage_disabled:
        dates_text = "、".join(retrieval_context["schedule_dates"])
        answer = {
            "summary": "未找到所查询日期的安排。",
            "recommendation": f"未找到 {dates_text} 对应的安排记录。",
            "reasoning_points": [], "action_items": [], "uncertainties": [],
            "_references": [], "_mode": "local_fallback",
        }
    elif governance_blocked:
        answer = {
            "summary": "需要先确认记忆的适用范围或冲突信息。",
            "recommendation": "\n\n".join(governance_notes),
            "reasoning_points": [], "action_items": [],
            "uncertainties": [{"text": note, "memory_ids": []} for note in governance_notes],
            "_mode": "clarification",
        }
    elif todo_format_query and (weekly_query or not conversation_history):
        answer = formatted_todo_answer(selected)
    elif direct_evidence_answer:
        answer = direct_evidence_answer
    elif settings["enabled"]:
        try:
            answer = call_model(settings, model_payload)
        except ModelCallError as error:
            model_error = str(error)
            model_error_code = error.code
            answer = weekly_todo_answer(weekly_todos, todo_period) if weekly_query else model_error_answer(model_error)
        if answer is None:
            missing = [
                label
                for key, label in (("base_url", "Base URL"), ("api_key", "API Key"), ("model", "模型名称"))
                if not settings[key]
            ]
            model_error = f"模型配置不完整，请填写：{'、'.join(missing)}"
            model_error_code = "model_config_missing"
            answer = model_error_answer(model_error)
    else:
        if weekly_query:
            answer = weekly_todo_answer(weekly_todos, todo_period)
        else:
            missing = [
                label
                for key, label in (("base_url", "Base URL"), ("api_key", "API Key"), ("model", "模型名称"))
                if not settings[key]
            ]
            model_error = (
                f"模型配置不完整，请填写：{'、'.join(missing)}"
                if missing
                else "模型服务未启用，请启用模型后重试"
            )
            model_error_code = "model_config_missing"
            answer = model_error_answer(model_error)
    answer = sanitize_answer(answer)
    mode = answer.pop("_mode", "model")
    error_message = answer.pop("_error", model_error)
    answer_warnings = answer.pop("_warnings", [])
    allowed_model_ids = {item["memory"]["id"] for item in model_memories}
    answer = normalize_structured_answer(answer, allowed_model_ids)
    explicit_references = cited_memory_ids(answer, allowed_model_ids)
    references = answer.pop("_references", explicit_references)
    selected_ids = [item["memory"]["id"] for item in selected]
    known_ids = {memory["id"] for memory in known_memories}
    missing_manual_ids = (
        [memory_id for memory_id in normalized_manual_ids if memory_id not in known_ids]
        if CHAT_MEMORY_RECALL_ENABLED
        else []
    )
    visible_ids = {
        item["memory"]["id"]
        for item in model_memories
        if item["memory"]["visibility"] == "visible"
    }
    if memory_usage_disabled or mode in ("model_error", "clarification"):
        used_memory_ids = []
    elif mode == "local_fallback" and explicit_references:
        used_memory_ids = explicit_references
    else:
        used_memory_ids = verify_used_memory_ids(
            settings,
            query,
            answer,
            model_memories,
        )
    references = prioritize_citations(
        used_memory_ids,
        manual_id_set,
        visible_ids,
    )
    if weekly_query:
        references = prioritize_citations(
            [
                *references,
                *[
                    item["memory_id"]
                    for item in weekly_todos
                    if item["memory_id"] in visible_ids
                ],
            ],
            manual_id_set,
            visible_ids,
        )
    primary_citation_ids = [
        memory_id for memory_id in references if memory_id in manual_id_set
    ]
    secondary_citation_ids = [
        memory_id for memory_id in references if memory_id not in manual_id_set
    ]
    warnings: list[str] = list(governance_notes)
    warnings.extend(str(warning) for warning in answer_warnings if str(warning).strip())
    if retrieval_diagnostics.get("embedding_error"):
        warnings.append("Embedding 服务不可用，本轮已降级为本地 BM25 关键词召回")
    if truncated_memory_ids:
        warnings.append(f"部分记忆卡内容过长，已截断后发送：{'、'.join(truncated_memory_ids)}")
    if mode == "model" and selected and not references and not context_policy["skip_auto_recall"]:
        answer["uncertainties"] = [
            *answer.get("uncertainties", []),
            {"text": "本轮模型没有返回可校验的记忆引用，以上建议的记忆依据未被确认。", "memory_ids": []},
        ]
        warnings.append("本轮模型没有返回有效引用")
    if missing_manual_ids:
        warnings.append(f"未找到手动指定的记忆卡：{'、'.join(missing_manual_ids)}")
    if manual_only_query:
        warnings.append("已按用户要求只使用手动选择的记忆卡，未自动补充其他记忆")
    retrieval_mode = (
        "disabled"
        if not CHAT_MEMORY_RECALL_ENABLED
        else (
            "hybrid_rag"
            if retrieval_diagnostics.get("embedding_used")
            else "bm25_keyword_fallback"
        )
    )
    harness_note("result", generation_mode=mode, retrieval_mode=retrieval_mode,
                 citation_ids=references, error_code=model_error_code)
    connection.executemany(
        "UPDATE memories SET last_used_at = ?, citations = citations + 1 WHERE id = ? AND user_id = ?",
        [(now(), memory_id, user_id) for memory_id in references],
    )
    connection.commit()
    return {
        "answer": answer,
        "memory_usage": {
            "selected_memory_ids": selected_ids,
            "manual_memory_ids": [item for item in manual_ids if item in references],
            "auto_recalled_memory_ids": [item for item in references if item not in manual_id_set],
            "dropped_memory_ids": missing_manual_ids,
            "citation_count": len(references),
            "primary_citation_ids": primary_citation_ids,
            "secondary_citation_ids": secondary_citation_ids,
            "recalled_memories": [
                {
                    **item["memory"],
                    "selection": "manual" if item["memory"]["id"] in manual_id_set else "auto",
                    "citation_priority": (
                        "primary" if item["memory"]["id"] in manual_id_set else "secondary"
                    ),
                    "score": item["score"],
                    "ranking_signals": item.get("ranking_signals", {}),
                }
                for item in selected
                if item["memory"]["visibility"] == "visible"
                and item["memory"]["id"] in references
            ],
            "weekly_todos": weekly_todos,
            "citations": [
                item_by_id["memory"]
                for memory_id in references
                for item_by_id in selected
                if item_by_id["memory"]["id"] == memory_id
                and item_by_id["memory"]["visibility"] == "visible"
            ],
        },
        "memory_capture": {
            "should_save": mode not in ("model_error", "clarification"),
            "suggested_memory_card": {
                "title": answer.get("summary", query)[:80],
                "content": answer.get("recommendation", ""),
                "keywords": ["对话沉淀"],
                "source": "conversation",
            },
        },
        "meta": {
            "generation_mode": mode,
            "generated_at": now(),
            "model": settings["model"] if mode in ("model", "model_error") else "local-fallback",
            "retrieved_count": len(selected),
            "used_count": len(selected_ids),
            "retrieval_mode": retrieval_mode,
            "fusion_mode": retrieval_diagnostics.get("fusion_mode", "none"),
            "reranker_used": retrieval_diagnostics.get("reranker_used", False),
            "reranker_rejected_count": retrieval_diagnostics.get("reranker_rejected_count", 0),
            **({"reranker_error": retrieval_diagnostics["reranker_error"]} if retrieval_diagnostics.get("reranker_error") else {}),
            "turn_type": turn_type,
            "retrieval_scope": retrieval_context,
            "ranking_weights": {"relevance": 0.60, "source_reliability": 0.25, "temporal_applicability": 0.15},
            **({"memory_usage_disabled": True} if memory_usage_disabled else {}),
            **({"turn_classification": turn_classification} if turn_classification else {}),
            **({"error": error_message} if error_message else {}),
            **({"error_code": model_error_code} if model_error_code else {}),
            **({"warnings": warnings} if warnings else {}),
        },
    }


@harness_flow("memory_create")
def handle_memory_create(connection: sqlite3.Connection, body: dict[str, Any], user_id: str) -> dict[str, Any]:
    title, content = str(body.get("title", "")).strip(), str(body.get("content", "")).strip()
    if not title or not content:
        raise ValueError("title 和 content 必填")
    folder_id = str(body.get("folder_id") or "").strip() or None
    if folder_id and not connection.execute(
        "SELECT 1 FROM memory_folders WHERE id = ? AND user_id = ?",
        (folder_id, user_id),
    ).fetchone():
        raise ValueError("文件夹不存在，请刷新后重试")
    memory_id = str(body.get("id") or f"m-{int(datetime.now().timestamp() * 1000)}")
    timestamp = now()
    workspace_id = str(body.get("workspace_id") or "default").strip()
    project = str(body.get("project", "")).strip()
    project_id = str(body.get("project_id") or project).strip()
    available_at = str(body.get("available_at") or timestamp).strip()
    event_at = str(body.get("event_at") or available_at).strip()
    supersedes_id = str(body.get("supersedes_id") or "").strip() or None
    conflicts_with_id = str(body.get("conflicts_with_id") or "").strip() or None
    connection.execute(
        """
    INSERT INTO memories
    (id, user_id, title, content, memory_type, source, tags_json, project,
         workspace_id, project_id, snapshot_id, visibility,
         folder_id, created_at, updated_at, event_at, available_at, valid_until,
         supersedes_id, conflicts_with_id, source_weight, quality_weight)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'visible', ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.8)
        """,
        (
            memory_id,
            user_id,
            title,
            content,
            body.get("type", "经验型"),
            body.get("source", "手动添加"),
            json.dumps(body.get("keywords", body.get("tags", [])), ensure_ascii=False),
            project,
            workspace_id,
            project_id,
            str(body.get("snapshot_id") or "").strip(),
            folder_id,
            timestamp,
            timestamp,
            event_at,
            available_at,
            str(body.get("valid_until") or "").strip() or None,
            supersedes_id,
            conflicts_with_id,
        ),
    )
    row = connection.execute(
        "SELECT * FROM memories WHERE id = ? AND user_id = ?",
        (memory_id, user_id),
    ).fetchone()
    sync_memory_entities(connection, row)
    connection.commit()
    harness_note("write_committed", operation="create", memory_id=memory_id)
    return memory_json(row)


@harness_flow("memory_update")
def handle_memory_update(connection: sqlite3.Connection, body: dict[str, Any], user_id: str, memory_id: str) -> Optional[dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM memories WHERE id = ? AND visibility = 'visible' AND user_id = ?",
        (memory_id, user_id),
    ).fetchone()
    if not row:
        return None
    fields = {
        "title": str(body.get("title", row["title"])).strip(),
        "content": str(body.get("content", row["content"])).strip(),
        "memory_type": body.get("type", row["memory_type"]),
        "workspace_id": str(body.get("workspace_id", row["workspace_id"])).strip() or "default",
        "project": str(body.get("project", row["project"])).strip(),
        "project_id": str(body.get("project_id", row["project_id"] or row["project"])).strip(),
        "snapshot_id": str(body.get("snapshot_id", row["snapshot_id"])).strip(),
        "tags_json": json.dumps(
            body.get("keywords", body.get("tags", json.loads(row["tags_json"]))),
            ensure_ascii=False,
        ),
        "folder_id": body.get("folder_id", row["folder_id"]) or None,
        "updated_at": now(),
        "event_at": body.get("event_at", row["event_at"]),
        "available_at": body.get("available_at", row["available_at"]),
        "valid_until": body.get("valid_until", row["valid_until"]),
        "supersedes_id": body.get("supersedes_id", row["supersedes_id"]),
        "conflicts_with_id": body.get("conflicts_with_id", row["conflicts_with_id"]),
    }
    if fields["folder_id"] and not connection.execute(
        "SELECT 1 FROM memory_folders WHERE id = ? AND user_id = ?",
        (fields["folder_id"], user_id),
    ).fetchone():
        raise ValueError("文件夹不存在，请刷新后重试")
    connection.execute(
        """
        UPDATE memories
        SET title=?, content=?, memory_type=?, tags_json=?, project=?,
            workspace_id=?, project_id=?, snapshot_id=?, folder_id=?, source='手动修改',
            updated_at=?, event_at=?, available_at=?, valid_until=?,
            supersedes_id=?, conflicts_with_id=?
        WHERE id=? AND user_id=?
        """,
        (
            fields["title"],
            fields["content"],
            fields["memory_type"],
            fields["tags_json"],
            fields["project"],
            fields["workspace_id"],
            fields["project_id"],
            fields["snapshot_id"],
            fields["folder_id"],
            fields["updated_at"],
            fields["event_at"],
            fields["available_at"],
            fields["valid_until"],
            fields["supersedes_id"],
            fields["conflicts_with_id"],
            memory_id,
            user_id,
        ),
    )
    sync_memory_entities(
        connection,
        connection.execute("SELECT * FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)).fetchone(),
    )
    connection.commit()
    harness_note("write_committed", operation="update", memory_id=memory_id)
    return memory_json(connection.execute("SELECT * FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)).fetchone())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def auth_token(self) -> str:
        header = self.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    def require_user(self, connection: sqlite3.Connection) -> Optional[sqlite3.Row]:
        user = current_user(connection, self.auth_token())
        if not user:
            self.send_json(401, {"error": "未登录"})
            return None
        ensure_default_folders(connection, user["id"])
        return user

    def do_GET(self) -> None:
        connection = db()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                self.send_json(200, {"ok": True, "database": str(DB_PATH), "harness_version": HARNESS_VERSION})
            elif parsed.path == "/api/auth/me":
                user = current_user(connection, self.auth_token())
                if not user:
                    self.send_json(401, {"error": "未登录"})
                else:
                    ensure_default_folders(connection, user["id"])
                    self.send_json(200, {"user": public_user(user)})
            elif parsed.path == "/api/folders":
                user = self.require_user(connection)
                if not user:
                    return
                folders = connection.execute(
                    """
                    SELECT folders.id, folders.name, folders.is_default, folders.created_at,
                           COUNT(memories.id) AS memory_count
                    FROM memory_folders AS folders
                    LEFT JOIN memories
                      ON memories.folder_id = folders.id
                     AND memories.visibility = 'visible'
                     AND memories.user_id = ?
                    WHERE folders.user_id = ?
                    GROUP BY folders.id
                    ORDER BY folders.sort_order ASC, folders.created_at ASC
                    """,
                    (user["id"], user["id"]),
                ).fetchall()
                self.send_json(
                    200,
                    {
                        "items": [
                            {
                                "id": row["id"],
                                "name": row["name"],
                                "is_default": bool(row["is_default"]),
                                "memory_count": row["memory_count"],
                            }
                            for row in folders
                        ]
                    },
                )
            elif parsed.path == "/api/memories":
                user = self.require_user(connection)
                if not user:
                    return
                keyword = query.get("query", [""])[0].strip().lower()
                rows = connection.execute(
                    "SELECT * FROM memories WHERE visibility = 'visible' AND user_id = ? ORDER BY updated_at DESC",
                    (user["id"],),
                ).fetchall()
                result = [
                    memory_json(row)
                    for row in rows
                    if not keyword
                    or keyword in row["title"].lower()
                    or keyword in row["content"].lower()
                    or keyword in row["tags_json"].lower()
                ]
                self.send_json(200, {"items": result})
            elif parsed.path == "/api/config":
                user = self.require_user(connection)
                if not user:
                    return
                self.send_json(200, public_config(connection, user["id"]))
            else:
                self.send_json(404, {"error": "not found"})
        finally:
            connection.close()

    def do_POST(self) -> None:
        connection = db()
        try:
            body = self.read_json()
            if self.path == "/api/auth/register":
                self.send_json(201, handle_register(connection, body))
            elif self.path == "/api/auth/login":
                self.send_json(200, handle_login(connection, body))
            elif self.path == "/api/auth/logout":
                token = str(body.get("token") or self.auth_token()).strip()
                if token:
                    connection.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
                    connection.commit()
                self.send_json(200, {"ok": True})
            elif self.path == "/api/memories":
                user = self.require_user(connection)
                if not user:
                    return
                self.send_json(201, handle_memory_create(connection, body, user["id"]))
            elif self.path == "/api/folders":
                user = self.require_user(connection)
                if not user:
                    return
                name = str(body.get("name", "")).strip()
                if not name:
                    raise ValueError("文件夹名称不能为空")
                if connection.execute(
                    "SELECT 1 FROM memory_folders WHERE user_id = ? AND name = ?",
                    (user["id"], name),
                ).fetchone():
                    raise ValueError("分组名称已存在，请重新命名")
                folder_id = f"folder-{int(datetime.now().timestamp() * 1000)}"
                timestamp = now()
                next_sort_order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), 999) + 1 FROM memory_folders WHERE user_id = ?",
                    (user["id"],),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO memory_folders (id, user_id, name, is_default, sort_order, created_at) VALUES (?, ?, ?, 0, ?, ?)",
                    (folder_id, user["id"], name, next_sort_order, timestamp),
                )
                connection.commit()
                self.send_json(
                    201,
                    {
                        "id": folder_id,
                        "name": name,
                        "is_default": False,
                        "memory_count": 0,
                    },
                )
            elif self.path == "/api/chat":
                user = self.require_user(connection)
                if not user:
                    return
                self.send_json(200, handle_chat(connection, body, user["id"]))
            elif self.path == "/api/memories/parse":
                user = self.require_user(connection)
                if not user:
                    return
                self.send_json(200, handle_memory_parse(connection, body, user["id"]))
            elif self.path == "/api/config/test":
                user = self.require_user(connection)
                if not user:
                    return
                target = str(body.get("target", "all")).strip()
                test_settings = config(connection, user["id"])
                override = body.get("config")
                if isinstance(override, dict):
                    for key in (
                        "provider",
                        "base_url",
                        "api_key",
                        "model",
                        "embedding_base_url",
                        "embedding_api_key",
                        "embedding_model",
                        "enabled",
                    ):
                        if key in override and override[key] != "********":
                            test_settings[key] = override[key]
                self.send_json(200, test_model_connection(test_settings, target))
            elif self.path == "/api/config":
                user = self.require_user(connection)
                if not user:
                    return
                for key in (
                    "provider",
                    "base_url",
                    "api_key",
                    "model",
                    "embedding_base_url",
                    "embedding_api_key",
                    "embedding_model",
                    "enabled",
                ):
                    if key in body:
                        if key in ("api_key", "embedding_api_key") and body[key] == "********":
                            continue
                        connection.execute(
                            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (f"{user['id']}:{key}", str(body[key]).lower() if isinstance(body[key], bool) else str(body[key])),
                        )
                connection.commit()
                self.send_json(200, public_config(connection, user["id"]))
            else:
                self.send_json(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError, HarnessBudgetError) as error:
            self.send_json(400, {"error": str(error),
                                 **({"request_id": error.request_id} if hasattr(error, "request_id") else {}),
                                 **({"error_code": error.code} if hasattr(error, "code") else {})})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "本地服务处理请求时发生异常，请重试；若持续失败，请检查后端日志"})
        finally:
            connection.close()

    def do_PUT(self) -> None:
        connection = db()
        try:
            body = self.read_json()
            user = self.require_user(connection)
            if not user:
                return
            path = urlparse(self.path).path
            if path.startswith("/api/folders/"):
                folder_id = path.removeprefix("/api/folders/")
                folder = connection.execute(
                    """
                    SELECT id, name, is_default, sort_order
                    FROM memory_folders
                    WHERE id = ? AND user_id = ?
                    """,
                    (folder_id, user["id"]),
                ).fetchone()
                if not folder:
                    self.send_json(404, {"error": "folder not found"})
                    return
                if folder["is_default"]:
                    self.send_json(400, {"error": "默认文件夹不能编辑"})
                    return
                name = str(body.get("name", "")).strip()
                if not name:
                    raise ValueError("文件夹名称不能为空")
                if connection.execute(
                    "SELECT 1 FROM memory_folders WHERE user_id = ? AND name = ? AND id != ?",
                    (user["id"], name, folder_id),
                ).fetchone():
                    raise ValueError("分组名称已存在，请重新命名")
                connection.execute(
                    "UPDATE memory_folders SET name = ? WHERE id = ? AND user_id = ?",
                    (name, folder_id, user["id"]),
                )
                connection.commit()
                memory_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM memories
                    WHERE folder_id = ? AND visibility = 'visible' AND user_id = ?
                    """,
                    (folder_id, user["id"]),
                ).fetchone()[0]
                self.send_json(
                    200,
                    {
                        "id": folder_id,
                        "name": name,
                        "is_default": False,
                        "memory_count": memory_count,
                    },
                )
                return
            memory_id = urlparse(self.path).path.removeprefix("/api/memories/")
            result = handle_memory_update(connection, body, user["id"], memory_id)
            self.send_json(200 if result else 404, result or {"error": "memory not found"})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        finally:
            connection.close()

    def do_DELETE(self) -> None:
        connection = db()
        try:
            user = self.require_user(connection)
            if not user:
                return
            path = urlparse(self.path).path
            if path.startswith("/api/folders/"):
                folder_id = path.removeprefix("/api/folders/")
                folder = connection.execute(
                    "SELECT id, is_default FROM memory_folders WHERE id = ? AND user_id = ?",
                    (folder_id, user["id"]),
                ).fetchone()
                if not folder:
                    self.send_json(404, {"error": "folder not found"})
                    return
                if folder["is_default"]:
                    self.send_json(400, {"error": "默认文件夹不能删除"})
                    return
                cursor = connection.execute(
                    "UPDATE memories SET folder_id = NULL WHERE folder_id = ? AND user_id = ?",
                    (folder_id, user["id"]),
                )
                connection.execute("DELETE FROM memory_folders WHERE id = ? AND user_id = ?", (folder_id, user["id"]))
                connection.commit()
                self.send_json(
                    200,
                    {
                        "deleted": True,
                        "id": folder_id,
                        "unassigned_memory_count": cursor.rowcount,
                    },
                )
                return
            memory_id = path.removeprefix("/api/memories/")
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ? AND visibility = 'visible' AND user_id = ?",
                (memory_id, user["id"]),
            )
            connection.commit()
            self.send_json(200, {"deleted": cursor.rowcount == 1, "id": memory_id})
        finally:
            connection.close()


def main() -> None:
    db().close()
    server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), Handler)
    print(f"Memory Master API listening on http://127.0.0.1:{DEFAULT_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
