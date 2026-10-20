"""Operator interventions: who changes what, how often, and when loops go manual.

Works on normalized events from :mod:`mfgkit.eventlog`. An intervention is a
``change`` or ``mode`` event made by a person. Batch, sequence and system
accounts also write changes, so ``system_users`` (a regex matched against the
``user`` column, ignoring case) leaves them out; tune it per site. Rows with
no user are kept, since it can't be told who made them.
"""

from __future__ import annotations

import re

import pandas as pd

DEFAULT_SYSTEM_USERS = r"^(system|local system|nt authority\\.*|.*\$|batch.*|.*service.*|sfc.*|seq.*)$"
MANUAL_MODES = ("MAN", "MANL", "IMAN", "LO", "ROUT")  # MANL is iFIX's manual


def operator_actions(events: pd.DataFrame, system_users: str | None = DEFAULT_SYSTEM_USERS) -> pd.DataFrame:
    """``change`` and ``mode`` events, without those made by system accounts."""
    missing = {"time", "event_type"} - set(events.columns)
    if missing:
        raise KeyError(f"events is missing columns {sorted(missing)}")
    actions = events.loc[events["event_type"].isin(["change", "mode"])]
    if system_users and "user" in actions.columns:
        users = actions["user"].astype("string")
        is_system = users.str.fullmatch(system_users, case=False).fillna(False).astype(bool)
        actions = actions.loc[~is_system]
    return actions


def shift_start(times: pd.Series, shift_starts) -> pd.Series:
    """When the shift each timestamp falls in began.

    ``shift_starts`` are times of day, e.g. ``('06:00', '18:00')``. A time
    before the day's first start belongs to the previous day's last shift.
    """
    offsets = sorted(pd.Timedelta(f"{s}:00" if s.count(":") == 1 else s) for s in shift_starts)
    if not offsets:
        raise ValueError("shift_starts is empty")
    days = times.dt.normalize()
    tod = times - days
    pos = pd.Series(offsets).searchsorted(tod, side="right") - 1
    before_first = pos < 0
    chosen = pd.to_timedelta([offsets[p] for p in pos.clip(min=0)])
    result = days + pd.Series(chosen, index=times.index)
    result[before_first] = days[before_first] - pd.Timedelta("1D") + offsets[-1]
    return result


def intervention_summary(
    events: pd.DataFrame,
    by=("area",),
    shift_starts=None,
    system_users: str | None = DEFAULT_SYSTEM_USERS,
) -> pd.DataFrame:
    """Operator actions per group (and per shift when ``shift_starts`` is given).

    Columns: the ``by`` columns, ``shift_start`` if requested, then
    ``actions``, ``mode_changes``, ``modules`` (distinct) and ``users`` (distinct).
    """
    actions = operator_actions(events, system_users).copy()
    keys = list(by)
    if shift_starts:
        actions["shift_start"] = shift_start(actions["time"], shift_starts)
        keys.append("shift_start")
    if not keys:
        raise ValueError("Give at least one 'by' column or shift_starts.")
    actions["_mode"] = actions["event_type"] == "mode"
    grouped = actions.groupby(keys, dropna=False)
    out = pd.DataFrame(
        {
            "actions": grouped.size(),
            "mode_changes": grouped["_mode"].sum().astype("int64"),
            "modules": grouped["module"].nunique(),
            "users": grouped["user"].nunique(),
        }
    )
    return out.reset_index()


def top_modules(events: pd.DataFrame, n: int = 10, system_users: str | None = DEFAULT_SYSTEM_USERS) -> pd.DataFrame:
    """The ``n`` modules operators adjust most, with their share of all actions."""
    actions = operator_actions(events, system_users)
    total = len(actions)
    grouped = actions.groupby("module", dropna=False)
    out = pd.DataFrame(
        {
            "actions": grouped.size(),
            "mode_changes": grouped["event_type"].apply(lambda s: int((s == "mode").sum())),
            "users": grouped["user"].nunique(),
        }
    )
    out = out.sort_values("actions", ascending=False, kind="stable").head(n).reset_index()
    out["share"] = out["actions"] / total if total else 0.0
    return out


def manual_mode_changes(
    events: pd.DataFrame,
    modes=MANUAL_MODES,
    system_users: str | None = DEFAULT_SYSTEM_USERS,
) -> pd.DataFrame:
    """Mode changes that put a module into one of ``modes`` (MAN, MANL, IMAN, LO, ROUT).

    The new mode is read from ``value``; rows without one fall back to the
    descriptions, which is less reliable ("from MAN to AUTO" also matches).
    Mode names match whole words and case, as DeltaV writes them.
    """
    actions = operator_actions(events, system_users)
    modes_ = actions.loc[actions["event_type"] == "mode"]
    pattern = r"\b(?:" + "|".join(re.escape(m) for m in modes) + r")\b"

    def text(col):
        if col not in modes_.columns:
            return pd.Series(pd.NA, index=modes_.index, dtype="string")
        return modes_[col].astype("string")

    descriptions = (text("description").fillna("") + " " + text("description2").fillna("")).str.strip()
    new_mode = text("value").fillna(descriptions)
    return modes_.loc[new_mode.str.contains(pattern, regex=True).fillna(False).astype(bool)]
