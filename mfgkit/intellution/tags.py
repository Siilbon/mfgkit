"""Intellution / iFIX tag database parsing.

Parses an exported Intellution tag database (a file of stacked tables, one per
block type) into a single DataFrame, with the tag name decomposed into
prefix/loop/suffix and the IO address decomposed into its OPC components.
"""

import pandas as pd

from ..utils import read_stacked_csv


def intellution_column_parser(columns):
    columns = (columns.str.lower()
                      .str.replace(pat=r'a_', repl='')
                      .str.replace(pat=r'!', repl=''))
    return columns


class IntellutionDB():

    def __init__(self, path):
        self.path = path
        self.orig_df = self.original_df()
        self.df = self.dataframe()
        self.alarm_df = self.alarm_dataframe()

    def original_df(self):

        # Read the file to find all the stacked tables
        tables = read_stacked_csv(path=self.path, 
                                  sep=r'\!A_NA',
                                  offset=3,
                                  table_name_col='!A_NAME',
                                  table_name_default='problem',
                                  column_parser=None,
                                  encoding='latin1')
        return tables

    def dataframe(self):

        # Read the file to find all the stacked tables
        tables = read_stacked_csv(path=self.path, 
                                  sep=r'\!A_NA',
                                  offset=3,
                                  table_name_col='name',
                                  table_name_default='problem',
                                  column_parser=intellution_column_parser,
                                  encoding='latin1')

        tables['agg'] = pd.concat(tables.values(), 
                                  axis=0, 
                                  ignore_index=True, 
                                  sort=False)

        df = tables['agg'].filter([
            'name', 'tag', 'desc', 'iscan', 'ioht', 'ioad', 'ienab', 'opendesc',
            'closedesc', 'elo', 'ehi', 'egudesc', 'pri', 'sa1', 'sa2', 'sa3',
            'area1', 'area2', 'area3', 'area4', 'area5', 'area6', 'area7',
            'area8', 'area9', 'area10', 'area11', 'area12', 'area13', 'area14',
            'area15', 'almext1', 'almext2'
        ])

        # A column containing all of the Alarm Areas, skipping blank slots so
        # there are no empty entries like 'ALL,,,,DeltaV'
        area_cols = df.columns[df.columns.str.contains(r'area')]

        df['all_areas'] = [
            ','.join(a.strip() for a in row if isinstance(a, str) and a.strip())
            for row in df[area_cols].itertuples(index=False)
        ]


        # A regex to pick out the prefix, loop number, and suffix from a tag
        pattern = r'(?P<prefix>[^\d\s]*)(?P<loop>\d{5,8}(?:_\d+[A-Z]*)?)(?P<suffix>.*)'
        loop_split = df.tag.str.extract(pattern, expand=True)

        df = df.merge(
            loop_split,
            how='left',
            left_on='tag',
            right_on=loop_split['prefix'] + loop_split['loop'] +
            loop_split['suffix'])

        # Add columns for the components of the IO address
        ioad_split = df.ioad.str.split(';', expand=True)
        ioad_split.columns = [
            'source', 'opc_topic', 'ioad', 'access_path', 'unknown1', 'unknown2'
        ]
        opc_split = ioad_split.ioad.str.extract(
            r'(?:^\[(?P<ft_topic>\w*)\])?(?P<ioad>.*)', expand=True)
        ioad_split = ioad_split.drop('ioad', axis=1)

        opc_split = opc_split.merge(
            ioad_split, how='left', left_index=True, right_index=True)

        df = df.drop('ioad', axis=1)
        df = df.merge(opc_split, how='left', left_index=True, right_index=True)

        # Add columns for alias
        alias_pattern = r'^(?P<alias_root>[\w\:]+)[\.\[\]\d]*(?P<alias_field>\w*)?(?P<alias_rest>.*)?'
        df_merge = df.ioad.str.extract(alias_pattern)
        df = df.merge(df_merge, how='left', left_index=True, right_index=True)

        return df

    def alarm_dataframe(self):
        alarm = self.df[self.df['name'] == 'DA']
        return alarm

    def lookup(self, tag, exact_loop=False):
        if exact_loop:
            results = self.df[self.df['loop'] == tag]
        else:
            results = self.df[self.df['tag'].str.contains(tag, case=False)]

        return results

    def alarm_area(self, area):
        results = self.df[self.df['all_areas'].str.contains(area)]

        return results
