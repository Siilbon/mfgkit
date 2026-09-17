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
from mfgkit.eventlog import EventLogMapping, suggest_mapping

schema = json.load(open('event_chronicle_schema.json'))
mapping = suggest_mapping(schema['dbo.Events']['columns'], 'dbo.Events')
mapping.to_json('event_chronicle_mapping.json')   # check every column before relying on it
```

**3. Fetch normalized events** with an `event_type` of `alarm`, `return`, `ack`,
`change`, `mode`, `config` or `other`:

```python
from mfgkit.deltav.events import EventChronicle
from mfgkit.intellution.alarms import IfixAlarmLog

mapping = EventLogMapping.from_json('event_chronicle_mapping.json')
with EventChronicle(conn_str, mapping) as ec:
    events = ec.fetch(start, end)
```

Event types come from regex rules over each row's category, state and
description; `DELTAV_RULES` and `IFIX_RULES` are starting points, and a
mapping's own `rules` (optionally aimed at one column, e.g.
`["return", "^OK$", "state"]`) replace them. Check a day of results against the
operator stations before trusting the classification.

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
