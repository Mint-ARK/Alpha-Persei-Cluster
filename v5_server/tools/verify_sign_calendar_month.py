"""Read-only calendar DTO regression probe, using only in-memory SQLite fixtures."""
import ast
import json
import sqlite3
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / 'gm_reader.py'
tree = ast.parse(SOURCE.read_text('utf-8-sig'))
reader_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'GMReader')
method = next(n for n in reader_class.body if isinstance(n, ast.FunctionDef) and n.name == 'get_month_sign_days')
namespace = {'json': json, 'time': SimpleNamespace(localtime=lambda: time.struct_time((2026, 11, 3, 12, 0, 0, 1, 307, 0)))}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), namespace)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users(uid INTEGER, level INTEGER);
            INSERT INTO users VALUES(42,70);
            CREATE TABLE sign(uid INTEGER, activity_id INTEGER, year INTEGER, month INTEGER, sign_list TEXT);
        ''')
        self.reader = SimpleNamespace(
            query=lambda sql, params=(): [dict(r) for r in self.db.execute(sql, params)],
            query_one=lambda sql, params=(): dict(self.db.execute(sql, params).fetchone()),
        )

    def add(self, year, month, days, uid=42, activity=3):
        self.db.execute('INSERT INTO sign VALUES(?,?,?,?,?)', (uid, activity, year, month, json.dumps(days)))

    def read(self, **kwargs):
        return namespace['get_month_sign_days'](self.reader, **kwargs)

    def test_previous_month_is_not_relabelled(self):
        self.add(2026, 10, [3, 10, 23])
        result = self.read(uid=42)
        self.assertEqual(result['signed_days'], [])
        self.assertEqual(result['total_signed'], 0)
        self.assertFalse(result['is_signed_today'])
        self.assertEqual(result['signed_dates'], ['2026-10-03', '2026-10-10', '2026-10-23'])

    def test_current_month_count_and_duplicates(self):
        self.add(2026, 11, [1, 3, 3])
        result = self.read(uid=42)
        self.assertEqual(result['signed_days'], [1, 3])
        self.assertEqual(result['total_signed'], 2)
        self.assertTrue(result['is_signed_today'])

    def test_year_is_part_of_identity(self):
        self.add(2025, 11, [3])
        result = self.read(uid=42)
        self.assertEqual(result['signed_days'], [])
        self.assertEqual(result['signed_dates'], ['2025-11-03'])

    def test_real_calendar_dates_and_bad_entries(self):
        self.add(2024, 2, [0, 1, 29, 30, True, '3', None, {}, 1])
        self.assertEqual(self.read(uid=42)['signed_dates'], ['2024-02-01', '2024-02-29'])

    def test_non_leap_february(self):
        self.add(2026, 2, [28, 29, 31])
        self.assertEqual(self.read(uid=42)['signed_dates'], ['2026-02-28'])

    def test_account_and_activity_isolation(self):
        self.add(2026, 11, [3], uid=99)
        self.add(2026, 11, [3], activity=34024)
        self.assertEqual(self.read(uid=42)['signed_dates'], [])
        self.assertEqual(self.read()['uid'], 42)

    def test_historical_query_is_not_today(self):
        self.add(2026, 10, [3])
        result = self.read(uid=42, year=2026, month=10)
        self.assertEqual(result['signed_days'], [3])
        self.assertFalse(result['is_signed_today'])

    def test_empty_and_invalid_records_have_no_fake_dots(self):
        self.assertEqual(self.read(uid=42)['signed_dates'], [])
        self.add(None, None, [3])
        self.add(2026, 13, [3])
        self.add(2026, 11, {'3': True})
        self.db.execute("INSERT INTO sign VALUES(42,3,2026,11,'broken')")
        self.assertEqual(self.read(uid=42)['signed_dates'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
