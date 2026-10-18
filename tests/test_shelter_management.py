import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app


class ShelterManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_shelters = app.shelters
        self.original_instructions = app.instructions
        self.original_data_file = app.DATA_FILE
        self.original_instructions_file = app.INSTRUCTIONS_FILE
        self.original_admin_credentials = app.ADMIN_CREDENTIALS
        app.DATA_FILE = os.path.join(self.temp_dir.name, 'shelters.json')
        app.INSTRUCTIONS_FILE = os.path.join(self.temp_dir.name, 'instructions.json')
        app.shelters = [{'id': 12, 'name': '既存避難所', 'address': '青森市', 'opening_status': '未開設'}]
        app.instructions = []
        app.ADMIN_CREDENTIALS = {'admin': 'test-only-password'}
        app.save_json_atomic(app.DATA_FILE, app.shelters)
        app.save_instructions([])
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True
        self.client.get('/shelter_register')
        with self.client.session_transaction() as session:
            self.csrf_token = session['csrf_token']

    def tearDown(self):
        app.shelters = self.original_shelters
        app.instructions = self.original_instructions
        app.DATA_FILE = self.original_data_file
        app.INSTRUCTIONS_FILE = self.original_instructions_file
        app.ADMIN_CREDENTIALS = self.original_admin_credentials
        self.temp_dir.cleanup()

    def shelter_form(self, **overrides):
        values = {
            'csrf_token': self.csrf_token,
            'name': '新しい避難所',
            'address': '青森市中央1-1',
            'capacity': '250',
            'disaster_types': ['地震', '洪水'],
            'facilities': ['バリアフリー', '備蓄あり'],
            'opening_status': '開設中',
        }
        values.update(overrides)
        return values

    def saved_shelters(self):
        with open(app.DATA_FILE, encoding='utf-8') as saved_file:
            return json.load(saved_file)

    def test_management_requires_authentication(self):
        client = app.app.test_client()
        for method, path, data in (
            ('get', '/shelter_register', None),
            ('post', '/shelter_register', self.shelter_form()),
            ('post', '/shelters/12/delete', {'csrf_token': self.csrf_token}),
        ):
            response = getattr(client, method)(path, data=data)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/login', response.headers['Location'])

    def test_login_tab_is_hidden_while_authenticated(self):
        authenticated_page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('>ログイン</a>', authenticated_page)
        self.assertIn('>ログアウト</a>', authenticated_page)

        anonymous_page = app.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('>ログイン</a>', anonymous_page)

    def test_registration_validates_required_numeric_and_allowed_values_with_values_retained(self):
        invalid_forms = (
            ({'name': '  ', 'address': '住所', 'capacity': '10'}, '避難所名を入力してください。'),
            ({'name': '保持する名前', 'address': '', 'capacity': '10'}, '住所を入力してください。'),
            ({'name': '保持する名前', 'address': '保持する住所', 'capacity': '1.5'}, '収容人数は1以上の整数'),
            ({'name': '保持する名前', 'address': '保持する住所', 'capacity': '0'}, '収容人数は1以上'),
            ({'name': '保持する名前', 'address': '保持する住所', 'capacity': '9' * 5000}, '収容人数は1以上'),
            ({'name': '保持する名前', 'address': '保持する住所', 'capacity': '10', 'disaster_types': ['不正値']}, '選択内容が正しくありません'),
        )
        for fields, message in invalid_forms:
            with self.subTest(fields=fields):
                form = self.shelter_form(**fields)
                response = self.client.post('/shelter_register', data=form)
                page = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, page)
                if fields.get('name', '').strip():
                    self.assertIn(fields['name'], page)
                self.assertEqual(len(self.saved_shelters()), 1)

    def test_create_edit_status_and_delete_persist(self):
        response = self.client.post('/shelter_register', data=self.shelter_form())
        self.assertEqual(response.status_code, 302)
        self.assertIn('notice=created', response.headers['Location'])
        saved = self.saved_shelters()
        created = saved[-1]
        self.assertEqual(created['opening_status'], '閉鎖中')
        self.assertEqual(created['capacity'], 250)
        self.assertEqual(created['disaster_types'], ['地震', '洪水'])
        self.assertNotEqual(created['id'], 12)
        self.assertIn('避難所を登録しました', self.client.get(response.headers['Location']).get_data(as_text=True))

        response = self.client.post(
            f"/shelters/{created['id']}/edit",
            data=self.shelter_form(name='編集後の避難所', opening_status='開設中'),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.saved_shelters()[-1]['name'], '編集後の避難所')
        self.assertEqual(self.saved_shelters()[-1]['opening_status'], '開設中')

        response = self.client.post(f"/shelters/{created['id']}/status", data={
            'csrf_token': self.csrf_token, 'opening_status': '未設定',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.saved_shelters()[-1]['opening_status'], '未設定')

        response = self.client.post(f"/shelters/{created['id']}/delete", data={'csrf_token': self.csrf_token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual([item['id'] for item in self.saved_shelters()], [12])
        self.assertEqual([item['id'] for item in app.shelters], [12])

    def test_unknown_targets_and_save_failures_do_not_mutate_shelters(self):
        before = self.saved_shelters()
        for path, data in (
            ('/shelters/999/edit', self.shelter_form()),
            ('/shelters/999/status', {'csrf_token': self.csrf_token, 'opening_status': '開設中'}),
            ('/shelters/999/delete', {'csrf_token': self.csrf_token}),
        ):
            response = self.client.post(path, data=data)
            self.assertEqual(response.status_code, 404)
            self.assertIn('対象の避難所が見つかりません', response.get_data(as_text=True))
        self.assertEqual(self.saved_shelters(), before)

        with self.assertLogs(level='ERROR'):
            with patch.object(app, 'save_json_atomic', side_effect=OSError('disk full')):
                response = self.client.post('/shelter_register', data=self.shelter_form())
        self.assertEqual(response.status_code, 500)
        self.assertIn('避難所を保存できませんでした', response.get_data(as_text=True))
        self.assertEqual(app.shelters, before)
        self.assertEqual(self.saved_shelters(), before)

    def test_status_and_delete_save_failures_leave_records_unchanged(self):
        before = self.saved_shelters()
        for path, data in (
            ('/shelters/12/status', {'csrf_token': self.csrf_token, 'opening_status': '開設中'}),
            ('/shelters/12/delete', {'csrf_token': self.csrf_token}),
        ):
            with self.subTest(path=path):
                with self.assertLogs(level='ERROR'):
                    with patch.object(app, 'save_json_atomic', side_effect=OSError('disk full')):
                        response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(app.shelters, before)
                self.assertEqual(self.saved_shelters(), before)

    def test_public_instruction_api_excludes_internal_audience_even_if_target_looks_public(self):
        app.instructions = [
            {'id': 1, 'target': '住民', 'audience': 'staff', 'content': '内部情報', 'status': '対応中'},
            {'id': 2, 'target': '住民', 'audience': 'resident', 'content': '解除済み情報', 'status': '解除'},
            {'id': 3, 'target': '防災課', 'audience': 'staff', 'content': '部署情報', 'status': '対応中'},
        ]
        response = self.client.get('/api/public_instructions')
        records = response.get_json()['instructions']
        self.assertEqual([item['id'] for item in records], [2])
        self.assertEqual(records[0]['status'], '解除')

    def test_home_has_recent_instruction_feed_connected_to_public_data(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('新着指示', page)
        self.assertIn('id="recentInstructions"', page)
        self.assertIn("fetch('/api/public_instructions'", page)
        self.assertIn('function renderRecentInstructions()', page)
        self.assertIn('function warningIcon(name)', page)
        self.assertIn("[/洪水/, '水']", page)

    def test_board_rejects_unknown_shelter_and_accepts_registered_id(self):
        response = self.client.post('/board', data={
            'csrf_token': self.csrf_token, 'target': '住民', 'content': '避難してください', 'shelter': '999',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('登録済みの避難所を選択してください', response.get_data(as_text=True))
        self.assertEqual(app.instructions, [])

        response = self.client.post('/board', data={
            'csrf_token': self.csrf_token, 'target': '住民', 'content': '避難してください', 'shelter': '12',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(app.instructions[0]['shelter'], '既存避難所')
        self.assertEqual(app.instructions[0]['shelter_id'], 12)
        self.assertEqual(app.instructions[0]['audience'], 'resident')


if __name__ == '__main__':
    unittest.main()