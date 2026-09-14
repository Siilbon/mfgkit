from .utils import parse_export

MODT_PATTERN = r"MODTI { NAME='(?P<name>[^']*)' DEVID=(?P<devid>\d*) DOID='[^']*' AREA=(?P<area>\d*) CDISP='(?P<cdisp>[^']*)' IDISP='(?P<idisp>[^']*)' DDISP='(?P<ddisp>[^']*)' DESC='(?P<desc>[^']*)' }"
ANT_PATTERN = r"ANTI { IT=(?P<it>\d*) NAME='(?P<area>[^']*)' SEQ=(?P<area_seq>\d*) TYPE=(?P<type>\w*) (?:DOID='(?P<doid>[^']*)')? }"
DT_PATTERN = r"DTI { NAM='(?P<node_name>\w*)' TYPE=(?P<node_type>\w*) ID=(?P<devid>\d*)"


def load_modules(modt_path, ant_path, dt_path):
    """Join MODT, ANT, and DT exports into a single enriched modules DataFrame."""
    modt = parse_export(modt_path, MODT_PATTERN, dropna=True)
    ant = parse_export(ant_path, ANT_PATTERN, dropna=True)
    dt = parse_export(dt_path, DT_PATTERN, dropna=True)

    modules = modt.merge(ant, how='left', left_on='area', right_on='it', suffixes=('_x', ''))
    modules = modules.merge(dt, how='left', on='devid')
    modules = modules.rename(columns={
        'cdisp': 'default_display',
        'idisp': 'faceplate',
        'ddisp': 'detail',
    })
    modules = modules.drop(columns=['devid', 'area_x', 'it', 'type'])
    return modules
