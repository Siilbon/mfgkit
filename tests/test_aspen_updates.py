"""Coverage for the aspen changes brought over from the work computer.

These arrived untested: date-range chunking (_ts_chunks / chunk_days), SQL
identifier validation (_sanitize_field), the generalized search_tags, and the
MAX_ROWS truncation warning. The corrected constants and the ip_discrete
table fix are pinned here too, so a future edit can't silently revert them.
"""

import logging
from datetime import datetime

import pytest

from mfgkit.aspen import AspenConn, _sanitize_field
from mfgkit.aspen.helpers import IP21_TS_FORMAT, IP21_TS_PARSE_FORMAT, _ts_chunks

from test_aspen import FakeConn, make_conn


# --------------------------------------------------------------------------- #
# Corrected constants -- these were the live bugs
# --------------------------------------------------------------------------- #

def test_send_and_parse_timestamp_formats_differ():
    """SQLplus rejects %d-%b-%y on input but returns it in result rows."""
    assert IP21_TS_FORMAT == "%Y-%m-%d %H:%M:%S"
    assert IP21_TS_PARSE_FORMAT == "%d-%b-%y %H:%M:%S.%f"
    # A returned TS must actually parse with the parse format.
    assert datetime.strptime("11-Jun-26 08:30:00.1", IP21_TS_PARSE_FORMAT)


def test_snapshot_selects_quality_not_qstatus():
    ac, conn = make_conn()
    conn.queue(["NAME", "IP_INPUT_VALUE", "IP_INPUT_TIME", "IP_INPUT_QUALITY"], [])
    ac.snapshot(["FI-101.PV"])
    assert "IP_INPUT_QUALITY" in conn.executed[-1]
    assert "IP_INPUT_QSTATUS" not in conn.executed[-1]


def test_ip_discrete_queries_the_discrete_table():
    """It previously queried IP_AnalogDef -- a copy-paste bug."""
    ac, conn = make_conn()
    conn.queue(["NAME"], [])
    ac.ip_discrete("FI")
    assert "IP_DiscreteDef" in conn.executed[-1]
    assert "IP_AnalogDef" not in conn.executed[-1]


# --------------------------------------------------------------------------- #
# _sanitize_field
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("field", ["NAME", "IP_DESCRIPTION", "A1"])
def test_sanitize_field_accepts_bare_identifiers(field):
    assert _sanitize_field(field) == field


@pytest.mark.parametrize(
    "field",
    ["NAME; DROP TABLE X", "NAME OR 1=1", "a b", "1NAME", "", "NAME'", "NAME--"],
)
def test_sanitize_field_rejects_injection(field):
    with pytest.raises(ValueError):
        _sanitize_field(field)


def test_sanitize_field_rejects_non_string():
    with pytest.raises(TypeError):
        _sanitize_field(123)


# --------------------------------------------------------------------------- #
# _ts_chunks
# --------------------------------------------------------------------------- #

def test_ts_chunks_splits_range():
    chunks = list(_ts_chunks(datetime(2026, 1, 1), datetime(2026, 1, 4), days=1))
    assert len(chunks) == 3
    assert chunks[0] == (datetime(2026, 1, 1), datetime(2026, 1, 2))
    # Windows are contiguous and end exactly on the requested end.
    assert chunks[-1][1] == datetime(2026, 1, 4)
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))


def test_ts_chunks_short_range_is_one_chunk():
    chunks = list(_ts_chunks(datetime(2026, 1, 1), datetime(2026, 1, 1, 6), days=1))
    assert chunks == [(datetime(2026, 1, 1), datetime(2026, 1, 1, 6))]


def test_ts_chunks_empty_when_end_not_after_start():
    assert list(_ts_chunks(datetime(2026, 1, 2), datetime(2026, 1, 2), days=1)) == []


def test_ts_chunks_accepts_strings():
    chunks = list(_ts_chunks("2026-01-01", "2026-01-03", days=1))
    assert len(chunks) == 2


# --------------------------------------------------------------------------- #
# chunk_days wiring
# --------------------------------------------------------------------------- #

def _history_desc():
    return ["NAME", "TS", "VALUE"]


def test_start_end_issues_one_query_per_day_chunk():
    ac, conn = make_conn()
    for _ in range(3):
        conn.queue(_history_desc(), [])
    ac.start_end(["FI-101.PV"], "2026-01-01", "2026-01-04", chunk_days=1)

    history_queries = [q for q in conn.executed if "FROM HISTORY" in q]
    assert len(history_queries) == 3
    assert "TIMESTAMP'2026-01-01 00:00:00'" in history_queries[0]
    assert "TIMESTAMP'2026-01-04 00:00:00'" in history_queries[-1]


def test_interpolated_chunks_and_sets_period():
    ac, conn = make_conn()
    for _ in range(2):
        conn.queue(_history_desc(), [])
    ac.interpolated(["FI-101.PV"], "2026-01-01", "2026-03-02",
                    period_seconds=60, chunk_days=30)

    history_queries = [q for q in conn.executed if "FROM HISTORY" in q]
    assert len(history_queries) == 2  # 60-day span / 30-day chunks
    assert "REQUEST = 2" in history_queries[0]
    assert "600" in history_queries[0]  # 60s in tenths


def test_max_rows_truncation_warns(caplog):
    ac, conn = make_conn(max_rows=2)
    conn.queue(_history_desc(), [
        ("FI-101.PV", "11-Jun-26 08:00:00.0", 1.0),
        ("FI-101.PV", "11-Jun-26 08:00:01.0", 1.5),
    ])
    with caplog.at_level(logging.WARNING, logger="mfgkit.aspen.history"):
        ac.start_end(["FI-101.PV"], "2026-06-11 08:00", "2026-06-11 09:00")

    assert "MAX_ROWS" in caplog.text
    assert "truncated" in caplog.text


# --------------------------------------------------------------------------- #
# search_tags
# --------------------------------------------------------------------------- #

def test_search_tags_rejects_unknown_table():
    ac, _ = make_conn()
    with pytest.raises(ValueError, match="table must be one of"):
        ac.search_tags("FI", table="Users; DROP TABLE X")


def test_search_tags_rejects_injected_field():
    ac, _ = make_conn()
    with pytest.raises(ValueError):
        ac.search_tags("FI", field="NAME LIKE '%' OR 1=1 --")


def test_search_tags_searches_a_chosen_field():
    ac, conn = make_conn()
    conn.queue(["NAME"], [])
    ac.search_tags("pump", field="IP_DESCRIPTION", table="IP_DiscreteDef")
    sql = conn.executed[-1]
    assert "IP_DiscreteDef" in sql
    assert "IP_DESCRIPTION LIKE '%pump%'" in sql


# --------------------------------------------------------------------------- #
# search_io_defs
# --------------------------------------------------------------------------- #

def test_search_io_defs_defaults_to_dcs_tag_in_iogetdef():
    ac, conn = make_conn()
    conn.queue(["NAME", "IO_TAGNAME", "IO_VALUE_RECORD&&FLD"], [])
    ac.search_io_defs("FT21001")
    sql = conn.executed[-1]
    assert "FROM IOGetDef" in sql
    assert "\"IO_TAGNAME\" LIKE '%FT21001%'" in sql


@pytest.mark.parametrize("field", ["NAME", "IO_TAGNAME", "IO_VALUE_RECORD&&FLD"])
def test_search_io_defs_searches_each_allowed_field(field):
    ac, conn = make_conn()
    conn.queue(["NAME"], [])
    ac.search_io_defs("FI", field=field, table="IOGetHistDef")
    sql = conn.executed[-1]
    assert "FROM IOGetHistDef" in sql
    assert f"\"{field}\" LIKE '%FI%'" in sql


def test_search_io_defs_rejects_unknown_table():
    ac, _ = make_conn()
    with pytest.raises(ValueError, match="table must be one of"):
        ac.search_io_defs("FI", table="IP_AnalogDef; DROP TABLE X")


def test_search_io_defs_rejects_unknown_field():
    ac, _ = make_conn()
    with pytest.raises(ValueError, match="field must be one of"):
        ac.search_io_defs("FI", field='NAME" OR 1=1 --')
