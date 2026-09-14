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
| `mfgkit.analysis`    | *system-agnostic*                         | Downtime flagging/masking, shared routines |

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
