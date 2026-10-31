"""Regression tests for bounded chat failures and DeepSeek structured output."""

import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

SPEC = importlib.util.spec_from_file_location(
    "chat_server", Path(__file__).resolve().parents[1] / "services/api/server.py"
)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
SETTINGS = {
    "enabled": True, "base_url": "https://api.deepseek.com/v1",
    "api_key": "test", "model": "deepseek-v4-pro",
}
ANSWER = {"summary": "准备办公设备", "recommendation": "提前确认网络和会议安排。"}


def response(content=None, finish_reason="stop"):
    return io.BytesIO(json.dumps({"choices": [{
        "finish_reason": finish_reason,
        "message": {"content": content, "reasoning_content": "private reasoning"},
    }]}).encode())


class ChatFailureTests(unittest.TestCase):
    def test_launch_fact_query_accepts_direct_answer_without_retry(self):
        answer = {
            "summary": "青禾项目于2026年10月25日上线，负责人是李华。",
            "recommendation": "上线日期：2026-10-25。负责人：李华。",
            "reasoning_points": [], "action_items": [], "uncertainties": [],
        }
        with patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response(json.dumps(answer))) as request:
            result = server.call_model(SETTINGS, {"query": "只根据我选的卡，青禾项目什么时候上线，负责人是谁？"})
        self.assertEqual(result["recommendation"], answer["recommendation"])
        self.assertEqual(result["_mode"], "model")
        self.assertEqual(request.call_count, 1)

    def test_launch_decision_still_requires_recommendation(self):
        for query in ("青禾项目要不要上线？", "青禾项目该不该上线？", "是否上线？"):
            with self.subTest(query=query):
                self.assertEqual(
                    server.model_answer_quality_error(query, {"summary": "负责人是李华。"}),
                    "决策问题未给出明确结论",
                )
                self.assertIsNone(server.model_answer_quality_error(query, {"summary": "建议暂缓上线，先确认验收结果。"}))

    def test_deepseek_returns_answer_without_spending_budget_on_thinking(self):
        with patch.object(server.urllib.request, "urlopen", return_value=response(json.dumps(ANSWER))) as request:
            result = server.call_model(SETTINGS, {"query": "居家办公准备什么？"})
        self.assertEqual(result["recommendation"], ANSWER["recommendation"])
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(server.structured_model_options({"base_url": "https://other.test/v1"}), {})

    def test_truncated_reasoning_does_not_repeat_identical_request(self):
        with patch.object(server.urllib.request, "urlopen", return_value=response("", "length")) as request:
            with self.assertRaises(server.ModelCallError) as raised:
                server.call_model(SETTINGS, {"query": "居家办公准备什么？"})
        self.assertEqual(raised.exception.code, "model_output_truncated")
        self.assertEqual(request.call_count, 1)

    def test_empty_content_is_never_replaced_with_reasoning(self):
        with patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response("")) as request, patch.object(server.time, "sleep"):
            with self.assertRaises(server.ModelCallError) as raised:
                server.call_model(SETTINGS, {"query": "测试"})
        self.assertEqual(raised.exception.code, "model_empty_response")
        self.assertEqual(request.call_count, 3)

    def test_retries_share_timeout_budget(self):
        with patch.object(server.time, "monotonic", side_effect=[0, 0, 45, 61]), patch.object(server.time, "sleep"), patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: response("")) as request:
            with self.assertRaises(server.ModelCallError) as raised:
                server.call_model(SETTINGS, {"query": "测试"})
        self.assertEqual(raised.exception.code, "model_timeout")
        self.assertEqual([call.kwargs["timeout"] for call in request.call_args_list], [60, 15])

    def test_malformed_response_is_classified(self):
        with patch.object(server.time, "sleep"), patch.object(server.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b'{"choices":null}')):
            with self.assertRaises(server.ModelCallError) as raised:
                server.call_model(SETTINGS, {"query": "测试"})
        self.assertEqual(raised.exception.code, "model_response_invalid")

    def test_invalid_json_can_recover_on_retry(self):
        with patch.object(server.time, "sleep"), patch.object(server.urllib.request, "urlopen", side_effect=[response("bad json"), response(json.dumps(ANSWER))]):
            result = server.call_model(SETTINGS, {"query": "测试"})
        self.assertEqual(result["_mode"], "model")

    def test_action_items_must_recover_to_structured_objects(self):
        invalid = {**ANSWER, "action_items": ["提前测试网络"]}
        valid = {**ANSWER, "action_items": [{"text": "提前测试网络", "priority": "high"}]}
        with patch.object(server.time, "sleep"), patch.object(server.urllib.request, "urlopen", side_effect=[response(json.dumps(invalid)), response(json.dumps(valid))]):
            result = server.call_model(SETTINGS, {"query": "测试"})
        self.assertEqual(result["action_items"], valid["action_items"])

    def test_model_error_cannot_be_saved_or_trigger_citation_verification(self):
        with patch.object(server, "config", return_value=SETTINGS), patch.object(server, "retrieve", return_value=[]), patch.object(server, "local_home_office_conflict_selection", return_value=[]), patch.object(server, "call_model", side_effect=server.ModelCallError("内容为空", "model_empty_response")), patch.object(server, "verify_used_memory_ids") as verify:
            connection = Mock()
            connection.execute.return_value.fetchall.return_value = []
            result = server.handle_chat(connection, {"query": "测试"}, "test-user")
        self.assertEqual(result["meta"]["error_code"], "model_empty_response")
        self.assertFalse(result["memory_capture"]["should_save"])
        verify.assert_not_called()

    def test_unexpected_chat_failure_returns_json_500_instead_of_disconnect(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "db", return_value=Mock()), patch.object(server.Handler, "require_user", return_value={"id": "test"}), patch.object(server, "handle_chat", side_effect=RuntimeError("test failure")), patch.object(server.traceback, "print_exc") as log:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{http.server_port}/api/chat", data=b'{"query":"test"}',
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(raised.exception.code, 500)
                self.assertIn("本地服务处理请求", json.load(raised.exception)["error"])
                log.assert_called_once()
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
