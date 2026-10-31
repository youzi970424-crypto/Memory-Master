import sqlite3
import unittest
from unittest.mock import patch

from services.api import server


class MemoryGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(server.SCHEMA)
        self.addCleanup(self.db.close)
        self.settings = {'enabled': False, 'model': '', 'base_url': '', 'api_key': '', 'embedding_model': ''}
        self.clock = patch.object(server, 'now', return_value='2026-10-12T12:00:00+08:00')
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def card(self, id, **fields):
        values = dict(id=id, user_id='u', title='发布排期', content='上线日期：2026-10-20',
                      project='甲项目', project_id='a', created_at='2026-09-01T00:00:00Z',
                      updated_at='2026-09-01T00:00:00Z', available_at='2026-09-01T00:00:00Z',
                      event_at='2026-09-01T00:00:00Z')
        values.update(fields)
        self.db.execute(f"INSERT INTO memories ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))
        return server.memory_json(self.db.execute('SELECT * FROM memories WHERE id=?', (id,)).fetchone())

    def retrieve(self, query='发布排期上线日期', manual=None, scope=None):
        self.diagnostics = {}
        return server.retrieve(self.db, query, manual or [], self.settings, self.diagnostics, scope, 'u')

    def chat(self, query, **body):
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
            return server.handle_chat(self.db, dict(query=query, **body), 'u')

    def test_default_current_time_excludes_future_and_expired(self):
        self.card('good')
        self.card('future', available_at='2026-12-01T00:00:00Z')
        self.card('expired', valid_until='2026-09-20T00:00:00Z')
        self.assertEqual([i['memory']['id'] for i in self.retrieve()], ['good'])

    def test_historical_query_keeps_then_valid_version(self):
        self.card('old', valid_until='2026-09-20T00:00:00Z')
        self.card('new', available_at='2026-10-01T00:00:00Z', supersedes_id='old')
        self.assertEqual([i['memory']['id'] for i in self.retrieve(scope={'as_of': '2026-09-15T00:00:00Z'})], ['old'])

    def test_account_isolation_applies_to_manual_ids(self):
        self.card('other', user_id='other')
        self.assertEqual(self.retrieve(manual=['other']), [])

    def test_explicit_project_isolation(self):
        self.card('a')
        self.card('b', project='乙项目', project_id='b')
        self.assertEqual([i['memory']['id'] for i in self.retrieve('甲项目发布排期')], ['a'])

    def test_cross_project_manual_card_requests_clarification(self):
        self.card('a')
        self.card('b', project='乙项目', project_id='b')
        answer = self.chat('甲项目发布排期', manual_memory_ids=['b'])
        self.assertEqual(answer['meta']['generation_mode'], 'clarification')
        self.assertFalse(answer['memory_capture']['should_save'])

    def test_explicit_replacement_removes_old_even_if_edited_later(self):
        self.card('old', updated_at='2026-10-11T00:00:00Z')
        self.card('new', content='上线日期：2026-10-30', supersedes_id='old')
        self.assertEqual([i['memory']['id'] for i in self.retrieve()], ['new'])

    def test_manual_old_card_is_not_treated_as_truth(self):
        self.card('old')
        self.card('new', content='上线日期：2026-10-30', supersedes_id='old')
        answer = self.chat('发布排期', manual_memory_ids=['old'])
        self.assertEqual(answer['meta']['generation_mode'], 'clarification')
        self.assertIn('替代', answer['answer']['recommendation'])

    def test_fact_conflict_is_not_deduplicated_by_similarity(self):
        self.card('a')
        self.card('b', content='上线日期：2026-10-30')
        answer = self.chat('发布排期')
        self.assertEqual(answer['meta']['generation_mode'], 'clarification')
        self.assertIn('上线日期', answer['answer']['recommendation'])

    def test_explicit_conflict_with_hidden_memory_does_not_expose_content(self):
        self.card('a')
        self.card('secret', title='隐藏标题不应展示', content='隐藏内容不应展示', visibility='hidden', conflicts_with_id='a')
        answer = self.chat('发布排期', manual_memory_ids=['a'])
        self.assertEqual(answer['meta']['generation_mode'], 'clarification')
        self.assertNotIn('隐藏标题', answer['answer']['recommendation'])
        self.assertEqual(answer['memory_usage']['citations'], [])

    def test_hidden_memory_cannot_replace_visible_fact(self):
        self.card('a')
        self.card('hidden', supersedes_id='a', visibility='hidden')
        selected = self.retrieve(manual=['a'])
        self.assertIn('a', [item['memory']['id'] for item in selected])

    def test_ranking_ignores_citation_frequency_and_edit_time(self):
        memory = self.card('a')
        first = server.memory_ranking_signals(memory, 0.8, server.now())
        memory.update(citations=9999, updated_at='2026-10-12T00:00:00Z', last_used_at=server.now())
        self.assertEqual(first, server.memory_ranking_signals(memory, 0.8, server.now()))
        self.assertAlmostEqual(first['score'], 0.6 * first['relevance'] + 0.25 * first['source_reliability'] + 0.15 * first['temporal_applicability'], places=3)

    def test_method_memory_does_not_decay(self):
        memory = self.card('a', memory_type='方法型', event_at='2000-01-01T00:00:00Z')
        self.assertEqual(server.memory_ranking_signals(memory, 0.8, server.now())['temporal_applicability'], 1)

    def test_same_content_in_different_projects_is_not_deduped(self):
        self.card('a')
        self.card('b', project='乙项目', project_id='b')
        self.assertEqual(len(self.retrieve()), 2)

    def test_ambiguous_projects_request_clarification(self):
        self.card('a')
        self.card('b', project='乙项目', project_id='b')
        self.assertEqual(self.chat('发布排期')['meta']['generation_mode'], 'clarification')

    def test_project_follow_up_and_switch(self):
        self.card('a')
        self.card('b', project='乙项目', project_id='b')
        history = [{'role': 'user', 'text': '甲项目发布排期'}, {'role': 'assistant', 'text': '请确认排期'}]
        result = self.chat('那负责人呢？', conversation_history=history, previous_retrieval_scope={'project_id': 'a'})
        self.assertEqual(result['meta']['retrieval_scope']['project_id'], 'a')
        result = self.chat('换个话题，乙项目发布排期', conversation_history=history, previous_retrieval_scope={'project_id': 'a'})
        self.assertEqual(result['meta']['retrieval_scope']['project_id'], 'b')

    def test_model_payload_clears_old_project_history(self):
        self.card('a')
        self.card('b', project='乙项目', project_id='b')
        captured = []
        def model(settings, payload):
            captured.append(payload)
            return dict(summary='需要确认', recommendation='请确认排期', reasoning_points=[], action_items=[], uncertainties=[])
        with patch.object(server, 'config', return_value={**self.settings, 'enabled': True}), patch.object(server, 'classify_turn_with_model', return_value=None), patch.object(server, 'call_model', side_effect=model), patch.object(server, 'verify_used_memory_ids', return_value=[]):
            server.handle_chat(self.db, {
                'query': '乙项目发布排期',
                'conversation_history': [{'role': 'user', 'text': '甲项目的旧信息'}],
                'previous_retrieval_scope': {'project_id': 'a'},
            }, 'u')
        self.assertEqual(captured[0]['conversation_history'], [])
        self.assertEqual(captured[0]['previous_answer'], '')
        self.assertEqual([m['id'] for m in captured[0]['memories']], ['b'])
        self.assertNotIn('retrieval_weight', captured[0]['memories'][0])

    def test_conflict_prevents_generation(self):
        self.card('a')
        self.card('b', content='上线日期：2026-11-01')
        with patch.object(server, 'call_model') as model:
            self.assertEqual(self.chat('发布排期')['meta']['generation_mode'], 'clarification')
            model.assert_not_called()

    def test_strict_manual_mode_does_not_add_weekly_todos(self):
        self.card('a', title='测试资料', content='测试内容')
        with patch.object(server, 'weekly_todo_items') as weekly:
            self.chat('只根据我选的卡整理本周待办', manual_memory_ids=['a'])
            weekly.assert_not_called()

    def test_hidden_replacement_with_different_fact_needs_confirmation(self):
        self.card('a')
        self.card('b', content='上线日期：2026-11-01', visibility='hidden', supersedes_id='a')
        self.assertEqual(self.chat('发布排期')['meta']['generation_mode'], 'clarification')

    def test_all_manual_cards_are_retained_within_api_limit(self):
        for i in range(6):
            self.card(str(i), title=f'资料{i}', content=f'独立内容{i}')
        self.assertEqual(len(self.retrieve(manual=[str(i) for i in range(6)])), 6)


if __name__ == '__main__':
    unittest.main()
