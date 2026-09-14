import os
import re
import pandas as pd
from .utils import extract_block


def parse_devicenet(fhx_data):
    """Extract DeviceNet device inventory from FHX file contents."""
    device_pat = r'DEVICENET_DEVICE NAME="(?P<device_name>[^\"]*)" MANUFACTURER="(?P<manufacturer>[^\"]*)" DEVICENET_DEVICE_TYPE="(?P<device_type>[^\"]*)" REVISION=(?P<rev>\d*)[^\{]*{\s*DESCRIPTION="(?P<desc>[^\"]*)"\s*PORT_ASSIGNMENT { (?P<port_raw>[^\}]*)}'
    port_pat = r'PATH="(?P<path>[^"]*)"\s+ADDRESS=(?P<address>\d+)'
    path_pat = r'(?P<controller>\w+)/IO1/(?P<card>\w+)/(?P<port>\w+)'

    matches = re.findall(device_pat, fhx_data, flags=re.S)
    df = pd.DataFrame(matches, columns=['device_name', 'manufacturer', 'device_type', 'rev', 'desc', 'port_raw'])

    port_df = df['port_raw'].str.extract(port_pat, expand=True)
    path_df = port_df['path'].str.extract(path_pat, expand=True)

    df = pd.concat([df.drop(columns='port_raw'), port_df, path_df], axis=1)
    df = df[['device_name', 'manufacturer', 'device_type', 'rev', 'desc', 'controller', 'card', 'port', 'address']]
    df = df.astype({'rev': int, 'address': int})
    return df.sort_values(by=['controller', 'card', 'address']).reset_index(drop=True)



IO_CHANNEL_COLUMNS = [
    'controller', 'subsystem', 'card_slot', 'card_definition', 'position',
    'channel_definition', 'description', 'enabled', 'tag', 'controller_assignment',
]

_CHANNEL_RE = re.compile(r'SIMPLE_IO_CHANNEL POSITION=(\d+) DEFINITION="([^"]*)"')
_DESC_RE = re.compile(r'DESCRIPTION="([^"]*)"')
# Anchored to the start of a line so ALARMS_ENABLED inside a HART DST can't match
_ENABLED_RE = re.compile(r'^\s*ENABLED=([TF])', flags=re.M)
_TAG_RE = re.compile(r'DEVICE_SIGNAL_TAG="([^"]*)"')
_HART_TAG_RE = re.compile(r'HART_DEVICE_SIGNAL_TAG NAME="([^"]*)"')
_ASSIGNMENT_RE = re.compile(r'CONTROLLER_ASSIGNMENT="([^"]*)"')


def _parse_io_cards(fhx_data, card_keyword, use_card_description):
    """Shared parser for card-like IO blocks (SIMPLE_IO_CARD, CHARM), one row per channel.

    use_card_description: take the description from the card instead of the
    channel. A CHARM holds one channel, and that channel's description is generic.
    """
    # The lookbehind stops CHARM matching the end of a longer keyword
    card_re = re.compile(
        rf'(?<![A-Z_]){card_keyword} CARD_SLOT=(\d+) IO_SUBSYSTEM="([^"]*)" '
        r'CONTROLLER="([^"]*)" DEFINITION="([^"]*)"'
    )

    rows = []
    for card_m in card_re.finditer(fhx_data):
        card_slot, subsystem, controller, card_def = card_m.groups()
        card_block = extract_block(fhx_data, card_m.end())

        # Card-level fields sit before the first channel
        first_ch = _CHANNEL_RE.search(card_block)
        card_header = card_block[:first_ch.start()] if first_ch else card_block
        card_desc_m = _DESC_RE.search(card_header)
        # Remote IO cards and CHARMs name the controller they're assigned to;
        # classic cards belong to the controller they sit in
        assignment_m = _ASSIGNMENT_RE.search(card_header)

        for ch_m in _CHANNEL_RE.finditer(card_block):
            position, channel_def = ch_m.groups()
            ch_block = extract_block(card_block, ch_m.end())

            desc_m = card_desc_m if use_card_description else _DESC_RE.search(ch_block)
            enabled_m = _ENABLED_RE.search(ch_block)
            tag_m = _TAG_RE.search(ch_block)
            hart_m = _HART_TAG_RE.search(ch_block)

            rows.append({
                'controller': controller,
                'subsystem': subsystem,
                'card_slot': int(card_slot),
                'card_definition': card_def,
                'position': int(position),
                'channel_definition': channel_def,
                'description': desc_m.group(1) if desc_m else '',
                'enabled': enabled_m.group(1) == 'T' if enabled_m else None,
                'tag': tag_m.group(1) if tag_m else (hart_m.group(1) if hart_m else ''),
                'controller_assignment': assignment_m.group(1) if assignment_m else controller,
            })

    return pd.DataFrame(rows, columns=IO_CHANNEL_COLUMNS)


def parse_trad(fhx_data):
    """Extract traditional IO channels from FHX file contents, one row per channel."""
    return _parse_io_cards(fhx_data, 'SIMPLE_IO_CARD', use_card_description=False)


def parse_charms(fhx_data):
    """Extract CHARMs IO from FHX file contents, one row per CHARM slot.

    Same columns as parse_trad: controller is the CIOC, card_slot is the CHARM
    number (1-96), and empty slots come through as CHMIO_UNDEFINED_CHARM.
    """
    return _parse_io_cards(fhx_data, 'CHARM', use_card_description=True)


def read_fhx(file_path, parser):
    """Open an FHX file and apply a parser function to its contents."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"FHX file not found: {file_path}")
    with open(file_path, mode='r', encoding='utf-16') as f:
        return parser(f.read())


def parse_fhx_directory(directory_path, parser, extension='.fhx'):
    """Apply a parser to all FHX files in a directory and return a concatenated DataFrame."""
    if not os.path.isdir(directory_path):
        raise ValueError(f"Invalid directory: {directory_path}")

    frames = []
    for filename in os.listdir(directory_path):
        if filename.endswith(extension):
            file_path = os.path.join(directory_path, filename)
            df = read_fhx(file_path, parser)
            df['source_file'] = filename
            frames.append(df)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
