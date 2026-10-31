import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from services.api import server


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(server.SCHEMA)
        self.addCleanup(self.db.close)
        self.settings = {'embedding_model': 'test'}
        self.diagnostics = {}

    def card(self, id, **fields):
        values = dict(id=id, user_id='u', title='交接档案', content='历史结论集中留存',
                      project='甲项目', project_id='a', created_at='2026-01-01T00:00:00Z',
                      updated_at='2026-01-01T00:00:00Z', available_at='2026-01-01T00:00:00Z')
        values.update(fields)
        self.db.execute(f"INSERT INTO memories ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))

    def retrieve(self, query='减少反复解释', manual=None):
        return server.retrieve(self.db, query, manual or [], self.settings, self.diagnostics,
                               {'project_id': 'a', 'as_of': '2026-10-12T00:00:00Z'}, 'u')

    def embeddings(self):
        return patch.object(server, 'call_embeddings', side_effect=lambda _, inputs: [[1.0, 0.0] for _ in inputs])

    def test_semantic_only_candidate_survives_zero_keyword_overlap(self):
        self.card('semantic')
        with self.embeddings():
            selected = self.retrieve()
        self.assertEqual([item['memory']['id'] for item in selected], ['semantic'])
        self.assertEqual(selected[0]['sparse_score'], 0)
        self.assertEqual(selected[0]['content_score'], 0)
        self.assertEqual(self.diagnostics['fusion_mode'], 'rrf')

    def test_semantic_match_does_not_bypass_scope_time_or_account(self):
        self.card('good')
        self.card('other-project', project='乙项目', project_id='b')
        self.card('other-user', user_id='other')
        self.card('expired', valid_until='2026-02-01T00:00:00Z')
        self.card('future', available_at='2027-01-01T00:00:00Z')
        with self.embeddings() as embed:
            self.assertEqual([item['memory']['id'] for item in self.retrieve()], ['good'])
        self.assertEqual(len(embed.call_args.args[1]), 2)

    def test_semantic_match_does_not_bypass_fact_type(self):
        self.card('no-revenue')
        with self.embeddings():
            self.assertEqual(self.retrieve('去年收入金额是多少'), [])

    def test_low_similarity_without_keywords_is_rejected(self):
        self.card('noise')
        with patch.object(server, 'call_embeddings', return_value=[[1.0, 0.0], [0.0, 1.0]]):
            self.assertEqual(self.retrieve(), [])

    def test_rrf_rewards_agreement_without_score_scale_dependence(self):
        a, b, c = [dict(memory={'id': id}) for id in 'abc']
        scores = server.fuse_retrieval_candidates([[a, b], [c, b]])
        self.assertGreater(scores['b'], scores['a'])
        self.assertEqual(scores['a'], scores['c'])

    def test_embedding_failure_preserves_keyword_fallback(self):
        self.card('keyword')
        with patch.object(server, 'call_embeddings', side_effect=ValueError('offline')):
            selected = self.retrieve('交接档案历史结论')
        self.assertEqual([item['memory']['id'] for item in selected], ['keyword'])
        self.assertEqual(self.diagnostics['fusion_mode'], 'keyword')

    def test_reranker_orders_automatic_cards_but_preserves_manual(self):
        self.card('manual', content='用户选定资料')
        self.card('a', content='历史结论集中留存一')
        self.card('b', content='历史结论集中留存二')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server, 'call_reranker', return_value=[0.1, 0.9]) as rerank:
            selected = self.retrieve(manual=['manual'])
        self.assertEqual([item['memory']['id'] for item in selected], ['manual', 'b', 'a'])
        self.assertEqual(len(rerank.call_args.args[2]), 2)
        self.assertTrue(self.diagnostics['reranker_used'])

    def test_reranker_failure_preserves_local_order(self):
        self.card('a')
        with self.embeddings():
            baseline = self.retrieve()
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server, 'call_reranker', side_effect=ValueError('offline')):
            selected = self.retrieve()
        self.assertEqual(selected, baseline)
        self.assertFalse(self.diagnostics['reranker_used'])

    def test_reranker_rejects_low_relevance_despite_source_and_semantic_scores(self):
        self.card('noise', quality_weight=1.0, event_at='2026-10-12')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server, 'call_reranker', return_value=[0.01]):
            self.assertEqual(self.retrieve(), [])
        self.assertTrue(self.diagnostics['reranker_used'])
        self.assertEqual(self.diagnostics['reranker_rejected_count'], 1)

    def test_reranker_filters_noise_without_dropping_manual_card(self):
        self.card('manual', content='手动选择的其他资料')
        self.card('noise', content='主题无关的资料')
        self.card('useful', content='有用的资料')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')

        def scores(_settings, _query, documents):
            return [0.01 if '主题无关' in document else 0.9 for document in documents]

        with self.embeddings(), patch.object(server, 'call_reranker', side_effect=scores):
            selected = self.retrieve(manual=['manual'])
        self.assertEqual([item['memory']['id'] for item in selected], ['manual', 'useful'])
        self.assertEqual(self.diagnostics['reranker_rejected_count'], 1)

    def test_reranker_threshold_is_inclusive_and_adjustable(self):
        self.card('candidate')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server, 'call_reranker', return_value=[0.3]):
            with patch.object(server, 'RERANK_RELEVANCE_THRESHOLD', 0.3):
                self.assertEqual(len(self.retrieve()), 1)
            with patch.object(server, 'RERANK_RELEVANCE_THRESHOLD', 0.4):
                self.assertEqual(self.retrieve(), [])

    def test_chat_does_not_send_rejected_memory_to_model(self):
        self.card('noise')
        self.settings.update(enabled=True, model='test', reranker_url='http://reranker.test/rerank', reranker_model='test')
        answer = {'summary': '请补充交接背景。', 'recommendation': '请补充交接背景。',
                  'reasoning_points': [], 'action_items': [], 'uncertainties': [], '_mode': 'model'}
        with self.embeddings(), patch.object(server, 'config', return_value=self.settings), \
                patch.object(server, 'classify_turn_with_model', return_value=None), \
                patch.object(server, 'call_reranker', return_value=[0.01]), \
                patch.object(server, 'call_model', return_value=answer) as model:
            response = server.handle_chat(self.db, {'query': '减少反复解释'}, 'u')
        self.assertEqual(model.call_args.args[1]['memories'], [])
        self.assertEqual(response['memory_usage']['recalled_memories'], [])
        self.assertEqual(response['meta']['reranker_rejected_count'], 1)

    def test_manual_only_does_not_call_reranker(self):
        self.card('manual')
        self.card('auto', content='其他结论')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server, 'call_reranker') as rerank:
            selected = self.retrieve('只根据选中的记忆卡回答', ['manual'])
        self.assertEqual([item['memory']['id'] for item in selected], ['manual'])
        rerank.assert_not_called()

    def test_selected_comparison_excludes_unselected_competitors(self):
        self.card('flow', title='FlowDesk 产品调研', content='每人39元，仅公有云。')
        self.card('constraint', title='采购约束', content='25人，必须私有化。')
        self.card('other', title='其他竞品调研', content='产品比较，私有化方案。')
        with self.embeddings():
            selected = self.retrieve('比较竞品并推荐方案，说明成本信息是否完整。', ['flow', 'constraint'])
        self.assertEqual({item['memory']['id'] for item in selected}, {'flow', 'constraint'})

    def test_comparison_can_explicitly_expand_and_negative_request_cannot(self):
        self.card('manual')
        self.card('other', content='其他产品资料')
        with self.embeddings():
            selected = self.retrieve('比较竞品，请补充其他竞品资料', ['manual'])
            limited = self.retrieve('比较竞品，不要补充其他竞品资料', ['manual'])
        self.assertEqual({item['memory']['id'] for item in selected}, {'manual', 'other'})
        self.assertEqual([item['memory']['id'] for item in limited], ['manual'])

    def test_selected_comparison_model_payload_excludes_old_competitors(self):
        self.card('flow', content='FlowDesk 每人39元，仅公有云。')
        self.card('constraint', content='25人，必须私有化。')
        self.card('other', content='OtherProduct 竞品分析。')
        self.settings.update(enabled=True, model='test', base_url='', api_key='')
        answer = dict(summary='FlowDesk不满足约束。', recommendation='缺少其他比较对象。',
                      reasoning_points=[], action_items=[], uncertainties=[], _mode='model')
        with self.embeddings(), patch.object(server, 'config', return_value=self.settings), \
                patch.object(server, 'classify_turn_with_model', return_value=None), \
                patch.object(server, 'call_model', return_value=answer) as model:
            result = server.handle_chat(self.db, {
                'query': '比较竞品并推荐方案，说明成本信息是否完整。',
                'manual_memory_ids': ['flow', 'constraint'],
                'conversation_history': [{'role': 'user', 'text': '比较产品'},
                                         {'role': 'assistant', 'text': 'OtherProduct更好。'}],
            }, 'u')
        payload = model.call_args.args[1]
        self.assertEqual({item['id'] for item in payload['memories']}, {'flow', 'constraint'})
        self.assertNotIn('OtherProduct', json.dumps(payload))
        self.assertIn('约束卡不是竞品', payload['special_request'])
        self.assertEqual(result['memory_usage']['auto_recalled_memory_ids'], [])

    def test_unselected_comparison_and_noncomparison_keep_automatic_recall(self):
        self.card('auto')
        with self.embeddings():
            self.assertEqual(len(self.retrieve('比较竞品')), 1)
        self.assertFalse(server.strict_manual_only_query('梳理项目背景', ['auto']))

    def test_reranker_does_not_restore_superseded_fact(self):
        self.card('old', content='上线日期：2026-10-20')
        self.card('new', content='上线日期：2026-10-30', supersedes_id='old')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server, 'call_reranker', return_value=[1.0, 0.1]):
            selected = self.retrieve()
        self.assertEqual([item['memory']['id'] for item in selected], ['new'])

    def test_reranker_timeout_uses_local_results_without_provider_details(self):
        self.card('a')
        self.settings.update(reranker_url='http://reranker.test/rerank', reranker_model='test')
        with self.embeddings(), patch.object(server.urllib.request, 'urlopen', side_effect=TimeoutError('sensitive provider details')):
            selected = self.retrieve()
        self.assertEqual([item['memory']['id'] for item in selected], ['a'])
        self.assertFalse(self.diagnostics['reranker_used'])
        self.assertNotIn('sensitive', self.diagnostics['reranker_error'])

    def test_reranker_protocol_reassembles_indices_and_validates_results(self):
        settings = dict(reranker_url='http://reranker.test/rerank', reranker_model='test')
        valid = [{'index': 1, 'relevance_score': 0.9}, {'index': 0, 'relevance_score': 0.2}]
        with patch.object(server.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps({'results': valid}).encode())) as request:
            self.assertEqual(server.call_reranker(settings, '问题', ['甲', '乙']), [0.2, 0.9])
            self.assertEqual(json.loads(request.call_args.args[0].data)['documents'], ['甲', '乙'])
        for results in [valid[:1], [valid[0], valid[0]], [{'index': 0, 'relevance_score': float('nan')}, valid[0]]]:
            with self.subTest(results=results), patch.object(server.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps({'results': results}).encode())):
                with self.assertRaises(ValueError):
                    server.call_reranker(settings, '问题', ['甲', '乙'])

    def test_reranker_key_is_masked(self):
        with patch.dict(server.os.environ, {'MEMORY_MASTER_RERANKER_API_KEY': 'secret'}):
            self.assertEqual(server.public_config(self.db, 'u')['reranker_api_key'], '********')


if __name__ == '__main__':
    unittest.main()
