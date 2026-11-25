# mfgkit

A manufacturing toolkit for control-system data. One installable package
wrapping the four vendor-specific parsers, so downstream apps import from a
single source of truth instead of vendoring their own drifting copies.

| Subpackage           | System                                    | Reads                                     |
|----------------------|-------------------------------------------|-------------------------------------------|
| `mfgkit.aspen`       | Aspen InfoPlus.21 (IP21) historian        | Live values, history, aggregates via ODBC |
| `mfgkit.deltav`      | Emerson DeltaV DCS                        | Flat-file exports, FHX files              |
| `mfgkit.intellution` | Intellution / iFIX HMI                    | Tag database and PowerTool exports        |
| `mfgkit.plc`         | Allen-Bradley ControlLogix                | Tag CSVs, L5X rungs, logic graphs         |
| `mfgkit.eventlog`    | DeltaV Event Chronicle, iFIX alarm ODBC   | Alarm and event tables in SQL databases   |
| `mfgkit.analysis`    | *system-agnostic*                         | Downtime, ISA-18.2 alarm performance, operator interventions |
| `mfgkit.dynamics`    | *system-agnostic*                         | Self-regulating (FOPDT) and integrating process models, fitted to output/PV data |

Shared parsing helpers live in `mfgkit.utils`.

## Installation

```bash
pip install -e ".[dev]"           # development
pip install "mfgkit[all]"         # everything
pip install "mfgkit[aspen]"       # historian access only (Windows)
```

Extras exist because the dependency profiles genuinely differ. `pyodbc` and the
AspenTech SQLplus driver are Windows-only, so they are **not** base dependencies
— `mfgkit.deltav` and `mfgkit.plc` stay installable and testable on macOS.

| Extra   | Pulls in                | Needed for                                    |
|---------|-------------------------|-----------------------------------------------|
| `aspen` | `pyodbc`                | `mfgkit.aspen` live connections                |
| `sql`   | `pyodbc`                | `mfgkit.eventlog` database connections          |
| `graph` | `networkx`, `plotly`    | `mfgkit.plc.graph`, `deltav.em` SFC graphs     |
| `excel` | `xlsxwriter`, `openpyxl`| `.to_excel()` on tags and rungs                |
| `dynamics` | `scipy`, `plotly`    | `mfgkit.dynamics` fitting and plots (models need numpy only) |

## Usage

```python
from mfgkit.aspen import AspenConn

with AspenConn("MYIP21HOST") as ip21:
    wide = ip21.current(["FI-101.PV", "TI-202.PV"], hours=4)
    snap = ip21.snapshot(["FI-101.PV"])
```

```python
from mfgkit.deltav.modules import load_modules
from mfgkit.deltav.alarm_config import load_sam_dir

modules = load_modules('MODT.TXT', 'ANT.TXT', 'DT.TXT')
```

```python
from mfgkit.intellution import IntellutionDB

db = IntellutionDB('WETMILL.csv')
db.lookup('FIC1234')
db.alarm_area('REFINERY')
```

```python
from mfgkit.plc import ControlLogix_Tags, ControlLogix_Rungs

tags  = ControlLogix_Tags('WM1_CLX_Tags.CSV')
rungs = ControlLogix_Rungs('WM1_CLX.L5X')

tags.lookup('MC61P09')          # alias search, substring or exact
tags.io_lookup(rack=11, mod=3)  # what's wired to rack 11, module 3
```

### Building an IO map

The common workflow: join alias entries that point at physical rack addresses
against the rungs that reference them, so you can see which IO is actually used.

```python
from mfgkit.plc import ControlLogix_Tags, ControlLogix_Rungs, get_io

tags  = ControlLogix_Tags('WT_CLX-Controller-Tags.CSV')
rungs = ControlLogix_Rungs('WT_CLX.L5X')

io = get_io(tags, rack_prefix=r'R\d{2}[A-Z]', rungs=rungs)
# 481 rows over 18 racks; adds rack, in_out, mod, bit,
# references, num_references, is_referenced

io[io['rack'] == 'R11B']       # everything on one rack
io[~io['is_referenced']]       # configured but never used in logic (124 here)
```

**Rack addressing is a per-controller convention**, and this is the parameter
you will get wrong first. One controller uses `R11B:I.Data[1].10`
(`rack_prefix=r'R\d{2}[A-Z]'`), the next uses `Rack_07:I.Slot[3].Data.2`
(`rack_prefix=r'Rack_\d{2}'`). A prefix that matches nothing returns an *empty
DataFrame*, not an error — so `get_io` logs a warning with real specifiers from
your file when that happens:

```
WARNING No alias specifiers matched rack_prefix 'R\d{3}\w'. This controller may
        use a different addressing convention. Example specifiers:
        ['R11B:I.Data[1].10', 'R11B:I.Data[1].8', ...]
```

Enable `logging.basicConfig(level=logging.WARNING)` to see it. Not every
controller has rack IO at all — one using produced/consumed mapped arrays
(`Manual_Control[156].9`) will legitimately return nothing.

Narrow the search or override the address pattern outright:

```python
io = get_io(
    tags,
    table_names=['ctrl', 'CornUpdate', 'DEVICE_LOGIC'],   # default: every table
    rack_regex=r'(?P<rack>Rack_\d{2}):(?P<in_out>[IO])\.Slot\[(?P<mod>\d*)\]\.Data\.(?P<bit>\w+)',
    scanners_df=pd.read_csv('io_scanners.csv'),           # optional, joined on 'rack'
)
```

Controllers that mix addressing styles need one pass per style, combined:

```python
frames = [get_io(tags, rack_regex=r) for r in (regex_slot, regex_colon, regex_data)]
io = frames[0].combine_first(frames[1]).combine_first(frames[2])
```

### Export to Excel

```python
tags.to_excel('tags.xlsx')      # one sheet per table in the export
rungs.to_excel('rungs.xlsx')
io.to_excel('io.xlsx', index=False)
```

### Logic graphs

```python
from mfgkit.plc import build_logic_graph, subgraph_around, plot_logic_graph_force

G = build_logic_graph(rungs.df, tags=tags)          # needs mfgkit[graph]
upstream = list(G.predecessors('MC61P09'))          # what drives this tag
plot_logic_graph_force(G, center_tag='T[521].DN', depth=3).show()
```

### Analysis helpers

`mfgkit.analysis` holds routines that are not specific to any one control
system — the place for shared helpers as you write them.

Historian pulls nearly always contain stretches the analysis should not see:
the line was down, a unit was in CIP, an instrument was isolated. Fitting or
trending across those quietly corrupts the result.

```python
from mfgkit.analysis import flag_downtime, mask_downtime, downtime_summary

mask = flag_downtime(df['F42103_PV'], threshold=125)   # True = downtime
up   = mask_downtime(df, 'F42103_PV', threshold=125, pad='120min')
```

A threshold crossing alone is **not** downtime — flow dips below a threshold
constantly from noise, brief upsets, and exception-reporting gaps. A stretch
only counts once it persists for `min_duration`, and each qualifying stretch is
then padded by `pad` on both sides so the startup and shutdown ramps around it
are excluded too.

| Parameter | Default | Meaning |
|---|---|---|
| `threshold` | *required* | Value to compare against |
| `direction` | `'below'` | `'below'` = downtime is a *low* reading (flow); `'above'` = a *high* one (a level backing up, a valve stuck open). `'lower'`/`'higher'` also accepted |
| `min_duration` | `'10min'` | How long a crossing must persist to count |
| `pad` | `'30min'` | Widens each stretch on both sides |

Stack it once per tag to narrow further each time:

```python
up = mask_downtime(df, 'F42103_PV', threshold=125, min_duration='0min', pad='120min')
up = mask_downtime(up, 'P42115_PV', threshold=15,  min_duration='0min', pad='60min')
```

Check what a mask actually removed before trusting it:

```python
downtime_summary(df['F42103_PV'], threshold=125)
#                 start                 end        duration
#   2026-01-02 09:20:00 2026-01-02 19:19:00 0 days 09:59:00
#   2026-01-08 15:20:00 2026-01-10 00:39:00 1 days 09:19:00
```

`contiguous_runs(mask)` is exposed separately — it yields `(start, end)` labels
for each True run in any boolean Series, not just downtime.

### Process dynamics

`mfgkit.dynamics` simulates process models from a recorded controller output
and fits them to plant data. The controller output is treated as the process
input, so it works on open-loop step tests and closed-loop trends alike.

```python
from mfgkit.dynamics import fit_self_regulating

fit = fit_self_regulating(
    df.index, df["TIC101.OUT"], df["TIC101.PV"],
    pv_range=(0, 150),        # degC transmitter span
    out_range=(0, 100),       # % output
)
fit.model.process_gain, fit.model.time_const, fit.model.dead_time   # %/%, s, s
fit.plot().show()                                 # model over data, one hover box
```

Every fit returns a `FitResult`. The fitted parameters live on its `model`;
the rest describes how good the fit is:

| Attribute | Meaning |
|---|---|
| `model` | The fitted `SelfRegulating` or `Integrating`, simulated over the data |
| `params` | Fitted parameters as a dict, name -> value. Only those the fit adjusted |
| `stderr` | Standard error of each fitted parameter, same keys. Large compared with the value means the data didn't pin it down |
| `r2` | Fraction of the PV's variance the model explains |
| `success`, `optimize` | Whether scipy converged, and its raw result |
| `plot()` | The model over the measured PV |

`params` makes tables across many fits a one-liner:
`pd.DataFrame([f.params for f in fits])`.

#### Self-regulating

`SelfRegulating` is a first-order-plus-dead-time (FOPDT) model:

    tau*dPV/dt + PV = PV_init + K*(OUT(t - theta) - OUT_init)

| Parameter | Units | Meaning |
|---|---|---|
| `process_gain` | %/% | % of PV span per % of output span, the dimensionless gain used for tuning. Negative if more output lowers the PV |
| `time_const` | s | Time to go 63% of the way once the PV starts moving |
| `dead_time` | s | Time from an output change until the PV starts moving |
| `pv_init` | EU | PV held at the starting output. The data should begin at steady state |
| `pv_range`, `out_range` | EU | `(low, high)` spans, used to convert to % internally |

It is a **local, linear** model around the starting operating point, not one
that assumes 0% output gives 0 PV. Real processes are rarely linear across their
whole range (a heater bottoms out at ambient, a valve's gain changes with
opening), so fit it near where the plant actually runs.

`p0=[K, tau, theta]` overrides the starting guess, and `bounds=(lower, upper)`
the limits, if a fit lands somewhere implausible.

#### Integrating

A level is the classic integrating process: after an output change the PV
doesn't settle, it ramps for as long as inflow and outflow are out of balance.

    dPV/dt = Ki*(OUT(t - theta) - OUT_bal)        (in % of span)

```python
from mfgkit.dynamics import fit_integrating

lvl = fit_integrating(
    df.index, df["LIC201.OUT"], df["LIC201.PV"],
    pv_range=(0, 4),          # m
)
lvl.params    # {"integrating_gain": ..., "dead_time": ..., "out_bal": ...}
lvl.stderr
```

| Parameter | Units | Meaning |
|---|---|---|
| `integrating_gain` | 1/s | Ramp rate in %/s per % the output sits off balance. Negative if more output lowers the PV (e.g. a drain valve) |
| `dead_time` | s | Time from an output change until the ramp changes |
| `out_bal` | EU | The output at which the PV holds still: inflow = outflow |
| `pv_init` | EU | PV at the start of the data |

`out_bal` is **fitted by default**. It moves with production rate and
conditions, so it says something about when the data was captured, and fixing it
even half a percent wrong noticeably skews `integrating_gain` and `dead_time`.
Pass `out_bal=` to fix it when you know it.

Step-test a level with **pulses** (move the output, then put it back) rather
than a held step, which would overflow or empty the tank. Pulses in both
directions, or a setpoint change up and back down, separate `integrating_gain`
from `out_bal`; check `stderr` if the output only moved one way. The fit
assumes `out_bal` is constant over the window, so keep windows short compared
with how often the load changes.

If a fit returns a large `dead_time` with the wrong-sign gain, it has paired
the wrong output move with the wrong ramp. Tighten the dead-time upper bound
with `bounds=`.

#### Data for either fit

`time` may be seconds or historian timestamps. Samples must not contain NaN,
so drop downtime and gaps first (see `mask_downtime` above). Fit near the
range the plant actually runs in; both models are linear.

### Alarm and event logs

DeltaV's Event Chronicle and iFIX's Alarm ODBC service log alarms and operator
changes to SQL tables whose names vary by version and site setup, so
`mfgkit.eventlog` maps them once instead of guessing. Use a read-only database
account; every query is a parameterized `SELECT`, and table and column names
from a mapping are validated before use.

**1. Discover the tables** (lists event-like tables, their columns, a few sample
rows and a suggested mapping):

```bash
python -m mfgkit.eventlog discover "DRIVER={ODBC Driver 18 for SQL Server};SERVER=APPSTATION\\SQLEXPRESS;DATABASE=...;Trusted_Connection=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly" --out event_chronicle_schema.json
```

**2. Save and review a mapping** of normalized columns to the table's columns:

```python
import json
from mfgkit.eventlog import EventLogMapping, suggest_mapping

schema = json.load(open('event_chronicle_schema.json'))
mapping = suggest_mapping(schema['dbo.Events']['columns'], 'dbo.Events')
mapping.to_json('event_chronicle_mapping.json')   # check every column before relying on it
```

**3. Fetch normalized events** with an `event_type` of `alarm`, `return`, `ack`,
`change`, `mode`, `config` or `other`:

```python
from mfgkit.eventlog import EventLogMapping
from mfgkit.deltav.events import EventChronicle
from mfgkit.intellution.alarms import IfixAlarmLog   # same interface for iFIX

mapping = EventLogMapping.from_json('event_chronicle_mapping.json')
with EventChronicle(conn_str, mapping) as ec:
    events = ec.fetch(start, end)                     # start <= time < end
    area_a = ec.fetch(start, end, area='AREA_A')      # filter on any mapped column
```

Saved exports (CSV, or the tab-delimited files the event viewers export) give
the same frame without a database; the mapping is guessed from the header
unless you pass one:

```python
events = EventChronicle.read_export('chronicle_export.txt')
events = IfixAlarmLog.read_export('fixalarms.csv', 'ifix_alarm_mapping.json')
```

Every frame has the same columns (`mfgkit.eventlog.OUTPUT_COLUMNS`): `time`,
`source_type`, `category`, `state`, `area`, `node`, `unit`, `module`,
`module_description`, `parameter`, `description`, `description2`, `value`,
`previous_value`, `priority`, `user`, `event_type` and `source`. Columns the
mapping doesn't cover are NA.

Event types come from regex rules over each row's category, state and
description; `DELTAV_RULES` and `IFIX_RULES` are starting points, and a
mapping's own `rules` (optionally aimed at one column, e.g.
`["return", "^OK$", "state"]`, or several columns that must all match, e.g.
`["return", {"source_type": "^ALARM$", "state": "^OK$"}]`) replace them. Check
a day of results against the operator stations before trusting the
classification.

For DeltaV, alarm rows are then re-typed from each alarm's State history:
priorities that need no acknowledgement log an alarm coming in as `ACT/ACK`,
so `ACT/ACK` only counts as an `ack` right after `ACT/UNACK`.

For iFIX, operator writes are `OPERATOR` rows whose tag is only in the message
(`Fix32.NODE.TAG.F_CV set to 1 by STATION::USER`); `IfixAlarmLog` fills
`module`, `parameter` and `value` from it. `EVENT` rows (tag value changes,
including the echo of each write) are `other`. Writes to a site's own
auto/manual tags can be counted as mode changes with a mapping rule such as
`["mode", {"source_type": "^OPERATOR$", "module": "_AMC?$"}]` placed before the
`change` rule.

**4. Analyze:**

```python
from mfgkit.analysis import alarms, interventions

alarms.alarm_kpis(events, start, end, operator_positions=3)   # rate, floods, top-10 share, chattering vs ISA-18.2
alarms.floods(events)            # flood periods (>10 alarms in a 10-minute window)
alarms.bad_actors(events, 10)    # most frequent alarms and their share
interventions.intervention_summary(events, by=('area',), shift_starts=('06:00', '18:00'))
interventions.top_modules(events, 10)       # loops operators adjust most
interventions.manual_mode_changes(events)   # modules put in MAN, IMAN, LO or ROUT
```

ISA-18.2 targets are per operator position. Rates are divided by
`operator_positions`, but flood metrics can't be, so filter `events` to one
console's areas before reading those.

Interventions are `change` and `mode` events by people; `system_users` (a regex)
leaves out system, batch and sequence accounts, and should be tuned per site.

## Design notes

**Everything is lazy.** `import mfgkit` resolves no subpackage, and
`mfgkit.plc` does not import `graph` (and therefore networkx) until you touch
it. `mfgkit.aspen` imports `pyodbc` only at connection time. This is what lets
one package span a Windows-only ODBC driver and pure-pandas file parsers
without splitting into separate distributions.

**No default tag on `mask_downtime`.** The original defaulted `flow_col` to
`'F42103_PV'`. In a toolkit used across units that would silently mask against
the wrong tag, so `tag_name` is now required and an unknown column raises with
the available ones listed.

**`read_stacked_csv` is the point of the consolidation.** Several control-system
exports are many tables concatenated into one file, each with its own header.
That function previously existed in three drifted copies with incompatible
signatures. It now lives once, in `mfgkit.utils`, with `encoding` passed through
`**kwargs` so older call sites keep working. `tests/test_imports.py` asserts
that `intellution` and `plc.tags` resolve to the same object.

## Tests

```bash
python -m pytest -q
```
