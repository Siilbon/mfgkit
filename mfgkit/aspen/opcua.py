"""OPC UA node IDs for IP21 record fields.

IP21's OPC UA server addresses a field with an opaque (ByteString) node ID
built from the record ID, the field number, and the record's definition ID.
IT uses these to read IP21 values through IoTHub.

``encode_opcua_node_id`` is the pure encoding and needs no server;
``OpcUaMixin`` looks the IDs up in IP21 and encodes them. This is a port of
the ``Generate_OPCUA_NodeID`` SQLplus procedure.
"""

from __future__ import annotations

import base64
import struct

import pandas as pd

from .helpers import _chunks, _sanitize, _sanitize_field

DEFAULT_FIELD = "IP_INPUT_VALUE"

# Accepted as shorthand for the value field, as in the SQLplus procedure
_FIELD_ALIASES = {"MEASUREMENT": DEFAULT_FIELD}

# Namespace for raw vs. non-raw data access
_RAW_NAMESPACE = 3
_NON_RAW_NAMESPACE = 2

# Byte layout of the opaque ID, all little-endian: repeat-area flag, data
# access flag and record ID as 32-bit ints, two zero bytes, the 16-bit field
# number, then the 32-bit definition ID. 20 bytes, so 28 base64 characters.
_NODE_ID_LAYOUT = struct.Struct("<IIIxxHI")


def encode_opcua_node_id(record_id: int, field_id: int, definition_id: int,
                         repeat_area: bool = False, raw: bool = True) -> str:
    """Encode IP21 IDs as an OPC UA node ID like ``ns=3;b=AAAAAA...AA=``.

    ``record_id`` is the record's RECID, ``field_id`` the field number from
    FieldNameDef/FieldLongNameDef, and ``definition_id`` the RECID of the
    record's definition record. ``repeat_area`` is True for fields that live
    in a repeat area. ``raw`` picks the raw data access namespace (ns=3)
    rather than ns=2.
    """
    for name, value, bits in (("record_id", record_id, 32), ("field_id", field_id, 16),
                              ("definition_id", definition_id, 32)):
        if not 0 <= value < 2 ** bits:
            raise ValueError(f"{name} must fit in {bits} bits, got {value}")
    payload = _NODE_ID_LAYOUT.pack(int(repeat_area), 0 if raw else 1,
                                   record_id, field_id, definition_id)
    namespace = _RAW_NAMESPACE if raw else _NON_RAW_NAMESPACE
    return f"ns={namespace};b={base64.b64encode(payload).decode('ascii')}"


def _field_number(value) -> int:
    """Field number from a "Field_Number (hex)" cell, an int or a hex string."""
    if isinstance(value, str):
        return int(value.strip(), 16)
    return int(value)


class OpcUaMixin:
    """Look up the IDs behind OPC UA node IDs and encode them."""

    def opcua_node_ids(self, tag_list, field: str = DEFAULT_FIELD,
                       raw: bool = True) -> pd.DataFrame:
        """OPC UA node IDs for one field of each tag.

        Returns a DataFrame with columns ``NAME``, ``FIELD`` and ``NODE_ID``,
        one row per tag in the order given. ``NODE_ID`` is missing (NaN) for a
        tag with no IP21 record. Raises ValueError for an unknown field.
        """
        tags = self._normalize_tags(tag_list)
        field = _FIELD_ALIASES.get(field.upper(), field)
        field = _sanitize_field(field)

        field_id = self._opcua_field_id(field)
        repeat_area = self._opcua_repeat_area(field)
        records = self._opcua_records(tags)

        node_ids = []
        for tag in tags:
            record = records.get(tag.upper())
            node_ids.append(None if record is None else encode_opcua_node_id(
                record[0], field_id, record[1], repeat_area=repeat_area, raw=raw))
        return pd.DataFrame({"NAME": tags, "FIELD": field, "NODE_ID": node_ids})

    def opcua_node_id(self, tag: str, field: str = DEFAULT_FIELD, raw: bool = True) -> str:
        """OPC UA node ID for one tag's field. Raises ValueError if the tag doesn't exist."""
        node_id = self.opcua_node_ids([tag], field, raw)["NODE_ID"].iloc[0]
        if pd.isna(node_id):
            raise ValueError(f"No IP21 record named {tag!r}")
        return node_id

    def _opcua_field_id(self, field: str) -> int:
        # Long names first, then short names, as the SQLplus procedure does
        for table in ("FieldLongNameDef", "FieldNameDef"):
            df = self._execute_df(f"""
                SELECT "Field_Number (hex)"
                FROM {table}
                WHERE NAME = '{field}'
            """)
            if not df.empty and pd.notna(df.iloc[0, 0]):
                return _field_number(df.iloc[0, 0])
        raise ValueError(f"Unknown IP21 field: {field!r}")

    def _opcua_repeat_area(self, field: str) -> bool:
        # A field is treated as fixed-area if any definition has it outside a
        # repeat area (index 0), matching the SQLplus procedure
        df = self._execute_df(f"""
            SELECT DISTINCT REPEAT_AREA_INDEX
            FROM DefinitionDef
            WHERE FIELD_NAME_RECORD LIKE '{field}'
        """)
        indexes = pd.to_numeric(df.iloc[:, 0], errors="coerce") if not df.empty else []
        return not any(i == 0 for i in indexes)

    def _opcua_records(self, tags: list[str]) -> dict[str, tuple[int, int]]:
        """(RECID, definition RECID) per upper-cased tag name, for tags that exist."""
        rows = self._records_by_name(tags, "RECID, DEFINITION")
        if rows.empty:
            return {}

        # DEFINITION comes back as the definition record's name; look up its ID.
        # Keep it if the driver already returned a number.
        definitions = rows["DEFINITION"]
        def_names = sorted({d.strip() for d in definitions if isinstance(d, str)})
        def_ids = {}
        if def_names:
            found = self._records_by_name([_sanitize(d) for d in def_names], "RECID")
            def_ids = dict(zip(found["NAME"].astype(str).str.strip().str.upper(), found["RECID"]))

        records = {}
        for name, recid, definition in zip(rows["NAME"], rows["RECID"], definitions):
            def_id = def_ids.get(definition.strip().upper()) if isinstance(definition, str) else definition
            if def_id is not None and pd.notna(def_id):
                records[str(name).strip().upper()] = (int(recid), int(def_id))
        return records

    def _records_by_name(self, names: list[str], columns: str) -> pd.DataFrame:
        frames = []
        for chunk in _chunks(names, self.chunk_size):
            in_list = ", ".join(f"'{n}'" for n in chunk)
            frames.append(self._execute_df(f"""
                SELECT NAME, {columns}
                FROM All_Records
                WHERE NAME IN ({in_list})
            """))
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
