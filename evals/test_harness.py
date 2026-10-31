"""Harness integration uses isolated SQLite databases and deterministic providers."""

import concurrent.futures
import http.client
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from services.api import server


SETTINGS = {
    "enabled": True, "base_url": "https://provider.test/v1", "api_key": "private-api-key",
    "model": "test-model", "embedding_model": "",
}
ANSWER = {"summary": "研发建议", "recommendation": "先确认研发目标。", "reasoning_points": [],
          "action_items": [], "uncertainties": []}


def response(answer=ANSWER, usage=17):
    body = {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(answer, ensure_ascii=False) if isinstance(answer, dict) else answer,
    }}]}
    if usage is not None:
        body["usage"] = {"total_tokens": usage}
    return io.BytesIO(json.dumps(body, ensure_ascii=False).encode())


def database(path=":memory:"):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(server.SCHEMA)
    return connection


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.db = database()
        self.addCleanup(self.db.close)
        self.config = patch.object(server, "config", return_value=SETTINGS)
        self.config.start()
        self.addCleanup(self.config.stop)

    def chat(self, **body):
        return server.handle_chat(self.db, {"query": "如何安排研发工作？", **body}, "alice")

    def trace(self, result):
        row = self.db.execute("SELECT * FROM harness_runs WHERE id = ? AND user_id = ?",
                              (result["meta"]["harness"]["request_id"], "alice")).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row["record_json"])

    def test_success_persists_stages_usage_and_no_content(self):
        with patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response()):
            result = self.chat(query="private-user-query 如何安排研发？")
        trace = self.trace(result)
        self.assertEqual(result["meta"]["generation_mode"], "model")
        self.assertEqual(trace["provider_calls"], 1)
        self.assertEqual(trace["total_tokens"], 17)
        self.assertTrue(trace["config_id"])
        stages = {item["stage"] for item in trace["events"]}
        self.assertTrue({"build_context_policy", "retrieve", "govern_memories", "call_model", "result"} <= stages)
        encoded = json.dumps(trace, ensure_ascii=False)
        for private in ("private-user-query", SETTINGS["api_key"], ANSWER["recommendation"]):
            self.assertNotIn(private, encoded)
        self.assertIsNone(server.ACTIVE_HARNESS.get())

    def test_missing_usage_remains_unknown(self):
        with patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response(usage=None)):
            result = self.chat()
        self.assertIsNone(self.trace(result)["total_tokens"])
        self.assertFalse(result["meta"]["harness"]["usage_complete"])

    def test_retry_is_bounded_and_all_attempt_usage_is_counted(self):
        with patch.object(server.urllib.request, "urlopen", side_effect=[response("invalid", 11), response(usage=17)]) as request, patch.object(server.time, "sleep"):
            result = self.chat()
        trace = self.trace(result)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(trace["total_tokens"], 28)
        self.assertTrue(any(event["stage"] == "retry" for event in trace["events"]))

    def test_call_budget_prevents_next_attempt_and_save_suggestion(self):
        with patch.object(server, "HARNESS_MAX_CALLS", 2), patch.object(server.time, "sleep"), patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response("invalid")) as request:
            result = self.chat()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["meta"]["error_code"], "harness_call_limit")
        self.assertFalse(result["memory_capture"]["should_save"])
        self.assertEqual(self.trace(result)["status"], "model_error")

    def test_expired_request_never_calls_provider(self):
        with patch.object(server, "HARNESS_TIMEOUT_SECONDS", 0), patch.object(server.urllib.request, "urlopen") as request:
            result = self.chat()
        request.assert_not_called()
        self.assertEqual(result["meta"]["error_code"], "harness_timeout")
        self.assertFalse(result["memory_capture"]["should_save"])

    def test_deadline_during_response_stops_subsequent_calls(self):
        def expire(*args, **kwargs):
            server.ACTIVE_HARNESS.get().deadline = server.time.perf_counter() - 1
            return response()
        with patch.object(server.urllib.request, "urlopen", side_effect=expire) as request:
            result = self.chat()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["meta"]["error_code"], "harness_timeout")

    def test_context_limit_is_explicit_and_does_not_send_request(self):
        with patch.object(server, "HARNESS_CONTEXT_CHAR_LIMIT", 100), patch.object(server.urllib.request, "urlopen") as request:
            result = self.chat()
        request.assert_not_called()
        self.assertEqual(result["meta"]["error_code"], "harness_context_limit")

    def test_optional_embedding_budget_falls_back_and_reserves_generation(self):
        settings = {**SETTINGS, "embedding_model": "vector", "embedding_base_url": "https://provider.test/v1", "embedding_api_key": "private-vector-key"}
        with patch.object(server, "config", return_value=settings), patch.object(server, "HARNESS_MAX_CALLS", 4), patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response()) as request:
            result = self.chat()
        self.assertEqual(result["meta"]["generation_mode"], "model")
        self.assertEqual(result["meta"]["retrieval_mode"], "bm25_keyword_fallback")
        self.assertEqual(request.call_count, 1)
        self.assertTrue(any(event["stage"] == "retrieval_fallback" for event in self.trace(result)["events"]))

    def test_optional_preparation_time_budget_skips_network(self):
        run = server.HarnessRun("chat")
        run.started -= 21
        token = server.ACTIVE_HARNESS.set(run)
        try:
            request = urllib.request.Request("https://provider.test", data=b'{}')
            with patch.object(server.urllib.request, "urlopen") as network:
                with self.assertRaises(ValueError):
                    server.provider_json(request, 8, "embedding")
            network.assert_not_called()
            self.assertEqual(run.calls, 0)
        finally:
            server.ACTIVE_HARNESS.reset(token)

    def test_rate_limit_recovery_has_attempt_status(self):
        failure = urllib.error.HTTPError("https://provider.test", 429, "private-body", {}, None)
        with patch.object(server.time, "sleep"), patch.object(server.urllib.request, "urlopen", side_effect=[failure, response()]):
            result = self.chat()
        calls = [event for event in self.trace(result)["events"] if event["kind"] == "provider"]
        self.assertEqual([item["status"] for item in calls], ["error", "ok"])
        self.assertNotIn("private-body", json.dumps(calls))
        self.assertIsNone(result["meta"]["harness"]["total_tokens"])

    def test_parse_budget_failure_does_not_write_a_card(self):
        with patch.object(server, "HARNESS_TIMEOUT_SECONDS", 0), patch.object(server.urllib.request, "urlopen") as request:
            with self.assertRaises(server.HarnessBudgetError):
                server.handle_memory_parse(self.db, {"content": "原始内容"}, "alice")
        request.assert_not_called()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT status FROM harness_runs").fetchone()[0], "error")
        self.assertIsNone(server.ACTIVE_HARNESS.get())

    def test_parse_success_only_returns_draft(self):
        draft = {"title": "研发记录", "content": "原始内容", "keywords": [], "folder": ""}
        with patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response(draft)):
            result = server.handle_memory_parse(self.db, {"content": "原始内容"}, "alice")
        self.assertEqual(self.trace(result)["flow"], "memory_parse")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)

    def test_format_revision_skips_retrieval_and_records_route(self):
        classification = {"turn_type": "format_revision", "confidence": 1, "reason": "private-reason"}
        with patch.object(server.urllib.request, "urlopen", side_effect=[response(classification), response()]), patch.object(server, "retrieve") as retrieve:
            result = self.chat(query="改成表格", conversation_history=[
                {"role": "user", "text": "研发工作如何安排？"},
                {"role": "assistant", "text": "先确认研发目标。"},
            ])
        retrieve.assert_not_called()
        route = next(event for event in self.trace(result)["events"] if event["stage"] == "route")
        self.assertEqual(route["turn_type"], "format_revision")
        self.assertTrue(route["skip_auto_recall"])

    def test_conflict_clarification_is_traced_without_generation(self):
        for memory_id, date in (("old", "2026-11-01"), ("new", "2026-11-02")):
            server.handle_memory_create(self.db, {"id": memory_id, "title": "发布排期",
                                                "content": "上线日期：" + date, "project": "甲项目"}, "alice")
        with patch.object(server.urllib.request, "urlopen") as network:
            result = self.chat(query="甲项目的上线日期？", manual_memory_ids=["old", "new"])
        network.assert_not_called()
        self.assertEqual(self.trace(result)["status"], "clarification")
        self.assertFalse(result["memory_capture"]["should_save"])

    def test_generation_and_citation_share_budget_and_usage(self):
        server.handle_memory_create(self.db, {"id": "visible", "title": "研发目标", "content": "先确认研发目标。"}, "alice")
        with patch.object(server.urllib.request, "urlopen", side_effect=[response(), response({"used_memory_ids": ["visible"]}, 5)]):
            result = self.chat(manual_memory_ids=["visible"])
        trace = self.trace(result)
        self.assertEqual(trace["total_tokens"], 22)
        self.assertEqual(trace["provider_calls"], 2)
        self.assertEqual(result["memory_usage"]["citation_count"], 1)
        event = next(item for item in trace["events"] if item["stage"] == "result")
        self.assertEqual(event["citation_ids"], ["visible"])

    def test_slow_response_body_is_bounded_across_chunks(self):
        class SlowProvider(server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                try:
                    for _ in range(20):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), SlowProvider)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started = time.perf_counter()
        try:
            with patch.object(server, "HARNESS_TIMEOUT_SECONDS", 0.15), patch.object(server, "config", return_value={**SETTINGS, "base_url": f"http://127.0.0.1:{httpd.server_port}"}):
                result = self.chat()
            self.assertLess(time.perf_counter() - started, 0.8)
            self.assertEqual(result["meta"]["generation_mode"], "model_error")
            self.assertEqual(result["meta"]["harness"]["provider_calls"], 1)
            self.assertFalse(result["memory_capture"]["should_save"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_manual_write_update_and_cross_account_isolation(self):
        created = server.handle_memory_create(self.db, {"id": "visible", "title": "研发计划", "content": "原文"}, "alice")
        self.assertEqual(self.trace(created)["flow"], "memory_create")
        updated = server.handle_memory_update(self.db, {"content": "用户修改"}, "alice", "visible")
        self.assertEqual(self.trace(updated)["flow"], "memory_update")
        self.assertIsNone(server.handle_memory_update(self.db, {"content": "其他账号"}, "bob", "visible"))
        self.assertEqual(self.db.execute("SELECT content FROM memories WHERE id = 'visible'").fetchone()[0], "用户修改")
        self.assertEqual(self.db.execute("SELECT status FROM harness_runs WHERE user_id = 'bob'").fetchone()[0], "not_found")

    def test_failed_write_rolls_back_without_logging_content(self):
        with patch.object(server, "sync_memory_entities", side_effect=RuntimeError("private-database-detail")):
            with self.assertRaises(RuntimeError):
                server.handle_memory_create(self.db, {"id": "broken", "title": "title", "content": "private-content"}, "alice")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        record = self.db.execute("SELECT record_json FROM harness_runs").fetchone()[0]
        self.assertNotIn("private-content", record)
        self.assertNotIn("private-database-detail", record)
        self.assertIsNone(server.ACTIVE_HARNESS.get())

    def test_trace_storage_failure_preserves_successful_write(self):
        self.db.execute("DROP TABLE harness_runs")
        result = server.handle_memory_create(self.db, {"id": "saved", "title": "title", "content": "content"}, "alice")
        self.assertFalse(result["meta"]["harness"]["trace_persisted"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)

    def test_registration_then_reopening_database_preserves_folder_ownership(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "DB_PATH", Path(directory) / "startup.db"):
            connection = server.db()
            try:
                user = server.handle_register(connection, {"account": "startup-user", "password": "test-password"})["user"]
                before = [tuple(row) for row in connection.execute("SELECT id, user_id, name FROM memory_folders ORDER BY id")]
            finally:
                connection.close()
            connection = server.db()
            try:
                after = [tuple(row) for row in connection.execute("SELECT id, user_id, name FROM memory_folders ORDER BY id")]
                self.assertEqual(before, after)
                result = server.handle_memory_create(connection, {"title": "记录", "content": "用户主动保存"}, user["id"])
                self.assertTrue(result["meta"]["harness"]["trace_persisted"])
            finally:
                connection.close()

    def test_trace_retention_is_bounded_per_account(self):
        for owner in ("alice", "bob"):
            self.db.execute("INSERT INTO harness_runs VALUES (?, ?, ?, 'chat', 'ok', '{}')", (owner, owner, "2000-01-01T00:00:00+00:00"))
        self.db.commit()
        with patch.object(server, "HARNESS_MAX_RUNS_PER_USER", 2), patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response()):
            for _ in range(3):
                self.chat()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM harness_runs WHERE user_id = 'alice'").fetchone()[0], 2)
        self.assertIsNone(self.db.execute("SELECT id FROM harness_runs WHERE id = 'alice'").fetchone())
        self.assertIsNotNone(self.db.execute("SELECT id FROM harness_runs WHERE id = 'bob'").fetchone())

    def test_hidden_candidates_only_record_counts(self):
        card = server.handle_memory_create(self.db, {"id": "private-hidden-id", "title": "private-title", "content": "private-hidden-content"}, "alice")
        self.db.execute("UPDATE memories SET visibility = 'hidden'")
        self.db.commit()
        with patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response()):
            result = self.chat()
        record = json.dumps(self.trace(result))
        for secret in (card["id"], "private-title", "private-hidden-content"):
            self.assertNotIn(secret, record)
        self.assertIn('"hidden_count": 1', record)

    def test_parallel_requests_do_not_share_context(self):
        barrier = threading.Barrier(2)
        def provider(*args, **kwargs):
            barrier.wait(timeout=3)
            return response()
        def task(owner):
            connection = database()
            try:
                result = server.handle_chat(connection, {"query": "研发工作如何安排？"}, owner)
                row = connection.execute("SELECT user_id, id FROM harness_runs").fetchone()
                self.assertEqual(row["user_id"], owner)
                self.assertIsNone(server.ACTIVE_HARNESS.get())
                return result["meta"]["harness"]
            finally:
                connection.close()
        with patch.object(server.urllib.request, "urlopen", side_effect=provider), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(task, ["alice", "bob"]))
        self.assertNotEqual(results[0]["request_id"], results[1]["request_id"])
        self.assertEqual([result["provider_calls"] for result in results], [1, 1])

    def test_http_chat_returns_persisted_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            database(path).close()
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "db", side_effect=lambda: database(path)), patch.object(server.Handler, "require_user", return_value={"id": "alice"}), patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response()):
                    client = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                    client.request("POST", "/api/chat", json.dumps({"query": "如何安排研发？"}), {"Content-Type": "application/json"})
                    reply = client.getresponse()
                    self.assertEqual(reply.status, 200)
                    result = json.loads(reply.read())
                    client.close()
                connection = database(path)
                try:
                    self.assertIsNotNone(connection.execute("SELECT id FROM harness_runs WHERE id = ? AND user_id = 'alice'", (result["meta"]["harness"]["request_id"],)).fetchone())
                finally:
                    connection.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
