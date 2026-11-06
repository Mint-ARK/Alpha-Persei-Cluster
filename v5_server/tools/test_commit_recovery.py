"""A375 regression: real SQLite memory only, no application/database startup.

Run: python -B v5_server/tools/test_commit_recovery.py
"""
import ast
import builtins
import datetime
import importlib
import json
import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from account_db import AccountDB  # No constructor or schema initialization.
from operation_notifications import (
    checkpoint_notifications, restore_notifications,
    clear_instance_deletions, take_pending_frames,
)


class FaultConnection(sqlite3.Connection):
    """Real engine failures plus finite cleanup/unknown-outcome adapters."""
    fail_rollback = False
    fail_close = False
    fail_after_commit = False

    def rollback(self):
        if self.fail_rollback:
            raise sqlite3.OperationalError("fixture rollback failure")
        return super().rollback()

    def close(self):
        if self.fail_close:
            raise sqlite3.OperationalError("fixture close failure")
        return super().close()

    def commit(self):
        super().commit()
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise sqlite3.OperationalError("fixture unknown commit outcome")


def make_db():
    db = object.__new__(AccountDB)
    db.path = ":memory:"
    db._local = threading.local()
    c = sqlite3.connect(":memory:", factory=FaultConnection)
    c.row_factory = sqlite3.Row
    c.executescript("""
CREATE TABLE momotalk_session(uid INTEGER, session_id INTEGER, is_view INTEGER,
                             update_ts INTEGER DEFAULT 0);
INSERT INTO momotalk_session VALUES(42,119401,0,0);
CREATE TABLE currency(uid INTEGER, id INTEGER, num INTEGER,
                      PRIMARY KEY(uid,id));
INSERT INTO currency VALUES(42,2,100);
CREATE TABLE user_timer_watermark(
 uid INTEGER PRIMARY KEY,last_pulse_ts INTEGER,last_daily_5am_ts INTEGER,
 last_weekly_mon_ts INTEGER,last_weekly_thu_ts INTEGER,last_monthly_ts INTEGER,
 last_heartbeat_ts INTEGER,today_online_seconds INTEGER,today_online_date TEXT,
 is_online INTEGER,last_logout_ts INTEGER,update_ts INTEGER);
INSERT INTO user_timer_watermark VALUES(42,0,0,0,0,0,0,0,'',1,0,0);
""")
    db._local.conn = c
    return db, c


def deny_once(c, verb):
    remaining = [verb]

    def authorize(action, arg1, *args):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == remaining[0]:
            remaining[0] = None
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    c.set_authorizer(authorize)


def selected(filename, names, env):
    nodes = ast.parse((ROOT / filename).read_text("utf-8-sig")).body
    picked = [n for n in nodes if isinstance(n, (ast.ClassDef, ast.FunctionDef))
              and n.name in names]
    assert {n.name for n in picked} == set(names)
    for n in picked:
        n.decorator_list = []
    exec(compile(ast.Module(body=picked, type_ignores=[]), filename, "exec"), env)


class CommitRecovery(unittest.TestCase):
    def setUp(self):
        self.db, self.c = make_db()
        self.addCleanup(self.db.close)

    def view(self):
        return self.db.query("SELECT is_view FROM momotalk_session")[0]["is_view"]

    def clean(self):
        self.assertFalse(self.db._in_txn())
        self.assertFalse(self.c.in_transaction)

    def test_commit_refusal_rolls_back_before_query_and_later_write(self):
        deny_once(self.c, "COMMIT")
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.transaction():
                self.db.set_momotalk_read(42, 119401)
        self.assertEqual(self.view(), 0)
        self.clean()
        self.db.execute("SELECT 1")
        self.c.rollback()
        self.assertEqual(self.view(), 0)
        with self.db.transaction():
            self.db.set_momotalk_read(42, 119401)
        self.assertEqual(self.view(), 1)
        self.clean()

    def test_standalone_execute_and_executemany_commit_refusal(self):
        for many in (False, True):
            with self.subTest(many=many):
                deny_once(self.c, "COMMIT")
                with self.assertRaises(sqlite3.DatabaseError):
                    if many:
                        self.db.executemany("UPDATE currency SET num=?", [(90,), (80,)])
                    else:
                        self.db.execute("UPDATE currency SET num=90")
                self.clean()
                self.db.execute("SELECT 1")
                self.assertEqual(self.db.query("SELECT num FROM currency")[0]["num"], 100)

    def test_executemany_partial_sql_failure_rolls_back(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.executemany("INSERT INTO currency VALUES(?,?,?)",
                                [(42, 3, 5), (42, 2, 1)])
        self.clean()
        self.assertEqual(len(self.db.query("SELECT * FROM currency")), 1)

    def test_begin_refusal_leaves_marker_clean_and_retry_works(self):
        deny_once(self.c, "BEGIN")
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.transaction():
                self.fail("BEGIN was refused")
        self.clean()
        with self.db.transaction():
            self.db.set_momotalk_read(42, 119401)
        self.assertEqual(self.view(), 1)

    def test_nested_success_and_outer_sql_failure(self):
        with self.db.transaction():
            with self.db.transaction():
                self.db.set_momotalk_read(42, 119401)
            self.assertTrue(self.c.in_transaction)
            self.assertTrue(self.db._in_txn())
        self.assertEqual(self.view(), 1)
        with self.assertRaises(sqlite3.OperationalError):
            with self.db.transaction():
                with self.db.transaction():
                    self.db.execute("UPDATE momotalk_session SET is_view=0")
                self.db.execute("INSERT INTO missing_fixture VALUES(1)")
        self.assertEqual(self.view(), 1)
        self.clean()

    def test_unowned_transaction_cannot_be_adopted_by_next_request(self):
        for entry in ("execute", "executemany", "query", "transaction"):
            with self.subTest(entry=entry):
                self.c.execute("UPDATE momotalk_session SET is_view=1")
                with self.assertRaises(sqlite3.OperationalError):
                    if entry == "transaction":
                        with self.db.transaction():
                            self.fail("must reject unowned transaction")
                    elif entry == "executemany":
                        self.db.executemany("UPDATE currency SET num=? WHERE id=0", [(1,)])
                    else:
                        getattr(self.db, entry)("SELECT 1")
                self.clean()
                self.assertEqual(self.view(), 0)

    def test_rollback_failure_discards_connection_preserving_original_error(self):
        for failed_commit in (False, True):
            with self.subTest(failed_commit=failed_commit):
                db, c = make_db()
                c.fail_rollback = True
                try:
                    if failed_commit:
                        deny_once(c, "COMMIT")
                    with self.assertRaisesRegex(Exception, "not authorized" if failed_commit else "body fault"):
                        with db.transaction():
                            db.set_momotalk_read(42, 119401)
                            if not failed_commit:
                                raise ValueError("body fault")
                    self.assertIsNone(db._local.conn)
                    self.assertFalse(db._in_txn())
                    with self.assertRaises(sqlite3.ProgrammingError):
                        c.execute("SELECT 1")
                    replacement = db._conn()
                    self.assertIsNot(replacement, c)
                    self.assertFalse(replacement.in_transaction)
                    with db.transaction():
                        db.execute("CREATE TABLE recovery(value INTEGER)")
                        db.execute("INSERT INTO recovery VALUES(1)")
                    self.assertEqual(db.query("SELECT value FROM recovery")[0]["value"], 1)
                finally:
                    db.close()

    def test_standalone_cleanup_failure_also_discards_connection(self):
        self.c.fail_rollback = True
        self.c.fail_close = True
        deny_once(self.c, "COMMIT")
        try:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "not authorized"):
                self.db.execute("UPDATE currency SET num=90")
            self.assertIsNone(self.db._local.conn)
            self.assertFalse(self.db._in_txn())
        finally:
            self.c.fail_rollback = self.c.fail_close = False
            self.c.rollback()
            self.c.close()

    def test_commit_error_without_open_transaction_is_not_retried(self):
        self.c.fail_after_commit = True
        with self.assertRaisesRegex(sqlite3.OperationalError, "unknown commit outcome"):
            with self.db.transaction():
                self.db.execute("UPDATE currency SET num=num-10")
        self.clean()
        # Outcome is uncertain to the caller: preserve failure, never retry debit.
        self.assertEqual(self.db.query("SELECT num FROM currency")[0]["num"], 90)
        self.db.execute("SELECT 1")
        self.assertEqual(self.db.query("SELECT num FROM currency")[0]["num"], 90)

    def test_core_encoded_failure_timer_and_retry(self):
        now = [int(datetime.datetime(2026, 10, 18, 12,
                     tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())]
        logs = []
        logger = ModuleType("logger")
        logger.log = lambda *a, **k: None
        old_logger = sys.modules.get("logger")
        sys.modules["logger"] = logger
        self.addCleanup(lambda: sys.modules.pop("logger", None) if old_logger is None
                        else sys.modules.__setitem__("logger", old_logger))
        codec = importlib.import_module("codec")
        codec._ensure_schema()
        # The Thursday helper now delegates to this pure, explicit-zone clock.
        imports = {name: importlib.import_module(name) for name in
                   ('boss_challenge_cycle', 'res_version_manager', 'multiplayer_policy')}

        def safe_import(name, *args, **kwargs):
            if name in imports:
                return imports[name]
            raise AssertionError("Unexpected application import: " + name)

        env = dict(threading=threading, json=json, time=NS(time=lambda: now[0]),
                   datetime=datetime, logger=NS(exception=lambda *a, **k: None),
                   checkpoint_notifications=checkpoint_notifications,
                   restore_notifications=restore_notifications,
                   clear_instance_deletions=clear_instance_deletions,
                   take_pending_frames=take_pending_frames,
                   __builtins__=dict(vars(builtins), __import__=safe_import))
        selected("event_bus.py", {"EventBus", "Events"}, env)
        env["bus"] = env["EventBus"]()
        selected("lazy_timer.py", {"get_daily_5am_ts", "get_weekly_mon_5am_ts",
                 "get_weekly_thu_5am_ts", "get_monthly_5am_ts", "LazyTimerBroadcaster"}, env)
        timer = env["LazyTimerBroadcaster"]()
        self.db.update_timer_watermark(42, last_pulse_ts=now[0],
            last_daily_5am_ts=env["get_daily_5am_ts"](now[0]),
            last_weekly_mon_ts=env["get_weekly_mon_5am_ts"](now[0]),
            last_weekly_thu_ts=env["get_weekly_thu_5am_ts"](now[0]),
            last_monthly_ts=env["get_monthly_5am_ts"](now[0]))
        selected("middleware.py", {"CSRequest", "CSResponse", "DownFrame", "HandlerCtx", "_varint", "h_10700"}, env)
        selected("core.py", {"Core", "CoreRequest", "CoreResponse", "Connection", "Operation", "OperationError", "_null_cm"}, env)
        selected("operations.py", {"MomoTalkSetReadOp"}, env)
        imports.update(time=env["time"], json=json, codec=codec,
                       lazy_timer=NS(lazy_timer=timer),
                       log_sifter=NS(get_operation_name=lambda *a: "read marker",
                           extract_logistics_details=lambda *a: {},
                           build_logistics_line=lambda *a: "fixture logistics"))
        env["OPERATIONS"] = {91006: env["MomoTalkSetReadOp"]()}
        env["_mw"] = NS(get_skeleton=lambda cmd: None, _varint=env["_varint"])
        env["_dispatch"] = lambda req, ctx: env["h_10700"](req, ctx)
        core = env["Core"](db=self.db, codec_encode=codec.encode, codec_decode=codec.decode,
                           log=lambda *a, **k: logs.append(str(a)))
        conn = env["Connection"](uid=42, db=self.db, codec_encode=codec.encode,
                                 codec_decode=codec.decode, log=core.log)

        def call(cmd=91006):
            resp = core.dispatch(env["CoreRequest"](cmd, {"session_id": 119401}, uid=42), conn)
            return [dict(sc=f["sc"], data=codec.decode(f["payload"], "sc_" + str(f["sc"])))
                    for f in resp.frames]

        deny_once(self.c, "COMMIT")
        failed = call()
        self.assertEqual(failed[0], dict(sc=91007, data={"result": 9}))
        self.assertEqual(self.view(), 0)
        self.clean()
        now[0] += 6
        pulse = call(10700)
        self.assertEqual(pulse[0]["data"]["timestamp"], now[0])
        self.assertEqual(self.view(), 0)
        self.clean()
        self.assertEqual(call()[0], dict(sc=91007, data={"result": 0}))
        self.assertEqual(self.view(), 1)
        self.assertFalse(any("cannot start a transaction" in s for s in logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
