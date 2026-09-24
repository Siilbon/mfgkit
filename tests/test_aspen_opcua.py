"""Tests for OPC UA node ID encoding and lookup (mfgkit.aspen.opcua)."""

import random

import pytest

from mfgkit.aspen import encode_opcua_node_id

from test_aspen import make_conn

BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def sqlplus_node_id(record_id, field_id, definition_id, repeat_area, dar):
    """Step-for-step port of the Generate_OPCUA_NodeID SQLplus procedure."""
    s_repeat = format(repeat_area, "016b")
    s_dar = format(dar, "016b")
    s_record = format(record_id, "024b")
    s_field = format(field_id, "016b")
    s_def = format(definition_id, "016b")
    null = "0" * 8

    c1, c2 = s_repeat[:8], s_repeat[8:]
    c3, c4 = s_dar[:8], s_dar[8:]
    c11, c5, c6 = s_record[:8], s_record[8:16], s_record[16:]
    c7, c8 = s_field[:8], s_field[8:]
    c9, c10 = s_def[:8], s_def[8:]

    bits = (c2 + c1 + null) + (null + c4 + c3) + (null + null + c6) \
        + (c5 + c11 + null) + (null + null + c8) + (c7 + c10 + c9) + null * 3
    chars = [BASE64[int(bits[i:i + 6], 2)] for i in range(0, len(bits), 6)]
    namespace = "ns=3;" if dar == 0 else "ns=2;"
    # The procedure drops the 28th character and ends with the pad instead
    return namespace + "b=" + "".join(chars[:27]) + "="


@pytest.mark.parametrize("raw", [True, False])
@pytest.mark.parametrize("repeat_area", [True, False])
def test_encoding_matches_sqlplus_procedure(raw, repeat_area):
    rng = random.Random(21)
    for _ in range(200):
        record_id = rng.randrange(2 ** 24)
        field_id = rng.randrange(2 ** 16)
        definition_id = rng.randrange(2 ** 16)
        expected = sqlplus_node_id(record_id, field_id, definition_id,
                                   int(repeat_area), 0 if raw else 1)
        assert encode_opcua_node_id(record_id, field_id, definition_id,
                                    repeat_area=repeat_area, raw=raw) == expected


def test_encoding_shape():
    node_id = encode_opcua_node_id(0x1234, 0x0022, 0x00A0)
    assert node_id == "ns=3;b=AAAAAAAAAAA0EgAAAAAiAKAAAAA="
    assert encode_opcua_node_id(1, 1, 1, raw=False).startswith("ns=2;b=AAAAAAEAAAA")


@pytest.mark.parametrize("kwargs", [
    {"record_id": -1, "field_id": 1, "definition_id": 1},
    {"record_id": 1, "field_id": 2 ** 16, "definition_id": 1},
    {"record_id": 1, "field_id": 1, "definition_id": 2 ** 32},
])
def test_encoding_rejects_out_of_range_ids(kwargs):
    with pytest.raises(ValueError):
        encode_opcua_node_id(**kwargs)


def _queue_lookup(conn, field_rows=(("0022",),), repeat_rows=((0,),),
                  record_rows=(("FT101", 0x1234, "IP_AnalogDef"),),
                  def_rows=(("IP_AnalogDef", 0xA0),)):
    conn.queue(["Field_Number (hex)"], list(field_rows))
    conn.queue(["REPEAT_AREA_INDEX"], list(repeat_rows))
    conn.queue(["NAME", "RECID", "DEFINITION"], list(record_rows))
    conn.queue(["NAME", "RECID"], list(def_rows))


def test_node_ids_look_up_record_field_and_definition():
    ac, conn = make_conn()
    _queue_lookup(conn)

    df = ac.opcua_node_ids(["FT101", "NOPE"])

    assert df["NAME"].tolist() == ["FT101", "NOPE"]
    assert df["FIELD"].tolist() == ["IP_INPUT_VALUE", "IP_INPUT_VALUE"]
    assert df["NODE_ID"].iloc[0] == encode_opcua_node_id(0x1234, 0x22, 0xA0)
    assert df["NODE_ID"].isna().iloc[1]
    sql = "\n".join(conn.executed)
    assert "FROM FieldLongNameDef" in sql
    assert "WHERE NAME IN ('FT101', 'NOPE')" in sql
    assert "WHERE NAME IN ('IP_AnalogDef')" in sql


def test_node_id_falls_back_to_short_field_names_and_repeat_area():
    ac, conn = make_conn()
    conn.queue(["Field_Number (hex)"], [])  # not a long name
    _queue_lookup(conn, field_rows=[(0x0101,)], repeat_rows=[(3,)])

    node_id = ac.opcua_node_id("FT101", field="IP_TREND_VALUE", raw=False)

    assert node_id == encode_opcua_node_id(0x1234, 0x0101, 0xA0, repeat_area=True, raw=False)
    assert any("FROM FieldNameDef" in sql for sql in conn.executed)


def test_measurement_is_an_alias_for_the_input_value():
    ac, conn = make_conn()
    _queue_lookup(conn)
    assert ac.opcua_node_ids("FT101", field="measurement")["FIELD"].iloc[0] == "IP_INPUT_VALUE"


def test_unknown_field_and_tag_raise():
    ac, conn = make_conn()
    conn.queue(["Field_Number (hex)"], [])
    conn.queue(["Field_Number (hex)"], [])
    with pytest.raises(ValueError, match="Unknown IP21 field"):
        ac.opcua_node_ids("FT101", field="NOT_A_FIELD")

    ac, conn = make_conn()
    _queue_lookup(conn, record_rows=[], def_rows=[])
    with pytest.raises(ValueError, match="No IP21 record"):
        ac.opcua_node_id("FT101")


def test_field_name_is_validated():
    ac, _ = make_conn()
    with pytest.raises(ValueError):
        ac.opcua_node_ids("FT101", field="X'; DROP")
