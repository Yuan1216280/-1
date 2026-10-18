import unittest

import app


class ShelterSearchTests(unittest.TestCase):
    def setUp(self):
        self.original_shelters = app.shelters
        app.shelters = [
            {
                'id': 1, 'name': '青森小学校', 'district': '第一地区',
                'safe_for': ['津波', '洪水'], 'pet_allowed': True, 'barrier_free': False,
            },
            {
                'id': 2, 'name': '浪岡小学校', 'district': '浪岡地区',
                'safe_for': ['土砂災害'], 'pet_allowed': False, 'barrier_free': True,
            },
            {'id': 3, 'name': '中央小学校', 'district': '第一地区'},
            {
                'id': 4, 'name': '旧式ペット対応施設', 'district': '第一地区',
                'disaster_types': ['洪水'], 'facilities': ['ペット同行'],
            },
            {
                'id': 5, 'name': '複合条件施設', 'district': '浪岡地区',
                'safe_for': ['洪水'], 'pet_allowed': True, 'barrier_free': True,
            },
            {
                'id': 6, 'name': 'North Hall', 'district': '第一地区',
                'safe_for': [], 'pet_allowed': False, 'barrier_free': False,
            },
            {'id': 7, 'name': '空地区施設', 'district': '   '},
        ]
        self.client = app.app.test_client()

    def tearDown(self):
        app.shelters = self.original_shelters

    def search_ids(self, **query):
        results, _ = app.search_shelters(app.shelters, query)
        return [shelter['id'] for shelter in results]

    def test_empty_conditions_return_all_facilities(self):
        self.assertEqual(self.search_ids(), [1, 2, 3, 4, 5, 6, 7])
        response = self.client.get('/search_results')
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('7件の避難所', page)
        self.assertIn('未登録', page)

    def test_each_condition_filters_independently(self):
        self.assertEqual(self.search_ids(disaster_type='津波'), [1])
        self.assertEqual(self.search_ids(district='第一地区'), [1, 3, 4, 6])
        self.assertEqual(self.search_ids(district='浪岡地区'), [2, 5])
        self.assertEqual(self.search_ids(name='学校'), [1, 2, 3])
        self.assertEqual(self.search_ids(name='north'), [6])
        self.assertEqual(self.search_ids(pet_allowed='1'), [1, 4, 5])
        self.assertEqual(self.search_ids(barrier_free='1'), [2, 5])

    def test_all_conditions_combine_with_and(self):
        self.assertEqual(self.search_ids(
            disaster_type='洪水', district='浪岡地区', name='複合',
            pet_allowed='1', barrier_free='1',
        ), [5])
        self.assertEqual(self.search_ids(
            disaster_type='津波', district='浪岡地区', pet_allowed='1',
        ), [])

    def test_unknown_disaster_is_ignored_and_unknown_district_matches_nothing(self):
        ids, filters = app.search_shelters(app.shelters, {'disaster_type': '火山'})
        self.assertEqual([shelter['id'] for shelter in ids], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(filters['disaster_type'], '')
        self.assertEqual(self.search_ids(district='未登録地区'), [])

    def test_unknown_and_false_features_do_not_match_and_display_as_unregistered(self):
        self.assertNotIn(3, self.search_ids(pet_allowed='1'))
        self.assertNotIn(2, self.search_ids(pet_allowed='1'))
        self.assertNotIn(1, self.search_ids(barrier_free='1'))
        views = {item['id']: item for item in app.search_shelters(app.shelters, {})[0]}
        self.assertEqual(views[1]['_search_pet_status'], '可')
        self.assertEqual(views[1]['_search_barrier_status'], '未対応')
        self.assertEqual(views[2]['_search_pet_status'], '不可')
        self.assertEqual(views[2]['_search_barrier_status'], '対応')
        self.assertEqual(views[3]['_search_pet_status'], '未登録')
        self.assertEqual(views[3]['_search_barrier_status'], '未登録')
        self.assertEqual(views[3]['_search_safe_for'], [])

    def test_search_form_has_distinct_nonempty_districts_and_get_navigation(self):
        self.assertEqual(len(app.SEARCH_DISTRICT_CHOICES), 38)
        response = self.client.get('/shelter_search')
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('災害の種類や必要な設備から、避難先を探せます。', page)
        self.assertIn('method="get"', page)
        self.assertIn('条件を指定して検索', page)
        self.assertIn('条件をクリア', page)
        self.assertEqual(page.count('value="第一地区"'), 1)
        self.assertEqual(page.count('value="浪岡地区"'), 1)
        self.assertIn('value="東部地区"', page)
        self.assertIn('value="勝田奥野地区"', page)
        self.assertNotIn('value="片瀬"', page)
        self.assertNotIn('value="御所見"', page)
        self.assertNotIn('value="   "', page)
        self.assertIn('placeholder="例：青森市立小学校"', page)
        self.assertIn('入力例：青森市立小学校、青森市民センター', page)
        self.assertIn('全施設一覧', page)
        self.assertIn('ホームへ戻る', page)

    def test_results_show_count_empty_message_and_safety_note(self):
        page = self.client.get('/search_results?district=存在しない地区').get_data(as_text=True)
        self.assertIn('0件の避難所', page)
        self.assertIn('条件に合う避難所が見つかりませんでした。条件を減らすか、未登録の情報は施設へ確認してください。', page)
        self.assertIn('災害への安全性や設備情報が未登録の施設は、条件検索の対象外です。現地の状況や自治体の最新情報も確認してください。', page)

    def test_all_facilities_reuses_results_view_with_unregistered_values(self):
        page = self.client.get('/all_shelters').get_data(as_text=True)
        self.assertIn('登録施設一覧', page)
        self.assertIn('7件の避難所', page)
        self.assertIn('旧式ペット対応施設', page)
        self.assertIn('未登録', page)


if __name__ == '__main__':
    unittest.main()