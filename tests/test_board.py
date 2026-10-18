import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app


class BoardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_instructions = app.instructions
        self.original_instructions_file = app.INSTRUCTIONS_FILE
        self.original_shelters = app.shelters
        app.instructions = []
        app.shelters = [{'id': 1, 'name': '中央避難所'}]
        app.INSTRUCTIONS_FILE = os.path.join(self.temp_dir.name, 'instructions.json')
        app.save_instructions([])
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True
        self.client.get('/board')
        with self.client.session_transaction() as session:
            self.csrf_token = session['csrf_token']

    def tearDown(self):
        app.instructions = self.original_instructions
        app.INSTRUCTIONS_FILE = self.original_instructions_file
        app.shelters = self.original_shelters
        self.temp_dir.cleanup()

    def add_instruction(self, target, content='避難してください', shelter='', custom_target=''):
        return self.client.post('/board', data={
            'csrf_token': self.csrf_token,
            'target': target,
            'custom_target': custom_target,
            'content': content,
            'urgency': '通常',
            'shelter': shelter,
        })

    def change_state(self, instruction_id, action, expected_status):
        return self.client.post(
            f'/board/{instruction_id}/status',
            data={
                'csrf_token': self.csrf_token,
                'action': action,
                'expected_status': expected_status,
            },
        )

    def read_saved_instructions(self):
        with open(app.INSTRUCTIONS_FILE, encoding='utf-8') as saved_file:
            return json.load(saved_file)

    def test_board_requires_authentication_for_view_and_post(self):
        client = app.app.test_client()
        response = client.get('/board')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        response = client.post('/board', data={'content': 'unauthorized'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(app.instructions, [])

    def test_valid_instruction_is_saved_as_active_and_escaped(self):
        content = '<script>alert(1)</script>'
        response = self.add_instruction('住民', content, '中央避難所')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(app.instructions[0]['status'], '対応中')
        self.assertTrue(app.instructions[0]['created_at'])
        self.assertTrue(app.instructions[0]['updated_at'])
        self.assertEqual(self.read_saved_instructions()[0]['status'], '対応中')
        page = self.client.get('/board').get_data(as_text=True)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('登録済みの指示（対応中）', page)

    def test_invalid_target_and_content_are_not_saved(self):
        for target, content, custom_target in (
            ('', '内容', ''), ('不正な宛先', '内容', ''),
            ('__custom__', '内容', '  '), ('__custom__', '内容', '宛先' * 41),
            ('住民', '  ', ''), ('住民', '長文' * 1001, ''),
        ):
            with self.subTest(target=target, content_length=len(content)):
                response = self.add_instruction(target, content, custom_target=custom_target)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(app.instructions, [])
                self.assertEqual(self.read_saved_instructions(), [])

    def test_custom_target_is_saved_and_treated_as_staff(self):
        response = self.add_instruction('__custom__', '現地確認をお願いします', custom_target='青森地域振興部')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(app.instructions[0]['target'], '青森地域振興部')
        self.assertEqual(app.instructions[0]['audience'], 'staff')
        self.assertEqual(app.instructions[0]['status'], '対応中')
        page = self.client.get('/board').get_data(as_text=True)
        custom_card = page.split('青森地域振興部向け', 1)[1].split('</li>', 1)[0]
        self.assertIn('name="action" value="complete"', custom_card)
        self.assertNotIn('name="action" value="release"', custom_card)

    def test_active_cards_only_offer_audience_specific_action(self):
        self.add_instruction('住民', '住民向け指示')
        self.add_instruction('防災課', '職員向け指示')
        page = self.client.get('/board').get_data(as_text=True)
        self.assertEqual(page.count('name="action" value="release"'), 1)
        self.assertEqual(page.count('name="action" value="complete"'), 1)
        self.assertNotIn('name="action" value="undo_release"', page)
        self.assertNotIn('name="action" value="undo_complete"', page)
        resident_card = page.split('住民向け指示', 1)[1].split('</li>', 1)[0]
        staff_card = page.split('職員向け指示', 1)[1].split('</li>', 1)[0]
        self.assertIn('解除', resident_card)
        self.assertNotIn('完了', resident_card)
        self.assertIn('完了', staff_card)
        self.assertNotIn('解除', staff_card)

    def test_server_rejects_cross_audience_actions(self):
        self.add_instruction('住民', '住民向け')
        self.add_instruction('防災課', '職員向け')
        self.assertEqual(self.change_state(1, 'complete', '対応中').status_code, 400)
        self.assertEqual(self.change_state(2, 'release', '対応中').status_code, 400)
        self.assertEqual([item['status'] for item in app.instructions], ['対応中', '対応中'])
        self.assertEqual([item['status'] for item in self.read_saved_instructions()], ['対応中', '対応中'])

    def test_release_complete_and_undo_persist_and_move_between_sections(self):
        self.add_instruction('住民', '住民向け')
        self.add_instruction('防災課', '職員向け')

        response = self.change_state(1, 'release', '対応中')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('#resolved-instructions'))
        response = self.change_state(2, 'complete', '対応中')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('#resolved-instructions'))
        self.assertEqual({item['status'] for item in self.read_saved_instructions()}, {'解除', '完了'})
        resolved_page = self.client.get('/board').get_data(as_text=True)
        self.assertIn('解除取り消し', resolved_page)
        self.assertIn('完了取り消し', resolved_page)
        self.assertIn('class="instruction-card is-resolved"', resolved_page)
        self.assertNotIn('name="action" value="release"', resolved_page)
        self.assertNotIn('name="action" value="complete"', resolved_page)

        self.assertEqual(self.change_state(1, 'undo_release', '解除').status_code, 302)
        self.assertEqual(self.change_state(2, 'undo_complete', '完了').status_code, 302)
        self.assertEqual([item['status'] for item in self.read_saved_instructions()], ['対応中', '対応中'])
        active_page = self.client.get('/board').get_data(as_text=True)
        self.assertIn('name="action" value="release"', active_page)
        self.assertIn('name="action" value="complete"', active_page)
        self.assertNotIn('解除取り消し', active_page)
        self.assertNotIn('完了取り消し', active_page)

    def test_stale_expected_state_does_not_change_record(self):
        self.add_instruction('住民', '住民向け')
        self.assertEqual(self.change_state(1, 'release', '対応中').status_code, 302)
        saved_before = self.read_saved_instructions()
        response = self.change_state(1, 'undo_release', '対応中')
        self.assertEqual(response.status_code, 409)
        self.assertIn('画面を再読み込みしてください', response.get_data(as_text=True))
        self.assertEqual(self.read_saved_instructions(), saved_before)

    def test_resolved_cards_only_offer_the_matching_undo(self):
        app.instructions = [
            {'id': 1, 'target': '住民', 'content': '解除済み', 'status': '解除'},
            {'id': 2, 'target': '防災課', 'content': '完了済み', 'status': '完了'},
        ]
        page = self.client.get('/board').get_data(as_text=True)
        self.assertEqual(page.count('name="action" value="undo_release"'), 1)
        self.assertEqual(page.count('name="action" value="undo_complete"'), 1)
        self.assertNotIn('name="action" value="release"', page)
        self.assertNotIn('name="action" value="complete"', page)

    def test_empty_sections_have_clear_messages(self):
        page = self.client.get('/board').get_data(as_text=True)
        self.assertIn('対応中の指示はありません。', page)
        self.assertIn('完了・解除済みの指示はありません。', page)

    def test_save_failure_is_reported_without_mutating_records_or_file(self):
        before = self.read_saved_instructions()
        with self.assertLogs(level='ERROR'):
            with patch.object(app, 'save_instructions', side_effect=OSError('disk full')):
                response = self.add_instruction('住民', '避難してください')
        self.assertEqual(response.status_code, 500)
        self.assertIn('指示を保存できませんでした', response.get_data(as_text=True))
        self.assertEqual(app.instructions, [])
        self.assertEqual(self.read_saved_instructions(), before)

        self.add_instruction('防災課', '確認してください')
        with self.assertLogs(level='ERROR'):
            with patch.object(app, 'save_instructions', side_effect=OSError('disk full')):
                response = self.change_state(1, 'complete', '対応中')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(app.instructions[0]['status'], '対応中')
        self.assertEqual(self.read_saved_instructions()[0]['status'], '対応中')

    def test_legacy_records_default_safely_and_sort_newest_first(self):
        app.instructions = [
            {'id': 1, 'target': '住民', 'content': '古い指示', 'created_at': '2026年10月18日 09:00'},
            {'id': 2, 'target': '道路管理課', 'content': '新しい指示', 'created_at': '2026年10月18日 10:00', 'status': '発信中'},
        ]
        page = self.client.get('/board').get_data(as_text=True)
        self.assertLess(page.index('新しい指示'), page.index('古い指示'))
        self.assertEqual(app.instructions[0].get('status'), None)
        self.assertEqual(app.instructions[1]['status'], '発信中')
        self.assertIn('完了', page)
        self.assertIn('解除', page)

    def test_legacy_state_for_wrong_audience_defaults_to_active(self):
        app.instructions = [
            {'id': 1, 'target': '住民', 'content': '住民指示', 'status': '完了'},
            {'id': 2, 'target': '防災課', 'content': '職員指示', 'status': '解除'},
        ]
        context = app.board_context()
        self.assertEqual([item['id'] for item in context['active_instructions']], [1, 2])
        self.assertEqual(context['resolved_instructions'], [])


if __name__ == '__main__':
    unittest.main()
