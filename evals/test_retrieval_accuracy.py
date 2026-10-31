"""Held-out retrieval cases: exact relevant sets, including no-answer queries."""

import sqlite3
import unittest

from services.api import server


class RetrievalAccuracyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(server.SCHEMA)
        self.addCleanup(self.db.close)
        cards = [
            ('export', '订单导出', '固定三字段CSV：订单号、付款金额、订单状态。'),
            ('release', '交付评审', '6月19日销售诉求：固定三字段CSV试点，前提是安全评审。'),
            ('customers', '客户记录', '青禾、南桥、松石提出需求增加。'),
            ('office', '居家办公', '远程办公需要检查网络、视频会议、设备补贴和信息安全。'),
            ('travel', '出差材料', '出差申请需要行程单、交通票据和住宿凭证。'),
            ('reconcile', '财务核对', '财务对账需要核实流水与账单差异。'),
        ]
        for id, title, content in cards:
            self.db.execute(
                "INSERT INTO memories (id, user_id, title, content, created_at, updated_at, available_at) "
                "VALUES (?, 'u', ?, ?, '2026-01-01', '2026-01-01', '2026-01-01')",
                (id, title, content),
            )

    def test_unrelated_queries_do_not_recall_business_examples(self):
        for query in ['星穹望远镜需要哪些信息？', '这周五能看到流星雨吗？',
                      '三个客户的宠物叫什么名字？', '烘焙前需要提前准备什么？']:
            with self.subTest(query=query):
                selected = server.retrieve(self.db, query, [], {}, user_id='u')
                self.assertEqual([item['memory']['id'] for item in selected], [])

    def test_relevant_query_has_no_extra_cards(self):
        selected = server.retrieve(self.db, '出差申请需要哪些信息？', [], {}, user_id='u')
        self.assertEqual([item['memory']['id'] for item in selected], ['travel'])

    def test_reconciliation_does_not_imply_order_export(self):
        selected = server.retrieve(self.db, '财务对账如何核实账单差异？', [], {}, user_id='u')
        self.assertEqual([item['memory']['id'] for item in selected], ['reconcile'])

    def test_synonym_still_recalls_relevant_memory(self):
        selected = server.retrieve(self.db, '在家办公', [], {}, user_id='u')
        self.assertEqual([item['memory']['id'] for item in selected], ['office'])

    def test_short_keyword_is_not_rejected_by_minimum_token_count(self):
        selected = server.retrieve(self.db, '出差', [], {}, user_id='u')
        self.assertEqual([item['memory']['id'] for item in selected], ['travel'])

    def test_manual_card_survives_unrelated_query(self):
        selected = server.retrieve(self.db, '星穹望远镜需要哪些信息？', ['travel'], {}, user_id='u')
        self.assertEqual([item['memory']['id'] for item in selected], ['travel'])


if __name__ == '__main__':
    unittest.main()
