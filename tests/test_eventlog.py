"""Tests for mfgkit.eventlog and its vendor readers, using a fake ODBC connection."""

import json
from collections import namedtuple
from datetime import datetime

import pandas as pd
import pytest

from mfgkit.deltav.events import EventChronicle
from mfgkit.eventlog import (
    DELTAV_RULES,
    IFIX_RULES,
    OUTPUT_COLUMNS,
    EventLogMapping,
    build_query,
    classify,
    discover,
    suggest_mapping,
)
from mfgkit.eventlog.__main__ import main as cli_main
from mfgkit.intellution.alarms import IfixAlarmLog

DELTAV_COLUMNS = [
    "Ord", "Date_Time", "Event_Type", "Category", "Area", "Node", "Unit", "Module",
    "Module_Description", "Attribute", "State", "Level", "Desc1", "Desc2",
]
IFIX_COLUMNS = [
    "ALM_NATIVETIMEIN", "ALM_TAGNAME", "ALM_TAGDESC", "ALM_VALUE", "ALM_UNIT",
    "ALM_ALMSTATUS", "ALM_ALMPRIORITY", "ALM_ALMAREA", "ALM_OPNAME", "ALM_MSGTYPE", "ALM_DESCR",
]

TableRow = namedtuple("TableRow", "table_cat table_schem table_name table_type")
ColumnRow = namedtuple("ColumnRow", "column_name type_name")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        columns, rows = self.conn.results.pop(0)
        self.description = [(c,) for c in columns]
        self._rows = rows
        return self

    def fetchall(self):
        return self._rows

    def tables(self, tableType=None):
        self._rows = self.conn.tables
        return self

    def columns(self, table=None, schema=None):
        self._rows = self.conn.columns[table]
        return self

    def close(self):
        pass


class FakeConn:
    def __init__(self, results=(), tables=(), columns=None):
        self.results = list(results)
        self.tables = list(tables)
        self.columns = columns or {}
        self.executed = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def deltav_mapping(**kw):
    return suggest_mapping(DELTAV_COLUMNS, "dbo.Journal", **kw)


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #

def test_suggest_mapping_deltav():
    m = deltav_mapping()
    assert m.columns["time"] == "Date_Time"
    assert m.columns["source_type"] == "Event_Type"
    assert m.columns["parameter"] == "Attribute"
    assert m.columns["priority"] == "Level"
    assert m.columns["description"] == "Desc1"
    assert m.columns["description2"] == "Desc2"


def test_suggest_mapping_ifix_skips_engineering_units():
    m = suggest_mapping(IFIX_COLUMNS, "FIXALARMS")
    assert m.columns["time"] == "ALM_NATIVETIMEIN"
    assert m.columns["module"] == "ALM_TAGNAME"
    assert m.columns["state"] == "ALM_ALMSTATUS"
    assert m.columns["user"] == "ALM_OPNAME"
    assert "unit" not in m.columns


def test_suggest_mapping_needs_a_time_column():
    with pytest.raises(ValueError):
        suggest_mapping(["Module", "State"], "dbo.Things")


def test_mapping_json_round_trip(tmp_path):
    m = EventLogMapping("dbo.Events", {"time": "Date/Time"}, rules=[["return", "^OK$", "state"], ["alarm", "ALM"]])
    path = tmp_path / "m.json"
    m.to_json(path)
    assert json.loads(path.read_text())["rules"] == [["return", "^OK$", "state"], ["alarm", "ALM"]]
    back = EventLogMapping.from_json(path)
    assert back == m


@pytest.mark.parametrize(
    "table, columns",
    [
        ("dbo.Events; DROP TABLE x", {"time": "Date_Time"}),
        ("dbo.Events", {"time": "Date_Time]; --"}),
        ("dbo.Events", {"time": "x'"}),
        ("a.b.c.d", {"time": "Date_Time"}),
    ],
)
def test_mapping_rejects_unsafe_names(table, columns):
    with pytest.raises(ValueError):
        EventLogMapping(table, columns)


def test_mapping_rejects_unknown_columns_and_rules():
    with pytest.raises(ValueError):
        EventLogMapping("t", {"time": "T", "colour": "C"})
    with pytest.raises(ValueError):
        EventLogMapping("t", {"time": "T"}, rules=[["alarmish", "x"]])
    with pytest.raises(ValueError):
        EventLogMapping("t", {"time": "T"}, rules=[["alarm", "x", "nope"]])
    with pytest.raises(ValueError):
        EventLogMapping("t", {}, rules=None)


# --------------------------------------------------------------------------- #
# Query building
# --------------------------------------------------------------------------- #

def test_build_query_is_parameterized():
    m = EventLogMapping("dbo.Events", {"time": "Date_Time", "area": "Area", "module": "Module"})
    sql, params = build_query(m, {"area": "AREA_A", "module": ["FIC101", "FIC102"]})
    assert sql == (
        "SELECT [Date_Time] AS [time], [Area] AS [area], [Module] AS [module] "
        "FROM [dbo].[Events] WHERE [Date_Time] >= ? AND [Date_Time] < ? "
        "AND [Area] IN (?) AND [Module] IN (?, ?) ORDER BY [Date_Time]"
    )
    assert params == ["AREA_A", "FIC101", "FIC102"]


def test_build_query_rejects_unmapped_filter():
    m = EventLogMapping("dbo.Events", {"time": "Date_Time"})
    with pytest.raises(KeyError):
        build_query(m, {"area": "A"})


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def test_deltav_rules():
    df = pd.DataFrame(
        {
            "source_type": ["ALARM", "ALARM", "ALARM", "CHANGE", "CHANGE", "EVENT", "STATUS"],
            "state": ["ACT/UNACK", "ACT/ACK", "INACT/ACK", None, None, None, None],
            "parameter": [None, None, None, "SP", "MODE.TARGET", None, None],
            "description": [None, None, None, None, None, "Download complete", "Node up"],
        }
    )
    # ACT/ACK is an 'alarm' here; EventChronicle finds acks from each alarm's history.
    assert list(classify(df, DELTAV_RULES)) == [
        "alarm", "alarm", "return", "change", "mode", "config", "other",
    ]


def test_ifix_rules():
    df = pd.DataFrame(
        {
            "source_type": ["ALARM", "ALARM", "ACK", "OPERATOR", "OPERATOR", "OPERATOR", "EVENT", "NETWORK"],
            "state": ["CFN", "OK", "HIHI", None, None, None, None, "OK"],
            "description": [
                "Tank high high", "Tank high high", None,
                "[N1 ] Fix32.N1.FIC101.A_AUTO set to MANL by ST01::OPERATOR A",
                "[N1 ] Fix32.N1.FIC101.F_CV set to 50 by ST01::OPERATOR A",
                "ST02 VIEW logged in as Application User",
                "UNIT 1 AUTO/MAN",
                "Connection is established with node N2",
            ],
        }
    )
    assert list(classify(df, IFIX_RULES)) == [
        "alarm", "return", "ack", "mode", "change", "other", "other", "other",
    ]


def test_dict_rule_needs_every_condition():
    df = pd.DataFrame({"source_type": ["ALARM", "NETWORK"], "state": ["OK", "OK"]})
    rules = [("return", {"source_type": "^ALARM$", "state": "^OK$"})]
    assert list(classify(df, rules)) == ["return", "other"]
    m = EventLogMapping("t", {"time": "T"}, rules=[["return", {"source_type": "^ALARM$", "state": "^OK$"}]])
    assert EventLogMapping.from_dict(json.loads(json.dumps(m.to_dict()))) == m
    with pytest.raises(ValueError):
        EventLogMapping("t", {"time": "T"}, rules=[["return", {"nope": "x"}]])


def test_suggest_mapping_ifix_alarm_odbc_layout():
    columns = [
        "DATETIME", "DATELAST", "TIMELAST", "NODE", "TAGNAME", "DESCRIPTION", "VALUE", "EGU",
        "ALARMSTATUS", "MESSAGETYPE", "ALARMUNIT", "OPERATOR", "OPERATORNODE", "AREA",
        "OPERATORAREA", "UniqueIdentifier",
    ]
    m = suggest_mapping(columns, "dbo.FIXALARMS")
    assert m.columns["time"] == "DATETIME"
    assert m.columns["source_type"] == "MESSAGETYPE"
    assert m.columns["state"] == "ALARMSTATUS"
    assert m.columns["module"] == "TAGNAME"
    assert m.columns["unit"] == "ALARMUNIT"
    assert m.columns["user"] == "OPERATOR"
    assert "EGU" not in m.columns.values()


def test_ifix_fetch_parses_operator_writes():
    m = EventLogMapping(
        "dbo.FIXALARMS",
        {"time": "DATETIME", "source_type": "MESSAGETYPE", "state": "ALARMSTATUS", "node": "NODE",
         "module": "TAGNAME", "description": "DESCRIPTION", "value": "VALUE", "user": "OPERATOR"},
    )
    cols = ["time", "source_type", "state", "node", "module", "description", "value", "user"]
    pad = " " * 8
    rows = [
        (datetime(2026, 10, 20, 14, 26, 10), "OPERATOR   ", pad, "N1  ", pad,
         "[N1      ] Fix32.N1.UNIT1_AMC.F_CV set to 0 by ST03::OPERATOR B1   ", pad, "OPB1   "),
        (datetime(2026, 10, 20, 14, 26, 11), "EVENT", pad, "N1", "UNIT1_AMC", "UNIT 1 AUTO/MAN", "0", pad),
        (datetime(2026, 10, 20, 14, 26, 14), "ALARM", "OK ", "N1", "XV200_A", "SUPPLY TK FWD", "OK", pad),
    ]
    conn = FakeConn(results=[(cols, rows)])
    events = IfixAlarmLog("DSN=x", m, connect_fn=lambda: conn).fetch("2026-10-20", "2026-10-21")
    assert list(events["event_type"]) == ["change", "other", "return"]
    write = events.iloc[0]
    assert (write["module"], write["parameter"], write["value"], write["user"]) == ("UNIT1_AMC", "F_CV", "0", "OPB1")
    assert pd.isna(events.loc[1, "user"])  # blank padding becomes NA


def test_classify_without_column_searches_combined_text_case_insensitively():
    df = pd.DataFrame({"category": ["process"], "description": ["x"]})
    assert list(classify(df, [("alarm", "PROCESS")])) == ["alarm"]


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def test_fetch_normalizes_and_classifies():
    m = EventLogMapping(
        "dbo.Journal",
        {"time": "Date_Time", "source_type": "Event_Type", "state": "State", "module": "Module"},
    )
    rows = [
        (datetime(2026, 10, 1, 8, 5), "ALARM ", "INACT/ACK", "FIC101  "),
        (datetime(2026, 10, 1, 8, 0), "ALARM", "ACT/UNACK", "FIC101"),
        (None, "ALARM", "ACT/UNACK", "BAD"),
    ]
    conn = FakeConn(results=[(["time", "source_type", "state", "module"], rows)])
    with EventChronicle("DSN=x", m, connect_fn=lambda: conn) as ec:
        events = ec.fetch("2026-10-01", "2026-10-02", module="FIC101")

    assert conn.closed
    sql, params = conn.executed[0]
    assert params == [datetime(2026, 10, 1), datetime(2026, 10, 2), "FIC101"]
    assert list(events.columns) == list(OUTPUT_COLUMNS)
    assert list(events["event_type"]) == ["alarm", "return"]  # sorted, bad time dropped
    assert list(events["module"]) == ["FIC101", "FIC101"]  # padding stripped
    assert (events["source"] == "deltav").all()
    assert events["area"].isna().all()


def test_mapping_rules_replace_defaults_and_path_is_accepted(tmp_path):
    path = tmp_path / "m.json"
    EventLogMapping("FIXALARMS", {"time": "T", "state": "S"}, rules=[["return", "^NORMAL$", "state"]]).to_json(path)
    conn = FakeConn(results=[(["time", "state"], [(datetime(2026, 1, 1), "NORMAL")])])
    log = IfixAlarmLog("DSN=x", path, connect_fn=lambda: conn)
    events = log.fetch("2026-01-01", "2026-01-02")
    assert list(events["event_type"]) == ["return"]
    assert list(events["source"]) == ["ifix"]


def test_fetch_rejects_backwards_window():
    conn = FakeConn()
    ec = EventChronicle("DSN=x", deltav_mapping(), connect_fn=lambda: conn)
    with pytest.raises(ValueError):
        ec.fetch("2026-01-02", "2026-01-01")


# --------------------------------------------------------------------------- #
# Discovery and CLI
# --------------------------------------------------------------------------- #

def _discovery_conn():
    return FakeConn(
        results=[(DELTAV_COLUMNS[:2], [(1, datetime(2026, 10, 1, 8, 0))])],
        tables=[
            TableRow("EJournal", "dbo", "Journal", "TABLE"),
            TableRow("EJournal", "dbo", "Users", "TABLE"),
        ],
        columns={
            "Journal": [ColumnRow(c, "varchar") for c in DELTAV_COLUMNS],
            "Users": [ColumnRow("Name", "varchar"), ColumnRow("Created", "datetime")],
        },
    )


def test_discover_finds_event_tables_only():
    conn = _discovery_conn()
    found = discover(conn, sample_rows=1)
    assert list(found) == ["dbo.Journal"]
    info = found["dbo.Journal"]
    assert info["suggested_mapping"]["columns"]["time"] == "Date_Time"
    assert info["sample"] == [{"Ord": 1, "Date_Time": "2026-10-01T08:00:00"}]
    assert conn.executed[0][0] == "SELECT TOP 1 * FROM [dbo].[Journal]"
    assert not conn.closed  # caller's connection stays open


def test_cli_discover_writes_json(tmp_path, capsys):
    out = tmp_path / "schema.json"
    rc = cli_main(
        ["discover", "DSN=x", "--out", str(out), "--rows", "1"],
        discover_fn=lambda conn_str, **kw: discover(_discovery_conn(), **kw),
    )
    assert rc == 0
    assert "dbo.Journal" in json.loads(out.read_text())
    assert "dbo.Journal" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# DeltaV alarm state transitions and exported files
# --------------------------------------------------------------------------- #

def _alarm_rows(states, module="FIC101", parameter="HI_ALM"):
    from mfgkit.deltav.events import alarm_transitions

    df = pd.DataFrame(
        {
            "time": pd.date_range("2026-10-20", periods=len(states), freq="s"),
            "module": module,
            "parameter": parameter,
            "state": states,
            "event_type": classify(pd.DataFrame({"state": states}), DELTAV_RULES),
        }
    )
    return list(alarm_transitions(df)["event_type"])


def test_alarm_transitions_ack_required():
    assert _alarm_rows(["ACT/UNACK", "ACT/ACK", "INACT/ACK"]) == ["alarm", "ack", "return"]
    assert _alarm_rows(["ACT/UNACK", "INACT/UNACK", "INACT/ACK"]) == ["alarm", "return", "ack"]


def test_alarm_transitions_no_ack_required_counts_every_activation():
    # Advisory priorities come in already acknowledged.
    states = ["ACT/ACK", "INACT/ACK", "ACT/ACK", "INACT/ACK"]
    assert _alarm_rows(states) == ["alarm", "return", "alarm", "return"]


def test_alarm_transitions_follow_each_alarm_separately():
    from mfgkit.deltav.events import alarm_transitions

    df = pd.DataFrame(
        {
            "module": ["A", "B", "A", "B"],
            "parameter": ["HI_ALM"] * 4,
            "state": ["ACT/UNACK", "INACT/ACK", "ACT/ACK", "ACT/ACK"],
            "event_type": ["alarm", "return", "ack", "ack"],
        }
    )
    assert list(alarm_transitions(df)["event_type"]) == ["alarm", "return", "ack", "alarm"]


DELTAV_EXPORT = (
    "Date/Time*\tEvent Type*\tCategory*\tArea\tNode\tUnit\tModule\tModule Description\t"
    "Parameter\tState\tLevel\tDesc1\tDesc2\n"
    "1\t10/20/2026 2:13:05.693 PM\tALARM\tSYSTEM\tAREA_B\tCP02\t\tPDI400\tCollector dP\t"
    "ALARM1\tINACT/ACK\tADVISORY\tANY\tAny Alarm Value %P1\n"
    "2\t10/20/2026 2:13:03.305 PM\tALARM\tPROCESS\tAREA_A\tCP01\tSILOS\tLI500\tSILO LEVEL\t"
    "HI_ALM\tACT/UNACK\tWARNING\tHIGH\tHigh Alarm Value 90.1341 Limit 90\n"
    "3\t10/20/2026 2:13:01.693 PM\tALARM\tSYSTEM\tAREA_B\tCP02\t\tPDI400\tCollector dP\t"
    "ALARM1\tACT/ACK\tADVISORY\tANY\tAny Alarm Value %P1\n"
)


def test_deltav_read_export(tmp_path):
    path = tmp_path / "chronicle.txt"
    path.write_text(DELTAV_EXPORT, encoding="utf-8")
    events = EventChronicle.read_export(path)
    assert list(events.columns) == list(OUTPUT_COLUMNS)
    assert list(events["time"]) == [
        pd.Timestamp("2026-10-20 14:13:01.693"),
        pd.Timestamp("2026-10-20 14:13:03.305"),
        pd.Timestamp("2026-10-20 14:13:05.693"),
    ]
    assert list(events["module"]) == ["PDI400", "LI500", "PDI400"]
    assert list(events["event_type"]) == ["alarm", "alarm", "return"]
    assert list(events["priority"]) == ["ADVISORY", "WARNING", "ADVISORY"]
    assert pd.isna(events.loc[0, "unit"])
    assert (events["source"] == "deltav").all()


def test_ifix_read_export_treats_null_text_as_blank(tmp_path):
    path = tmp_path / "fixalarms.csv"
    path.write_text(
        "DATETIME,NODE,TAGNAME,DESCRIPTION,VALUE,ALARMSTATUS,MESSAGETYPE,OPERATOR\n"
        "2026-10-20 14:24:47.770,N1,,[N1 ] Fix32.N1.LIC300_IVP.F_CV set to 100 by ST04::OPERATOR A1,,,OPERATOR,OPA1\n"
        "2026-10-20 14:24:47.000,NULL,LIC300_IVP,EVAP STEAM,100,NULL,NULL,OPA1\n",
        encoding="utf-8",
    )
    events = IfixAlarmLog.read_export(path)
    assert list(events["event_type"]) == ["other", "change"]
    assert events.loc[1, "module"] == "LIC300_IVP"
    assert pd.isna(events.loc[0, "source_type"])
