"""Tests for mfgkit.analysis.alarms and mfgkit.analysis.interventions."""

import pandas as pd
import pytest

from mfgkit.analysis import alarms, interventions
from mfgkit.eventlog import OUTPUT_COLUMNS

T0 = pd.Timestamp("2026-10-01 00:00")


def events(rows):
    """rows: (minutes after T0, event_type, module[, extra columns dict])."""
    records = []
    for row in rows:
        minutes, event_type, module, *extra = row
        rec = {c: pd.NA for c in OUTPUT_COLUMNS}
        rec.update(time=T0 + pd.Timedelta(minutes=minutes), event_type=event_type, module=module)
        if extra:
            rec.update(extra[0])
        records.append(rec)
    return pd.DataFrame(records, columns=list(OUTPUT_COLUMNS))


# --------------------------------------------------------------------------- #
# Alarm counts and floods
# --------------------------------------------------------------------------- #

def test_alarm_counts_includes_empty_buckets_and_ignores_non_alarms():
    ev = events([(1, "alarm", "A"), (2, "return", "A"), (25, "alarm", "B")])
    counts = alarms.alarm_counts(ev, T0, T0 + pd.Timedelta("30min"))
    assert list(counts) == [1, 0, 1]
    assert counts.index[1] == T0 + pd.Timedelta("10min")


def test_alarm_counts_empty():
    assert alarms.alarm_counts(events([(1, "change", "A")])).empty


def test_floods_merges_consecutive_windows():
    rows = [(0.5 * i, "alarm", "FLOODER") for i in range(24)]  # 12 min of alarms
    rows += [(15 + i * 0.1, "alarm", "OTHER") for i in range(3)]
    rows += [(45, "alarm", "LATE")]
    out = alarms.floods(events(rows), start=T0, end=T0 + pd.Timedelta("1h"))
    assert len(out) == 1
    flood = out.iloc[0]
    assert flood["start"] == T0
    assert flood["end"] == T0 + pd.Timedelta("10min")
    assert flood["alarms"] == 20 and flood["peak"] == 20
    assert flood["top_module"] == "FLOODER"


def test_floods_none():
    out = alarms.floods(events([(1, "alarm", "A")]))
    assert out.empty and "top_module" in out.columns


# --------------------------------------------------------------------------- #
# Bad actors, chattering, priority
# --------------------------------------------------------------------------- #

def test_bad_actors_share():
    ev = events([(i, "alarm", "A") for i in range(3)] + [(10, "alarm", "B")])
    out = alarms.bad_actors(ev, 1, by=("module",))
    assert out.to_dict("records") == [{"module": "A", "alarms": 3, "share": 0.75, "cumulative_share": 0.75}]


def test_chattering_finds_three_in_a_minute():
    ev = events(
        [(0, "alarm", "CHAT"), (0.2, "alarm", "CHAT"), (0.5, "alarm", "CHAT")]
        + [(0, "alarm", "SLOW"), (2, "alarm", "SLOW"), (4, "alarm", "SLOW")]
    )
    out = alarms.chattering(ev, by=("module",))
    assert out.to_dict("records") == [{"module": "CHAT", "alarms": 3, "max_in_window": 3}]


def test_priority_distribution():
    ev = events([(1, "alarm", "A", {"priority": "LOW"}), (2, "alarm", "B", {"priority": "LOW"}),
                 (3, "alarm", "C", {"priority": "HIGH"}), (4, "ack", "C", {"priority": "HIGH"})])
    out = alarms.priority_distribution(ev).set_index("priority")
    assert out.loc["LOW", "alarms"] == 2
    assert out.loc["HIGH", "share"] == pytest.approx(1 / 3)


def test_alarm_kpis():
    # 300 alarms over one day from 30 sources, spread evenly; 2 operator positions.
    rows = [(i * 4.8, "alarm", f"M{i % 30}") for i in range(300)]
    kpis = alarms.alarm_kpis(events(rows), T0, T0 + pd.Timedelta("1D"), operator_positions=2).set_index("metric")
    assert kpis.loc["Alarms per day per operator", "value"] == pytest.approx(150)
    assert kpis.loc["Alarms per day per operator", "meets"]
    assert kpis.loc["Max alarms in a 10-minute period", "value"] == 3
    assert kpis.loc["% of alarms from the top 10 sources", "value"] == pytest.approx(100 / 3)
    assert not kpis.loc["% of alarms from the top 10 sources", "meets"]
    assert kpis.loc["Chattering alarm sources", "value"] == 0


def test_alarm_kpis_validates_inputs():
    with pytest.raises(ValueError):
        alarms.alarm_kpis(events([]), T0, T0)
    with pytest.raises(ValueError):
        alarms.alarm_kpis(events([]), T0, T0 + pd.Timedelta("1D"), operator_positions=0)


def test_missing_columns_raise():
    with pytest.raises(KeyError):
        alarms.alarm_counts(pd.DataFrame({"time": []}))


# --------------------------------------------------------------------------- #
# Interventions
# --------------------------------------------------------------------------- #

def intervention_events():
    return events(
        [
            (60 * 5, "change", "FIC101", {"area": "A", "user": "JSMITH"}),       # 05:00 -> night shift
            (60 * 7, "mode", "FIC101", {"area": "A", "user": "JSMITH", "value": "MAN"}),
            (60 * 8, "mode", "FIC102", {"area": "A", "user": "BDOE", "value": "AUTO",
                                        "description": "Mode changed from MAN"}),
            (60 * 9, "mode", "TIC200", {"area": "B", "user": "BDOE", "description": "Mode set to IMAN"}),
            (60 * 10, "change", "FIC101", {"area": "A", "user": "BATCH_EXEC"}),
            (60 * 11, "change", "FIC101", {"area": "A", "user": "SYSTEM"}),
            (60 * 12, "alarm", "FIC101", {"area": "A"}),
            (60 * 19, "change", "TIC200", {"area": "B"}),                        # unknown user kept
        ]
    )


def test_operator_actions_drops_system_users_and_non_actions():
    out = interventions.operator_actions(intervention_events())
    assert len(out) == 5
    assert set(out["user"].dropna()) == {"JSMITH", "BDOE"}
    assert len(interventions.operator_actions(intervention_events(), system_users=None)) == 7


def test_shift_start():
    times = pd.Series(pd.to_datetime(["2026-10-01 05:00", "2026-10-01 06:00", "2026-10-01 19:00"]))
    out = interventions.shift_start(times, ("06:00", "18:00"))
    assert list(out) == list(pd.to_datetime(["2026-09-30 18:00", "2026-10-01 06:00", "2026-10-01 18:00"]))


def test_intervention_summary_by_area_and_shift():
    out = interventions.intervention_summary(intervention_events(), by=("area",), shift_starts=("06:00", "18:00"))
    rows = {(r["area"], r["shift_start"].hour): r for r in out.to_dict("records")}
    assert rows[("A", 18)]["actions"] == 1          # the 05:00 change, previous night shift
    assert rows[("A", 6)]["actions"] == 2
    assert rows[("A", 6)]["mode_changes"] == 2
    assert rows[("A", 6)]["users"] == 2
    assert rows[("B", 6)]["actions"] == 1
    assert rows[("B", 18)]["actions"] == 1


def test_top_modules():
    out = interventions.top_modules(intervention_events(), n=1)
    assert out.iloc[0]["module"] == "FIC101"
    assert out.iloc[0]["actions"] == 2
    assert out.iloc[0]["share"] == pytest.approx(2 / 5)


def test_manual_mode_changes_prefers_value_over_description():
    out = interventions.manual_mode_changes(intervention_events())
    assert list(out["module"]) == ["FIC101", "TIC200"]  # FIC102 went to AUTO
