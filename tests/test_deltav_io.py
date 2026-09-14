"""Tests for the DeltaV FHX IO parsers, using a trimmed synthetic export.

The snippet mirrors the structure of a real control network FHX so the suite
stays self-contained -- no plant data files in the repo.
"""

from mfgkit.deltav.io import IO_CHANNEL_COLUMNS, parse_charms, parse_trad

FHX = '''
SIMPLE_IO_CARD CARD_SLOT=1 IO_SUBSYSTEM="IO1" CONTROLLER="RIO01" DEFINITION="AI_16CH_HART_4-20"
 user="novaspect" time=1680888521/* "07-Apr-2023 12:28:41" */
{
  DESCRIPTION="AI Card, 16 Ch., 4-20 mA, HART"
  CONTROLLER_ASSIGNMENT="CP01"
  SIMPLE_IO_CHANNEL POSITION=1 DEFINITION="AI_HD_HART_CHAN"
  {
    DESCRIPTION="AHU-A PRE-FILTER dP"
    ENABLED=T
    HART_DEVICE_SIGNAL_TAG NAME="DPT-27708"
    {
      DESCRIPTION="HART device"
      ALARMS_ENABLED=F
    }
  }
  SIMPLE_IO_CHANNEL POSITION=2 DEFINITION="AI_HD_CHAN"
  {
    DESCRIPTION="Analog Input Channel"
    ENABLED=F
    DEVICE_SIGNAL_TAG="RIO01C01CH02"
  }
}
SIMPLE_IO_CARD CARD_SLOT=5 IO_SUBSYSTEM="IO1" CONTROLLER="CP02" DEFINITION="DO_32CH_HD"
{
  DESCRIPTION="DO Card"
  SIMPLE_IO_CHANNEL POSITION=3 DEFINITION="DO_HD_CHAN"
  {
    DESCRIPTION="Pump Start"
    ENABLED=T
    DEVICE_SIGNAL_TAG="MC-101"
  }
}
CHARM_TYPE_DEFINITION NAME="CHMIO_DI_DEFAULT_CHARM" CLASS="CHMIO_DI_CLASS"
{
  DESCRIPTION="DI Generic CHARM"
}
CHARM CARD_SLOT=7 IO_SUBSYSTEM="CHARMS" CONTROLLER="CIOC-1" DEFINITION="CHMIO_IS_AI_4-20MA_HART_CHARM"
{
  DESCRIPTION="D/T 7TH TRAY TEMP"
  CONTROLLER_ASSIGNMENT="CP03"
  SIMPLE_IO_CHANNEL POSITION=1 DEFINITION="CHMIO_AI_HART_CHAN"
  {
    DESCRIPTION="HART Analog Input"
    ENABLED=T
    HART_DEVICE_SIGNAL_TAG NAME="TIT87290"
    {
      DESCRIPTION="D/T 7TH TRAY TEMP"
    }
  }
}
CHARM CARD_SLOT=8 IO_SUBSYSTEM="CHARMS" CONTROLLER="CIOC-1" DEFINITION="CHMIO_UNDEFINED_CHARM"
{
  DESCRIPTION="Undefined functionality"
  CONTROLLER_ASSIGNMENT=""
  SIMPLE_IO_CHANNEL POSITION=1 DEFINITION="CHMIO_UNDEFINED_CHAN"
  {
    ENABLED=F
  }
}
'''


def test_parse_trad_reads_classic_card_channels():
    df = parse_trad(FHX)

    assert list(df.columns) == IO_CHANNEL_COLUMNS
    assert len(df) == 3
    hart, spare, pump = df.to_dict(orient='records')

    assert hart['tag'] == 'DPT-27708'
    assert hart['description'] == 'AHU-A PRE-FILTER dP'
    assert hart['enabled'] is True
    assert hart['controller_assignment'] == 'CP01'

    assert spare['tag'] == 'RIO01C01CH02'
    assert spare['enabled'] is False

    # Classic cards without an assignment belong to the controller they sit in
    assert pump['card_slot'] == 5
    assert pump['position'] == 3
    assert pump['controller_assignment'] == 'CP02'


def test_parse_trad_skips_charms():
    assert set(parse_trad(FHX)['controller']) == {'RIO01', 'CP02'}


def test_parse_charms_uses_card_description():
    df = parse_charms(FHX)

    assert list(df.columns) == IO_CHANNEL_COLUMNS
    assert len(df) == 2
    ai, empty = df.to_dict(orient='records')

    assert ai['controller'] == 'CIOC-1'
    assert ai['card_slot'] == 7
    assert ai['tag'] == 'TIT87290'
    assert ai['description'] == 'D/T 7TH TRAY TEMP'
    assert ai['controller_assignment'] == 'CP03'

    assert empty['card_definition'] == 'CHMIO_UNDEFINED_CHARM'
    assert empty['tag'] == ''
    assert empty['enabled'] is False
    assert empty['controller_assignment'] == ''


def test_parsers_return_columns_when_nothing_found():
    assert list(parse_trad('').columns) == IO_CHANNEL_COLUMNS
    assert list(parse_charms('').columns) == IO_CHANNEL_COLUMNS
