import re
import numpy as np
import pandas as pd
import xml.etree.ElementTree as ET


class ControlLogix_Rungs:
    def __init__(self, path):
        self.path = path
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()
        self.df = self._get_rung_df()

    def datatype(self, plc_row_series):
        alias_type = plc_row_series.datatype
        specifier = plc_row_series.specifier
        field = ''
        try:
            field = re.search(
                r'^[\w\:]+[\.\[\]\d]*(?P<alias_field>\w*)?(?:.*)?',
                specifier,
            ).group(1)
        except TypeError:
            pass

        try:
            udt = re.search(r'(^[\w\:]+)', alias_type).group(0)
            if udt in {'BOOL', 'INT', 'BIT', 'DINT', 'SINT'}:
                return udt
            if field.lower() in {'alarms', 'status_word'}:
                field = field.title()
            return self.root.find(
                f"Controller/DataTypes/*[@Name='{udt}']/Members/*[@Name='{field}']"
            ).get('DataType')
        except TypeError:
            return np.nan

    def _get_rung_df(self):
        '''Build a DataFrame of all rung info from the L5X file.'''
        rows = []
        for program in self.tree.findall('.//Program'):
            prog_name = program.attrib['Name']
            for routine in program.findall('.//Routine'):
                rout_name = routine.attrib['Name']
                for rung in routine.findall('.//Rung'):
                    text_el    = rung.find('Text')
                    comment_el = rung.find('Comment')
                    rows.append({
                        'program':      prog_name,
                        'routine':      rout_name,
                        'rung_num':     rung.attrib['Number'],
                        'rung_text':    text_el.text    if text_el    is not None else None,
                        'rung_comment': comment_el.text if comment_el is not None else None,
                    })
        return pd.DataFrame(rows, columns=['program', 'routine', 'rung_num', 'rung_text', 'rung_comment'])

    def to_excel(self, path, table_name='Rungs'):
        with pd.ExcelWriter(path) as writer:
            self.df.to_excel(writer, sheet_name=table_name, index=False)
            ws = writer.sheets[table_name]
            for col_num, col in enumerate(self.df):
                max_len = max(self.df[col].astype(str).str.len().max(), len(str(col))) + 1
                ws.set_column(col_num, col_num, max_len)
