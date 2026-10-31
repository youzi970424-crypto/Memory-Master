import sqlite3
import unittest
from unittest.mock import patch

from services.api import server


class ScheduleRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(server.SCHEMA)
        self.addCleanup(self.db.close)
        clock = patch.object(server, 'now', return_value='2026-10-19T08:00:00+00:00')
        clock.start()
        self.addCleanup(clock.stop)
        self.settings = dict(enabled=False, model='', base_url='', api_key='', embedding_model='')

    def card(self, content, created='2026-10-09T08:00:00Z', updated='2026-10-19T08:00:00Z'):
        self.db.execute("INSERT INTO memories (id,user_id,title,content,created_at,updated_at) VALUES ('card','u','办公安排',?,?,?)", (content, created, updated))

    def chat(self, **extra):
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
            return server.handle_chat(self.db, dict(query='本周三 周四 周五去做什么', **extra), 'u')

    def test_weekend_checklist_keeps_all_items_and_excludes_heading(self):
        tasks = ['查看平台产品文档', '几句话概述平台内容', '设计平台封面内容',
                 '上线记忆大师产品', '调整产品详情页及下载地址', '描述并配图说明如何使用产品',
                 'skill下载并说明产品', '大概描述下过往经历', '联系方式记得写',
                 '首屏考虑增加鹿的形象']
        self.card('本周末待办\n' + '\n'.join(tasks), '2026-10-19T06:00:00Z')
        self.db.execute("UPDATE memories SET title='本周末待办', folder_id='u-folder-daily-todos'")
        row = self.db.execute('SELECT * FROM memories').fetchone()
        self.assertEqual(server.extract_todo_items(row), tasks)
        self.assertEqual(server.todo_date_label(row, tasks[0]), '10.24—10.25')
        for query in ('本周待办', '本月待办', '本周待办按条列出'):
            with self.subTest(query=query), patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
                result = server.handle_chat(self.db, {'query': query}, 'u')
            for task in tasks:
                self.assertIn(task, result['answer']['recommendation'])
            self.assertNotIn('| 本周末待办 |', result['answer']['recommendation'])

    def test_dot_dates_remain_intact_and_filter_across_month_boundary(self):
        with patch.object(server, 'now', return_value='2026-10-28T08:00:00Z'):
            for identifier, title, content in [
                ('sunday', '11.1线下会议讨论待办', '11.1日需要讨论线下会议内容'),
                ('future', '11月6日出门旅行', '11.6日需要出门旅行'),
            ]:
                self.db.execute('INSERT INTO memories (id,user_id,title,content,created_at,updated_at,folder_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                (identifier, 'u', title, content, '2026-10-28T03:00:00Z', '2026-10-28T03:00:00Z', 'u-folder-daily-todos'))
            for row in self.db.execute('SELECT * FROM memories'):
                self.assertEqual(server.extract_todo_items(row), [row['content']])
            for query, expected in [('本周待办', ['sunday']), ('本月待办', [])]:
                with self.subTest(query=query), patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
                    result = server.handle_chat(self.db, {'query': query}, 'u')
                self.assertEqual([item['memory_id'] for item in result['memory_usage']['weekly_todos']], expected)
                self.assertNotIn('旅行', result['answer']['recommendation'])
                if expected:
                    self.assertIn('| 11.1 | 11.1日需要讨论线下会议内容 |', result['answer']['recommendation'])

    def test_checklist_is_not_truncated_at_twelve_and_skips_completed_tasks(self):
        tasks = [f'查看第{index}份评审材料' for index in range(15)]
        self.card('本周待办\n' + '\n'.join(f'{index + 1}. {task}' for index, task in enumerate(tasks))
                  + '\n已完成归档\n取消旧方案\n背景：产品定位说明', '2026-10-19T06:00:00Z')
        self.db.execute("UPDATE memories SET title='本周待办'")
        self.assertEqual(server.extract_todo_items(self.db.execute('SELECT * FROM memories').fetchone()), tasks)
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
            result = server.handle_chat(self.db, {'query': '本周待办'}, 'u')
        self.assertEqual(len(result['memory_usage']['weekly_todos']), 15)
        for task in tasks:
            self.assertIn(task, result['answer']['recommendation'])

    def test_pending_design_task_without_date_is_recalled_and_labeled(self):
        content = '需要完成个人介绍页及记忆大师和会议总结产品的产品介绍页的设计，并将记忆大师产品推送出去'
        self.card(content, '2026-10-20T06:31:35Z')
        self.db.execute("UPDATE memories SET title='完成产品介绍页设计与记忆大师推送', created_at='2026-10-19T06:31:35Z', updated_at='2026-10-19T06:31:35Z', event_at='2026-10-19T06:31:35Z'")
        query = '本周待完成事项，帮我按条列出来'
        for history in ([], [{'role': 'user', 'text': '介绍产品'}, {'role': 'assistant', 'text': '这是一个知识工具'}]):
            with self.subTest(history=history), patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value={'turn_type': 'format_revision', 'confidence': 0.9}):
                result = server.handle_chat(self.db, dict(query=query, conversation_history=history), 'u')
                self.assertIn(content, result['answer']['recommendation'])
                self.assertIn('未注明截止日期', result['answer']['recommendation'])
                self.assertNotIn('待确认', result['answer']['recommendation'])
                self.assertIn('card', result['memory_usage']['selected_memory_ids'])
        self.assertEqual(server.todo_query_period(query), '本周')
        self.assertTrue(server.is_todo_format_query(query))

    def test_weekly_query_includes_relative_dated_and_undated_tasks(self):
        cards = [
            ('relative', '完成记忆大师设计工作', '本周需要完成记忆大师设计工作'),
            ('dated', '原力平台模型广场内容提测', '10月22日完成原力平台模型广场内容提测'),
            ('undated', '完成产品介绍页设计与记忆大师推送', '需要完成个人介绍页及记忆大师和会议总结产品的产品介绍页的设计，并将记忆大师产品推送出去'),
            ('old', '旧设计任务', '本周需要完成旧设计工作'),
            ('future', '下月提测', '11月22日完成模型广场内容提测'),
        ]
        for identifier, title, content in cards:
            created = '2026-10-09T08:00:00Z' if identifier == 'old' else '2026-10-19T08:00:00Z'
            self.db.execute('INSERT INTO memories (id,user_id,title,content,created_at,updated_at,event_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (identifier, 'u', title, content, created, created, created))
        for history in ([], [{'role': 'user', 'text': '本周待办事项'}, {'role': 'assistant', 'text': cards[2][2]}]):
            with self.subTest(history=history), patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
                result = server.handle_chat(self.db, {'query': '本周待办事项', 'conversation_history': history}, 'u')
                self.assertEqual({item['memory_id'] for item in result['memory_usage']['weekly_todos']}, {'relative', 'dated', 'undated'})
                for _, _, content in cards[:3]:
                    self.assertIn(content, result['answer']['recommendation'])
                self.assertIn('未注明截止日期', result['answer']['recommendation'])
                self.assertNotIn('待确认', result['answer']['recommendation'])
        self.settings['enabled'] = True
        answer = dict(summary='', recommendation='待办清单', reasoning_points=[], action_items=[], uncertainties=[], _mode='model')
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None), patch.object(server, 'call_model', return_value=answer) as model:
            server.handle_chat(self.db, {'query': '本周待办事项'}, 'u')
        self.assertEqual({item['memory_id'] for item in model.call_args.args[1]['weekly_todos']}, {'relative', 'dated', 'undated'})

    def test_undated_tasks_use_local_creation_date_not_edit_time(self):
        cases = [
            ('2026-10-18T15:59:59Z', False),
            ('2026-10-18T16:00:00Z', True),
            ('2026-10-25T15:59:59Z', True),
            ('2026-10-25T16:00:00Z', False),
        ]
        dates = server.schedule_dates('本周待办', server.local_datetime(server.now()))
        scope = server.retrieval_scope('本周待办', {'include_undated_todos': True, 'as_of': '2026-11-02T08:00:00Z'})
        for created, expected in cases:
            with self.subTest(created=created):
                self.db.execute('DELETE FROM memories')
                self.card('需要完成产品介绍页设计', created)
                row = self.db.execute('SELECT * FROM memories').fetchone()
                self.assertEqual(server.row_matches_retrieval_scope(row, scope), expected)
                self.assertEqual(server.row_matches_retrieval_scope(row, scope, manual=True), expected)
                self.assertEqual(server.is_current_week_memory(row), expected)
                self.assertEqual(bool(server.weekly_todo_items(self.db, scope, 'u')), expected)
                self.assertEqual(bool(server.weekly_todo_items(self.db, {'as_of': scope['as_of']}, 'u')), expected)
                # A creation date must not become a scheduled appointment.
                self.assertFalse(server.memory_matches_schedule(row, dates))

    def test_old_undated_task_is_excluded_even_with_manual_selection_and_history(self):
        self.card('需要完成产品介绍页设计', '2026-10-18T06:31:35Z')
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
            result = server.handle_chat(self.db, {
                'query': '本周待办事项', 'manual_memory_ids': ['card'],
                'conversation_history': [{'role': 'assistant', 'text': '需要完成产品介绍页设计'}],
            }, 'u')
        self.assertEqual(result['memory_usage']['weekly_todos'], [])
        self.assertEqual(result['memory_usage']['selected_memory_ids'], [])
        self.assertNotIn('产品介绍页设计', result['answer']['recommendation'])

    def test_action_semantics_and_todo_classification_reach_answers(self):
        cases = [
            ('优化记忆大师的Harness功能', None, '[]'),
            ('修复召回遗漏问题', None, '[]'),
            ('整理评审材料', None, '[]'),
            ('本周需要优化记忆大师的Harness功能', None, '[]'),
            ('10月22日修复召回遗漏问题', None, '[]'),
            ('Harness功能验收', 'u-folder-daily-todos', '[]'),
            ('Harness功能验收', None, '["待办"]'),
            ('Harness功能验收', None, '["日常待办"]'),
        ]
        for content, folder, tags in cases:
            with self.subTest(content=content, folder=folder, tags=tags):
                self.db.execute('DELETE FROM memories')
                self.card(content, '2026-10-19T06:00:00Z')
                self.db.execute('UPDATE memories SET title=?, folder_id=?, tags_json=?', (content, folder, tags))
                row = self.db.execute('SELECT * FROM memories').fetchone()
                self.assertEqual(server.extract_todo_items(row), [content])
                self.assertEqual(server.extract_todo_items(server.memory_json(row)), [content])
                self.assertTrue(server.is_current_week_memory(row))
                for query in ('本周待办事项', '本周待办按条列出'):
                    with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
                        result = server.handle_chat(self.db, {'query': query}, 'u')
                    self.assertIn('card', result['memory_usage']['selected_memory_ids'])
                    self.assertIn(content, result['answer']['recommendation'])
                    self.assertEqual([item['memory_id'] for item in result['memory_usage']['weekly_todos']], ['card'])

    def test_completed_cancelled_and_knowledge_text_are_not_pending_tasks(self):
        cases = [
            ('Harness优化原理：通过约束提升执行质量', None),
            ('优化建议：可以通过缓存提高速度', None),
            ('已完成优化记忆大师的Harness功能', 'u-folder-daily-todos'),
            ('取消优化记忆大师的Harness功能', 'u-folder-daily-todos'),
            ('不需要优化记忆大师的Harness功能', 'u-folder-daily-todos'),
            ('待办：已完成Harness功能验收', 'u-folder-daily-todos'),
        ]
        for content, folder in cases:
            with self.subTest(content=content):
                self.db.execute('DELETE FROM memories')
                self.card(content, '2026-10-19T06:00:00Z')
                self.db.execute('UPDATE memories SET title=?, folder_id=?', (content, folder))
                row = self.db.execute('SELECT * FROM memories').fetchone()
                self.assertEqual(server.extract_todo_items(row), [])
                self.assertEqual(server.weekly_todo_items(self.db, user_id='u'), [])

    def test_todo_classification_does_not_override_dates_or_visibility(self):
        for content, created, visibility in [
            ('Harness功能验收', '2026-10-09T08:00:00Z', 'visible'),
            ('11月22日Harness功能验收', '2026-10-19T06:00:00Z', 'visible'),
            ('Harness功能验收', '2026-10-19T06:00:00Z', 'hidden'),
        ]:
            with self.subTest(content=content, created=created, visibility=visibility):
                self.db.execute('DELETE FROM memories')
                self.card(content, created)
                self.db.execute("UPDATE memories SET title=?, folder_id='u-folder-daily-todos', visibility=?", (content, visibility))
                self.assertEqual(server.weekly_todo_items(self.db, user_id='u'), [])

    def test_monthly_query_recalls_tasks_across_weeks_and_overrides_history(self):
        cards = [
            ('early', '优化记忆大师的Harness功能', '2026-10-02T08:00:00Z', None),
            ('classified', 'Harness功能验收', '2026-10-19T06:00:00Z', 'u-folder-daily-todos'),
            ('relative', '本月需要整理评审材料', '2026-10-02T08:00:00Z', None),
            ('dated', '10月22日完成模型广场提测', '2026-09-20T08:00:00Z', None),
            ('old', '修复旧版问题', '2026-09-20T08:00:00Z', None),
            ('future', '11月2日完成下一版提测', '2026-10-02T08:00:00Z', None),
            ('next_month', '下月需要整理评审材料', '2026-10-02T08:00:00Z', None),
        ]
        for identifier, content, created, folder in cards:
            self.db.execute('INSERT INTO memories (id,user_id,title,content,created_at,updated_at,folder_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (identifier, 'u', content, content, created, created, folder))
        expected = {'early', 'classified', 'relative', 'dated'}
        for query in ('本月待办事项', '这个月待办按条列出', '当月待完成任务'):
            with self.subTest(query=query), patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value={'turn_type': 'format_revision', 'confidence': 0.9}):
                result = server.handle_chat(self.db, {
                    'query': query,
                    'conversation_history': [{'role': 'user', 'text': '本周待办事项'}, {'role': 'assistant', 'text': '未找到本周待办'}],
                    'previous_retrieval_scope': {'schedule_dates': ['2026-10-19']},
                }, 'u')
            self.assertEqual({item['memory_id'] for item in result['memory_usage']['weekly_todos']}, expected)
            for _, content, _, _ in cards[:4]:
                self.assertIn(content, result['answer']['recommendation'])
            self.assertNotIn('本周', result['answer']['recommendation'])
        self.settings['enabled'] = True
        answer = dict(summary='', recommendation='本月待办', reasoning_points=[], action_items=[], uncertainties=[], _mode='model')
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None), patch.object(server, 'call_model', return_value=answer) as model:
            server.handle_chat(self.db, {'query': '本月待办事项'}, 'u')
        payload = model.call_args.args[1]
        self.assertEqual({item['memory_id'] for item in payload['weekly_todos']}, expected)
        self.assertEqual(payload['retrieval_scope']['schedule_dates'][0], '2026-10-01')
        self.assertEqual(payload['retrieval_scope']['schedule_dates'][-1], '2026-10-31')
        self.assertIn('本月', payload['special_request'])
        self.assertNotIn('这是本周待办查询', payload['special_request'])

    def test_classified_task_background_is_not_split_into_extra_todos(self):
        task = '为小营家做一张产品功能宣传海报'
        self.card(task + '\n小营家是AI装修生意助理，可以长期协助处理日常工作。\n主标题：现场施工你把控，线上杂事交给小营家', '2026-10-19T06:00:00Z')
        self.db.execute("UPDATE memories SET title=?, folder_id='u-folder-daily-todos'", (task,))
        row = self.db.execute('SELECT * FROM memories').fetchone()
        self.assertEqual(server.extract_todo_items(row), [task])
        with patch.object(server, 'config', return_value=self.settings), patch.object(server, 'classify_turn_with_model', return_value=None):
            result = server.handle_chat(self.db, {'query': '本月待办事项'}, 'u')
        self.assertEqual(len(result['memory_usage']['weekly_todos']), 1)
        self.assertNotIn('主标题', result['answer']['recommendation'])

    def test_month_dates_cover_calendar_boundaries_and_creation_timezone(self):
        for current, first, last, count in [
            ('2026-10-28T08:00:00Z', '2026-10-01', '2026-10-31', 31),
            ('2028-02-15T08:00:00Z', '2028-02-01', '2028-02-29', 29),
            ('2026-12-15T08:00:00Z', '2026-12-01', '2026-12-31', 31),
        ]:
            with self.subTest(current=current):
                dates = server.schedule_dates('本月待办事项', server.local_datetime(current))
                self.assertEqual(dates, [(server.local_datetime(first) + server.timedelta(days=i)).date().isoformat() for i in range(count)])
                self.assertEqual(dates[-1], last)
        scope = server.retrieval_scope('本月待办事项', {'include_undated_todos': True, 'as_of': '2026-11-02T08:00:00Z'})
        for created, expected in [('2026-09-30T15:59:59Z', False), ('2026-09-30T16:00:00Z', True), ('2026-10-31T15:59:59Z', True), ('2026-10-31T16:00:00Z', False)]:
            with self.subTest(created=created):
                self.db.execute('DELETE FROM memories')
                self.card('优化记忆大师的Harness功能', created)
                row = self.db.execute('SELECT * FROM memories').fetchone()
                self.assertEqual(server.row_matches_retrieval_scope(row, scope), expected)

    def test_todo_table_shows_time_and_item_without_extra_actions(self):
        self.card('本周需要完成设计工作；10月22日完成内容提测；优化Harness功能', '2026-10-19T06:00:00Z')
        self.db.execute("UPDATE memories SET title='设计任务', updated_at='2026-10-28T06:00:00Z'")
        row = self.db.execute('SELECT * FROM memories').fetchone()
        self.assertEqual(server.todo_date_label(row, '本周需要完成设计工作'), '10.19—10.25')
        self.assertEqual(server.todo_date_label(row, '10月22日完成内容提测'), '10.22')
        self.assertEqual(server.todo_date_label(row, '优化Harness功能'), '10.19 创建（未注明截止日期）')
        self.assertEqual(server.todo_date_label(row, '周三至周五完成设计工作'), '10.21—10.23')
        result = server.formatted_todo_answer([{'memory': server.memory_json(row)}])
        lines = result['recommendation'].splitlines()
        self.assertEqual(lines[:2], ['| 时间 | 事项 |', '| --- | --- |'])
        self.assertEqual(len(lines[2:]), 3)
        self.assertTrue(all(len(line.split('|')) == 4 for line in lines[2:]))
        self.assertEqual(result['action_items'], [])
        self.assertEqual(result['uncertainties'], [])

    def test_old_relative_schedule_is_not_this_week_even_after_edit(self):
        self.card('周三至周五居家办公，周三9:30-11:30陪父亲复查。')
        result = self.chat()
        self.assertIn('未找到', result['answer']['recommendation'])
        self.assertIn('2026-10-21', result['answer']['recommendation'])
        self.assertEqual(result['meta']['retrieved_count'], 0)
        self.assertEqual(result['memory_usage']['citations'], [])

    def test_manual_and_previous_answer_cannot_restore_old_schedule(self):
        self.card('周三至周五居家办公，周三9:30-11:30陪父亲复查。')
        result = self.chat(manual_memory_ids=['card'], conversation_history=[
            {'role': 'user', 'text': '办公安排'}, {'role': 'assistant', 'text': '本周三至周五居家办公。'}])
        self.assertIn('未找到', result['answer']['recommendation'])
        self.assertEqual(result['meta']['retrieved_count'], 0)

    def test_dates_override_storage_time(self):
        for content, expected in [('2026-10-21安排居家办公。', True), ('2026-10-07安排居家办公。', False)]:
            with self.subTest(content=content):
                self.db.execute('DELETE FROM memories')
                self.card(content)
                row = self.db.execute('SELECT * FROM memories').fetchone()
                self.assertEqual(server.memory_matches_schedule(row, ['2026-10-21']), expected)

    def test_current_relative_and_recurring_schedules(self):
        for content, created in [('本周三居家办公', '2026-10-19T08:00:00Z'), ('下周三居家办公', '2026-10-12T08:00:00Z'), ('每周三10:00团队站会', '2026-10-09T08:00:00Z')]:
            with self.subTest(content=content):
                self.db.execute('DELETE FROM memories')
                self.card(content, created)
                row = self.db.execute('SELECT * FROM memories').fetchone()
                self.assertTrue(server.memory_matches_schedule(row, ['2026-10-21']))

    def test_weekly_todos_exclude_old_weekday_text(self):
        self.card('本周三待办：陪父亲复查。')
        self.assertEqual(server.weekly_todo_items(self.db, user_id='u'), [])

    def test_valid_schedule_reaches_model_with_date_scope(self):
        self.card('2026-10-21居家办公。')
        self.settings['enabled'] = True
        answer = dict(summary='周三居家办公', recommendation='10月21日居家办公；未找到另外两天安排。',
                      reasoning_points=[], action_items=[], uncertainties=[], _mode='model')
        with patch.object(server, 'call_model', return_value=answer) as model:
            self.chat()
        payload = model.call_args.args[1]
        self.assertEqual([item['id'] for item in payload['memories']], ['card'])
        self.assertEqual(payload['retrieval_scope']['schedule_dates'], ['2026-10-21', '2026-10-22', '2026-10-23'])
        self.assertIn('created_at', payload['memories'][0])

    def test_unrequested_weekday_does_not_count_as_evidence(self):
        self.card('本周一居家办公。', created='2026-10-19T08:00:00Z')
        self.assertEqual(self.chat()['meta']['retrieved_count'], 0)

    def test_product_weekly_quota_is_not_a_recurring_schedule(self):
        self.card('产品定位：能独立完成完整工程任务，规划方案、提交PR。\n商业模式：Plus有每周额度，重度工程任务消耗很快。', created='2026-10-19T08:00:00Z')
        result = self.chat()
        self.assertEqual(result['meta']['retrieved_count'], 0)
        self.assertEqual(result['memory_usage']['weekly_todos'], [])
        self.assertEqual(result['memory_usage']['citations'], [])
        self.assertIn('未找到', result['answer']['recommendation'])
        self.assertEqual(server.extract_todo_items(self.db.execute('SELECT * FROM memories').fetchone()), [])

    def test_fast_local_path_obeys_dates(self):
        self.card('周三9:30-11:30要陪父亲复查，10:00团队站会。')
        scope = server.retrieval_scope('本周三上午哪些会议有冲突')
        self.assertEqual(server.local_home_office_conflict_selection(self.db, '本周三上午哪些会议有冲突', scope, 'u'), [])


if __name__ == '__main__':
    unittest.main()
