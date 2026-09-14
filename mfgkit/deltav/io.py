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



def parse_trad(fhx_data):
    """Extract traditional IO channels from FHX file contents, one row per channel."""
    card_re = re.compile(r'SIMPLE_IO_CARD CARD_SLOT=(\d+) IO_SUBSYSTEM="([^"]*)" CONTROLLER="([^"]*)" DEFINITION="([^"]*)"')
    channel_re = re.compile(r'SIMPLE_IO_CHANNEL POSITION=(\d+) DEFINITION="([^"]*)"')
    desc_re = re.compile(r'DESCRIPTION="([^"]*)"')
    enabled_re = re.compile(r'ENABLED=([TF])')
    tag_re = re.compile(r'DEVICE_SIGNAL_TAG="([^"]*)"')
    hart_tag_re = re.compile(r'HART_DEVICE_SIGNAL_TAG NAME="([^"]*)"')

    rows = []
    for card_m in card_re.finditer(fhx_data):
        card_slot, subsystem, controller, card_def = card_m.groups()
        card_block = extract_block(fhx_data, card_m.end())

        for ch_m in channel_re.finditer(card_block):
            position, channel_def = ch_m.groups()
            ch_block = extract_block(card_block, ch_m.end())

            desc_m = desc_re.search(ch_block)
            enabled_m = enabled_re.search(ch_block)
            tag_m = tag_re.search(ch_block)
            hart_m = hart_tag_re.search(ch_block)

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
            })

    return pd.DataFrame(rows)


def parse_charms(fhx_data):
    """Extract CHARMS IO data from FHX file contents."""
    raise NotImplementedError("CHARMS parsing is not yet implemented")


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
