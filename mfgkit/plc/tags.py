import logging
import re

import pandas as pd
from ..utils import read_stacked_csv

logger = logging.getLogger(__name__)


def plc_column_parser(columns):
    return columns.str.lower()


def plc_comment_parser(comment):
    return (comment.str.replace(pat=r'\$+N',    repl=' ',  regex=True)
                   .str.replace(pat=r'\$+Q',    repl='"',  regex=True)
                   .str.replace(pat=r'\$+\'',   repl='"',  regex=True)
                   .str.replace(pat=r'\$+0006', repl='-',  regex=True))


def add_alias_to_column(column):
    return f'alias_{column}'


def rack_extract(df, column):
    '''Add rack/mod/bit columns parsed from an IO address column.'''
    pat = r'R(?P<rack>\d+)(?P<rack_letter>[A-Z]?):(?P<io>[OI]).Data\[(?P<mod>\d+)\]\.(?P<bit>\d+)'
    rack_info = df[column].str.extract(pat=pat)
    return df.merge(rack_info, left_index=True, right_index=True)


class ControlLogix_Tags:
    def __init__(self, path):
        self.path = path
        self.tables = self._tables()
        self.alias = self._alias()
        self.tags = self._tags()

    def _tables(self):
        tables = read_stacked_csv(
            path=self.path,
            sep=r'^TYPE',
            offset=1,
            table_name_col='scope',
            table_name_default='ctrl',
            column_parser=plc_column_parser,
            encoding='latin1',
        )
        comment_columns = ['comment', 'description']
        for table_name, table in tables.items():
            for col in comment_columns:
                try:
                    table[col] = plc_comment_parser(table[col].astype(str))
                except KeyError:
                    pass
        return tables

    def _alias(self):
        ctrl = self.tables['ctrl']
        alias = ctrl[ctrl['type'] == 'ALIAS']
        alias = alias.rename(add_alias_to_column, axis='columns')
        alias = alias.merge(
            alias['alias_specifier'].str.extract(r'^(?P<clx_tag_name>[\w\:]+)'),
            how='left',
            left_index=True,
            right_index=True,
        )
        return alias

    def _tags(self):
        ctrl = self.tables['ctrl']
        tags = ctrl[ctrl['type'] == 'TAG']
        tags = tags.rename(columns={
            'name':        'tag_name',
            'description': 'tag_description',
            'datatype':    'tag_datatype',
        })
        tags = tags.drop(['type', 'scope', 'specifier', 'attributes'], axis=1)
        return tags

    def lookup(self, tag, exact_loop=False):
        if exact_loop:
            return self.alias[self.alias['alias_name'] == tag]
        return self.alias[self.alias['alias_name'].str.contains(tag, case=False)]

    def io_lookup(self, rack, mod=r'\d+', bit=r'\d+'):
        rack_regex = rf'R{rack}:\w.Data\[{mod}\].{bit}'
        return self.alias[self.alias['alias_specifier'].str.contains(rack_regex)].sort_values('alias_specifier')

    def to_excel(self, path):
        with pd.ExcelWriter(path) as writer:
            for table_name, table in self.tables.items():
                sheet = re.sub(r'[\[\]\:\*\?\/\\]', ' ', table_name)
                table.to_excel(writer, sheet_name=sheet, index=False)
                ws = writer.sheets[sheet]
                for col_num, col in enumerate(table):
                    max_len = max(table[col].astype(str).str.len().max(), len(str(col))) + 1
                    ws.set_column(col_num, col_num, max_len)


# IO utility functions

def get_rack_df(
    io_df,
    regex=r'(?P<rack>R\d{3}\w):(?P<in_out>[IO])\.Data\[(?P<mod>\d*)\]\.(?P<bit>\d*)',
):
    '''Extract rack/slot/bit columns from the specifier column of an alias DataFrame.'''
    # astype('string') so an empty frame (all-NaN float column) extracts to an
    # empty result instead of raising on the .str accessor.
    racks_df = io_df['alias_specifier'].astype('string').str.extract(regex, expand=True)
    racks_df['mod'] = pd.to_numeric(racks_df['mod'], errors='coerce')
    racks_df['bit'] = pd.to_numeric(racks_df['bit'], errors='coerce')
    return racks_df


def find_references(rungs, name):
    '''Return a dict of {index: "program routine rung_num"} for rungs referencing name.'''
    mask = rungs.df['rung_text'].str.contains(rf'\W{re.escape(name)}\W', na=False)
    refs = rungs.df[mask]
    return (refs['program'] + ' ' + refs['routine'] + ' ' + refs['rung_num']).to_dict()


def reference_cols(io_df, rungs):
    '''Return a DataFrame with reference counts for each row in io_df.'''
    refs = io_df.apply(
        lambda row: find_references(rungs=rungs, name=row['alias_name']), axis=1
    )
    result = pd.DataFrame(index=io_df.index)
    result['references']     = refs
    result['num_references'] = refs.apply(len)
    result['is_referenced']  = result['num_references'] > 0
    return result


def get_io(tags, table_names=None, rack_prefix=r'Rack_\d{2}',
           rack_regex=None, scanners_df=None, rungs=None):
    '''
    Build an IO DataFrame from alias entries that point to physical rack addresses.

    Parameters:
        tags:        ControlLogix_Tags instance
        table_names: list of table keys to search (defaults to all tables)
        rack_prefix: regex pattern that specifier must start with
        rack_regex:  override the rack address extraction pattern
        scanners_df: optional DataFrame with scanner/channel info keyed on 'rack'
        rungs:       optional ControlLogix_Rungs instance for reference counts
    '''
    if table_names is None:
        table_names = list(tags.tables.keys())

    ctrl_df = pd.concat([tags.tables[t] for t in table_names], axis=0, ignore_index=True)
    alias_df = ctrl_df[ctrl_df['type'] == 'ALIAS'].copy()

    # Re-apply alias column rename if it hasn't been done on the concatenated frame
    if 'name' in alias_df.columns:
        alias_df = alias_df.rename(columns={'name': 'alias_name', 'specifier': 'alias_specifier'})

    # An export with no ALIAS rows leaves an all-NaN float column, and the .str
    # accessor raises on it. Coerce so an aliasless controller returns an empty
    # frame rather than crashing.
    specifiers = alias_df['alias_specifier'].astype('string')
    io_df = alias_df[specifiers.str.contains(rf'^{rack_prefix}', na=False)].copy()

    # Rack addressing is a per-controller convention: R11B:I.Data[1].10 on one
    # controller, Rack_07:I.Slot[3].Data.2 on the next. A prefix that matches
    # nothing yields an empty frame rather than an error, which reads as "this
    # controller has no IO" instead of "your regex is wrong". Say so.
    if io_df.empty and not alias_df.empty:
        sample = alias_df['alias_specifier'].dropna().head(3).tolist()
        logger.warning(
            "No alias specifiers matched rack_prefix %r. This controller may use "
            "a different addressing convention. Example specifiers: %s",
            rack_prefix, sample,
        )

    if rack_regex is None:
        rack_regex = rf'(?P<rack>{rack_prefix}):(?P<in_out>[IO])\.Data\[(?P<mod>\d*)\]\.(?P<bit>\w*\d*\w*)'

    rack_df = get_rack_df(io_df, regex=rack_regex)
    io_df = io_df.merge(rack_df, left_index=True, right_index=True)

    if scanners_df is not None:
        io_df = io_df.merge(scanners_df, left_on='rack', right_on='rack')

    if rungs is not None:
        io_df = io_df.merge(reference_cols(io_df, rungs), left_index=True, right_index=True)

    return io_df
