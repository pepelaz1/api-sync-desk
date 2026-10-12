import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from sync import connect, export_csv, fetch_products, snapshot, sync

ROW = {'sku': 'A', 'name': 'Sample', 'price': '12.30', 'currency': 'USD'}

class Tests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
    def tearDown(self):
        self.db.close()
    def test_repeat_preserves_one_product(self):
        self.assertEqual(sync(self.db, [ROW])['inserted'], 1)
        self.assertEqual(sync(self.db, [ROW])['unchanged'], 1)
        self.assertEqual(len(snapshot(self.db)['products']), 1)
    def test_changed_price_updates(self):
        sync(self.db, [ROW])
        self.assertEqual(sync(self.db, [{**ROW, 'price': '13.00'}])['updated'], 1)
        self.assertEqual(snapshot(self.db)['products'][0]['price'], '13.00')
    def test_invalid_rows_and_duplicates_reported(self):
        r = sync(self.db, [ROW, ROW, {**ROW, 'sku': 'B', 'price': 'NaN'}, None])
        self.assertEqual((r['inserted'], r['rejected']), (1, 3))
        self.assertEqual([e['row'] for e in r['errors']], [2, 3, 4])
    def test_csv_neutralizes_formulas(self):
        sync(self.db, [{**ROW, 'name': '=1+1'}])
        self.assertIn("'=1+1", export_csv(self.db))
    def test_transient_failure_retries(self):
        response = io.BytesIO(b'[]')
        with patch('urllib.request.urlopen', side_effect=[HTTPError('u',503,'busy',{},None), response]) as call:
            waits = []
            self.assertEqual(fetch_products('http://local', sleep=waits.append), [])
            self.assertEqual((call.call_count, waits), (2, [1]))
    def test_client_error_not_retried(self):
        with patch('urllib.request.urlopen', side_effect=HTTPError('u',401,'auth',{},None)) as call:
            with self.assertRaises(HTTPError):
                fetch_products('http://local', sleep=lambda _: None)
            self.assertEqual(call.call_count, 1)
    def test_retries_exhaust_and_do_not_overwrite_data(self):
        sync(self.db, [ROW])
        with patch('urllib.request.urlopen', side_effect=HTTPError('u',503,'busy',{},None)) as call:
            with self.assertRaises(HTTPError):
                fetch_products('http://local', sleep=lambda _: None)
            self.assertEqual(call.call_count, 3)
        self.assertEqual(len(snapshot(self.db)['products']), 1)

if __name__ == '__main__':
    unittest.main()
