# aspen

Aspen InfoPlus.21 (IP21) data access via the AspenTech SQLplus ODBC driver.

`AspenConn` wraps an IP21 connection and exposes safe, chunked, pandas-friendly
queries for live values, history, aggregates, and definition/metadata tables.

## Quick start

```python
from aspen import AspenConn

with AspenConn("MYIP21HOST") as ip21:
    wide = ip21.current(["FI-101.PV", "TI-202.PV"], hours=4)  # history, wide
    snap = ip21.snapshot(["FI-101.PV"])                       # latest value+quality
    if ip21.is_alive:
        ...
```

The public surface is unchanged from the original single-file module: import
`AspenConn`, `ConnStatus`, and `DBError` straight from `aspen`.

## Layout

The class is assembled from focused mixins so each file owns one concern. The
MRO is `AspenConn → AspenConnBase → HealthMixin → HistoryMixin → MetadataMixin → OpcUaMixin`.

| Module          | Contents                                                                       |
|-----------------|--------------------------------------------------------------------------------|
| `helpers.py`    | Constants, lazy `pyodbc`/`DBError`, pure helpers (`_sanitize`, `_ts_literal`, `_chunks`, `_is_good_quality`) |
| `base.py`       | `AspenConnBase` — connection lifecycle, reconnect/close, context manager, `_execute_df`, `_normalize_tags` |
| `health.py`     | `ConnStatus` + `HealthMixin` — `status`, `is_alive`, `snapshot`, `io_task_status` (alias `iostatus`) |
| `history.py`    | `HistoryMixin` — `start_end`, `current`, `interpolated`, `aggregates`          |
| `metadata.py`   | `MetadataMixin` — `search_tags` (AnalogDef/DiscreteDef, any field), `ip_analog`, `ip_discrete`, `iogethistdef`, `iogetdef`, raw `query` |
| `opcua.py`      | `OpcUaMixin` — `opcua_node_ids`, `opcua_node_id`; pure `encode_opcua_node_id` |
| `connection.py` | `AspenConn`, assembled from the base + mixins                                  |
| `__init__.py`   | Package docstring + re-exports                                                 |

## OPC UA node IDs

IT reads IP21 through IoTHub using OPC UA node IDs. `opcua_node_ids` looks up
each tag's record and definition IDs plus the field number and encodes them
(a port of the `Generate_OPCUA_NodeID` SQLplus procedure):

```python
with AspenConn("MYIP21HOST") as ip21:
    ip21.opcua_node_id("FI-101.PV")                  # 'ns=3;b=...='
    ip21.opcua_node_ids(["FI-101.PV", "TI-202.PV"])  # NAME, FIELD, NODE_ID
```

`field` defaults to `IP_INPUT_VALUE` (`MEASUREMENT` is accepted as an alias);
`raw=False` gives the ns=2 node ID instead of the raw ns=3 one.

## Design notes

- **Safety** — tags and timestamps are validated/escaped (`_sanitize`,
  `_ts_literal`); the only unguarded path is the deliberate `query()` escape
  hatch, where the caller owns safety.
- **Robustness** — `SET MAX_ROWS` avoids silent truncation, large tag lists are
  chunked (`chunk_size`) and concatenated transparently, and `_execute_df`
  reconnects once on a dropped connection before raising.
- **Shape** — history methods return a wide frame (TS index × tag columns);
  `aggregates` returns long format (one row per tag/period); `snapshot` is
  indexed by tag `NAME`.
- **Testability** — `pyodbc` is imported lazily and the connection factory is
  injectable via `connect_fn`, so the package imports and tests without the
  driver or a live server.

## Tests

```bash
python -m unittest test_aspen -v
```

Runs without `pyodbc` or a live IP21 server via an injected fake connection.
