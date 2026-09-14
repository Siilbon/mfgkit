from pathlib import Path
import pandas as pd

ALMACTIVE_PRETTY_COLS = {
    'alm_status': 'Alarm Status',
    'ack_status': 'Ack Status',
    'time_in': 'Time In',
    'unit': 'Unit',
    'module': 'Module',
    'parameter': 'Parameter',
    'description': 'Description',
    'alarm': 'Alarm Text',
    'help': 'Help',
    'message': 'Message',
    'priority': 'Priority',
    'node': 'Node',
}


def clean_almsum_df_cols(columns):
    """Normalize a Alarm Summary column name by lowercasing and stripping whitespace."""
    columns = columns.lower()
    columns = columns.strip()
    return columns


def extract_plant_area(path: str | Path) -> str:
    """Return the plant area prefix parsed from a Alarm Summary alarms XML filename."""
    path = Path(path)
    plant_area_pat = r'^(?P<plant_area>[\w]+)_Alarm SummaryAlarmsReport.xml'
    plant_area = re.search(pattern=plant_area_pat,
                           string=path.name)
    plant_area = plant_area['plant_area']

    return plant_area


def load_almsum_xml(path: str | Path):
    """Load a single Alarm Summary alarms XML report into a DataFrame tagged with its plant area."""
    path = Path(path)

    df = pd.read_xml(path)
    df = df.rename(columns=clean_almsum_df_cols)
    df = df.dropna(how='all')

    # correct column to datetime
    df['time_in'] = pd.to_datetime(df['time_in'])

    # split out the alarm and acknowledge status
    df[['alm_status', 'ack_status']] = df['ack'].str.split('/', expand=True)
    
    # split out the module and parameter
    df[['module', 'parameter']] = df['module_parameter'].str.split('/', expand=True)
    
    # drop the original columns
    df = df.drop(columns=['ack', 'module_parameter'])
    df = df[list(ALMACTIVE_PRETTY_COLS)]

    print(f'loaded {path.name} alarms')

    return df


def load_almsum_dir(path: str | Path):
    """Load and concatenate every Alarm Summary alarms XML report in a directory into one DataFrame."""
    path = Path(path)
    dfs = []
    for file in path.glob('*.xml'):
        df = load_almsum_xml(file)
        dfs.append(df)
    almsum_df = pd.concat(dfs, ignore_index=True)

    print(f'Combined {len(dfs)} files from {path}')
    return almsum_df