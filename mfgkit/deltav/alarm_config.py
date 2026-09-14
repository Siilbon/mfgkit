import re
from pathlib import Path
import pandas as pd


SAM_PRETTY_COLS = {
    'name': 'Module',
    'desc': 'Module Description',
    'default_display': 'Default Display',
    'plant_area': 'Plant Area',
    'unit_module': 'Unit Module',
    'module': 'Module (from path)',
    'alarm': 'Alarm',
    'attribute': 'Attribute',
    'path': 'Path',
    'alarmsourcename': 'Alarm Source Name',
    'alarmsourcedescription': 'Alarm Source Description',
    'type': 'Type',
    'parameter': 'Parameter',
    'limitvalue': 'Limit Value',
    'enable': 'Enabled',
    'inverted': 'Inverted',
    'priority': 'Priority',
    'p1parameter': 'P1 Parameter',
    'p2parameter': 'P2 Parameter',
    'enabledelay': 'Enable Delay',
    'ondelay': 'On Delay',
    'offdelay': 'Off Delay',
    'hysteresis': 'Hysteresis',
    'daystimeout': 'Days Timeout',
    'hourstimeout': 'Hours Timeout',
    'minutestimeout': 'Minutes Timeout',
    'functionalclassification': 'Functional Classification',
    'functionalclassificationname': 'Functional Classification Name',
    'hasalarmhelp': 'Has Alarm Help',
    'alarmdescription': 'Alarm Description',
}


def clean_sam_df_cols(columns):
    """Normalize a SAM column name by lowercasing and stripping whitespace."""
    columns = columns.lower()
    columns = columns.strip()
    return columns


def extract_plant_area(path: str | Path) -> str:
    """Return the plant area prefix parsed from a SAM alarms XML filename."""
    path = Path(path)
    plant_area_pat = r'^(?P<plant_area>[\w]+)_SAMAlarmsReport.xml'
    plant_area = re.search(pattern=plant_area_pat,
                           string=path.name)
    plant_area = plant_area['plant_area']

    return plant_area


def load_sam_xml(path: str | Path):
    """Load a single SAM alarms XML report into a DataFrame tagged with its plant area."""
    path = Path(path)
    plant_area = extract_plant_area(path)

    df = pd.read_xml(path)
    df = df.rename(columns=clean_sam_df_cols)
    df = df.dropna(how='all')

    # extract the details from the alarm path
    alarm_path_pat = r'(?P<plant_area>[\w\-\$]+)/(?P<unit_module>[\w\-\$]+/)?(?P<module>[\w\-\$]+)/(?P<alarm>[\w\-\$]+)'
    path_df = df['path'].str.extract(alarm_path_pat)
    path_df['unit_module'] = path_df['unit_module'].str.replace('/', '')
    df = pd.concat([path_df, df], axis=1)
    # df['plant_area'] = plant_area

    # cols = ['plant_area'] + [col for col in df.columns if col != 'plant_area']
    # df = df[cols]
    print(f'loaded {plant_area} alarms')

    return df


def load_sam_dir(path: str | Path):
    """Load and concatenate every SAM alarms XML report in a directory into one DataFrame."""
    path = Path(path)
    dfs = []
    for file in path.glob('*.xml'):
        df = load_sam_xml(file)
        dfs.append(df)
    sam_df = pd.concat(dfs, ignore_index=True)

    print(f'Combined {len(dfs)} files from {path}')
    return sam_df