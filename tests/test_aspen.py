"""Unit tests for aspen.py.

Runnable without pyodbc or a live IP21 server via an injected fake connection:

    python -m unittest test_aspen -v
"""

import unittest
from datetime import datetime

import pandas as pd

from mfgkit import aspen
from mfgkit.aspen import AspenConn, ConnStatus, _chunks, _is_good_quality, _sanitize, _ts_literal


class FakeCursor:
    """Minimal pyodbc-cursor stand-in driven by a queue of canned results."""

    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql):
        self._conn.executed.append(sql)
        if self._conn.error_on_execute:
            raise aspen.DBError("boom")
        description, rows = self._conn.next_result()
        self.description = description
        self._rows = rows
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    """Records executed SQL and replays queued (description, rows) results."""

    def __init__(self, results=None, error_on_execute=False):
        # results: list of (description, rows); description is [(colname, ...), ...]
        self._results = list(results or [])
        self.executed = []
        self.closed = False
        self.timeout = None
        self.error_on_execute = error_on_execute

    def queue(self, columns, rows):
        self._results.append(([(c,) for c in columns], rows))

    def next_result(self):
        if self._results:
            return self._results.pop(0)
        return (None, [])  # statement with no result set (e.g. SET MAX_ROWS)

    def cursor(self):
        return FakeCursor(self)

    def execute(self, sql):
        self.executed.append(sql)
        return FakeCursor(self)

    def close(self):
        self.closed = True


def make_conn(results=None, **kwargs):
    conn = FakeConn(results=results)
    ac = AspenConn("HOST", connect_fn=lambda: conn, **kwargs)
    return ac, conn


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestHelpers(unittest.TestCase):
    def test_sanitize_escapes_quotes(self):
        self.assertEqual(_sanitize("FI-101.PV"), "FI-101.PV")
        self.assertEqual(_sanitize("A%B*"), "A%B*")

    def test_sanitize_rejects_bad_chars(self):
        with self.assertRaises(ValueError):
            _sanitize("DROP; SELECT")
        with self.assertRaises(ValueError):
            _sanitize("tag'name")  # quote is not in the allowed charset

    def test_sanitize_rejects_non_string(self):
        with self.assertRaises(TypeError):
            _sanitize(123)

    def test_ts_literal_from_datetime(self):
        ts = datetime(2026, 6, 11, 8, 30, 0)
        self.assertEqual(_ts_literal(ts), "TIMESTAMP'2026-06-11 08:30:00'")

    def test_ts_literal_from_string(self):
        self.assertEqual(
            _ts_literal("2026-06-11 08:30:00"), "TIMESTAMP'2026-06-11 08:30:00'"
        )

    def test_ts_literal_bad_type(self):
        with self.assertRaises(TypeError):
            _ts_literal(12345)

    def test_chunks(self):
        self.assertEqual(list(_chunks([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])
        with self.assertRaises(ValueError):
            list(_chunks([1, 2], 0))

    def test_is_good_quality(self):
        self.assertTrue(_is_good_quality("Good"))
        self.assertTrue(_is_good_quality("GOOD"))
        self.assertTrue(_is_good_quality(0))
        self.assertFalse(_is_good_quality("Bad"))
        self.assertFalse(_is_good_quality(None))


# --------------------------------------------------------------------------- #
# Connection lifecycle
# --------------------------------------------------------------------------- #


class TestLifecycle(unittest.TestCase):
    def test_connect_sets_max_rows(self):
        ac, conn = make_conn(max_rows=42)
        self.assertIn("SET MAX_ROWS 42;", conn.executed)
        self.assertEqual(conn.timeout, ac.timeout)

    def test_context_manager_closes(self):
        conn = FakeConn()
        with AspenConn("HOST", connect_fn=lambda: conn) as ac:
            self.assertIsNotNone(ac.conn)
        self.assertTrue(conn.closed)
        self.assertIsNone(ac.conn)

    def test_repr(self):
        ac, _ = make_conn()
        self.assertIn("open", repr(ac))
        ac.close()
        self.assertIn("closed", repr(ac))


# --------------------------------------------------------------------------- #
# Status features
# --------------------------------------------------------------------------- #


class TestStatus(unittest.TestCase):
    def test_status_ok(self):
        ac, conn = make_conn()
        conn.queue(["NAME"], [])  # ping returns no rows but succeeds
        st = ac.status()
        self.assertIsInstance(st, ConnStatus)
        self.assertTrue(st.connected)
        self.assertTrue(bool(st))
        self.assertTrue(ac.is_alive)
        self.assertIn(ac.ping_sql, conn.executed)

    def test_status_when_closed(self):
        ac, _ = make_conn()
        ac.close()
        st = ac.status()
        self.assertFalse(st.connected)
        self.assertEqual(st.error, "connection closed")
        self.assertFalse(ac.is_alive)

    def test_status_handles_db_error(self):
        ac, conn = make_conn()
        conn.error_on_execute = True
        st = ac.status()
        self.assertFalse(st.connected)
        self.assertIsNotNone(st.error)

    def test_snapshot_parses_value_and_quality(self):
        ac, conn = make_conn()
        conn.queue(
            ["NAME", "IP_INPUT_VALUE", "IP_INPUT_TIME", "IP_INPUT_QUALITY"],
            [
                ("FI-101.PV", 12.5, datetime(2026, 6, 11, 8, 0, 0), "Good"),
                ("TI-202.PV", 88.0, datetime(2026, 6, 11, 8, 0, 1), "Bad"),
            ],
        )
        snap = ac.snapshot(["FI-101.PV", "TI-202.PV"])
        self.assertEqual(list(snap.columns), ["VALUE", "TS", "QUALITY", "IS_GOOD"])
        self.assertEqual(snap.index.name, "NAME")
        self.assertTrue(snap.loc["FI-101.PV", "IS_GOOD"])
        self.assertFalse(snap.loc["TI-202.PV", "IS_GOOD"])
        self.assertEqual(snap.loc["FI-101.PV", "VALUE"], 12.5)

    def test_snapshot_empty(self):
        ac, conn = make_conn()
        conn.queue(["NAME", "IP_INPUT_VALUE", "IP_INPUT_TIME", "IP_INPUT_QUALITY"], [])
        snap = ac.snapshot("FI-101.PV")
        self.assertTrue(snap.empty)
        self.assertEqual(list(snap.columns), ["VALUE", "TS", "QUALITY", "IS_GOOD"])

    def test_io_task_status_query(self):
        ac, conn = make_conn()
        conn.queue(["IO_MAIN_TASK", "Total_Tags"], [("TSK1", 10)])
        df = ac.io_task_status()
        self.assertEqual(len(df), 1)
        self.assertIn("IOGetHistDef", conn.executed[-1])

    def test_iostatus_alias(self):
        self.assertIs(AspenConn.iostatus, AspenConn.io_task_status)


# --------------------------------------------------------------------------- #
# History queries
# --------------------------------------------------------------------------- #


class TestHistory(unittest.TestCase):
    def test_history_pivots_wide(self):
        ac, conn = make_conn()
        conn.queue(
            ["NAME", "TS", "VALUE"],
            [
                ("FI-101.PV", datetime(2026, 6, 11, 8, 0, 0), 1.0),
                ("TI-202.PV", datetime(2026, 6, 11, 8, 0, 0), 2.0),
                ("FI-101.PV", datetime(2026, 6, 11, 8, 0, 1), 1.5),
            ],
        )
        df = ac.start_end(
            ["FI-101.PV", "TI-202.PV"], "2026-06-11 08:00", "2026-06-11 09:00"
        )
        self.assertEqual(set(df.columns), {"FI-101.PV", "TI-202.PV"})
        self.assertTrue(df.index.is_monotonic_increasing)
        sql = conn.executed[-1]
        self.assertIn("REQUEST = 1", sql)
        self.assertIn("TIMESTAMP'2026-06-11 08:00:00'", sql)

    def test_history_empty(self):
        ac, conn = make_conn()
        conn.queue(["NAME", "TS", "VALUE"], [])
        df = ac.current("FI-101.PV", mins=5)
        self.assertTrue(df.empty)

    def test_current_duration_in_tenths(self):
        ac, conn = make_conn()
        conn.queue(["NAME", "TS", "VALUE"], [])
        ac.current("FI-101.PV", mins=1)  # 60 s -> 600 tenths
        self.assertIn("CURRENT_TIMESTAMP - 600", conn.executed[-1])

    def test_interpolated_adds_period_and_request2(self):
        ac, conn = make_conn()
        conn.queue(["NAME", "TS", "VALUE"], [])
        ac.interpolated("FI-101.PV", "2026-06-11 08:00", "2026-06-11 09:00",
                        period_seconds=60)
        sql = conn.executed[-1]
        self.assertIn("REQUEST = 2", sql)
        self.assertIn("PERIOD = 600", sql)

    def test_chunking_runs_multiple_queries(self):
        ac, conn = make_conn(chunk_size=1)
        conn.queue(["NAME", "TS", "VALUE"], [
            ("A", datetime(2026, 6, 11, 8, 0, 0), 1.0)])
        conn.queue(["NAME", "TS", "VALUE"], [
            ("B", datetime(2026, 6, 11, 8, 0, 0), 2.0)])
        df = ac.start_end(["A", "B"], "2026-06-11 08:00", "2026-06-11 09:00")
        history_queries = [s for s in conn.executed if "FROM HISTORY" in s]
        self.assertEqual(len(history_queries), 2)
        self.assertEqual(set(df.columns), {"A", "B"})

    def test_aggregates_long_format(self):
        ac, conn = make_conn()
        conn.queue(
            ["NAME", "TS", "AVG", "MIN", "MAX"],
            [("FI-101.PV", datetime(2026, 6, 11, 8, 0, 0), 1.0, 0.5, 1.5)],
        )
        df = ac.aggregates("FI-101.PV", "2026-06-11 08:00", "2026-06-11 09:00")
        self.assertEqual(len(df), 1)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["TS"]))


# --------------------------------------------------------------------------- #
# Execution / reconnect
# --------------------------------------------------------------------------- #


class TestExecution(unittest.TestCase):
    def test_reconnect_once_on_failure(self):
        bad = FakeConn(error_on_execute=True)  # first connection fails on query
        good = FakeConn()                      # reconnect lands here and works
        good.queue(["NAME", "TS", "VALUE"], [
            ("A", datetime(2026, 6, 11, 8, 0, 0), 1.0)])
        made = []
        pool = [bad, good]

        def connect():
            c = pool[len(made)]
            made.append(c)
            return c

        ac = AspenConn("HOST", connect_fn=connect)
        df = ac.start_end("A", "2026-06-11 08:00", "2026-06-11 09:00")
        self.assertEqual(len(made), 2)  # one reconnect happened
        self.assertEqual(list(df.columns), ["A"])

    def test_raises_after_second_failure(self):
        def connect():
            return FakeConn(error_on_execute=True)

        ac = AspenConn("HOST", connect_fn=connect)
        with self.assertRaises(aspen.DBError):
            ac.query("SELECT 1")

    def test_no_result_set_returns_empty(self):
        ac, conn = make_conn()
        # next_result with empty queue yields (None, []) -> empty DataFrame
        df = ac.query("SET SOMETHING 1;")
        self.assertTrue(df.empty)


if __name__ == "__main__":
    unittest.main()
