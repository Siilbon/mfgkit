"""FactoryTalk PowerTool OPC configuration parsing.

Reads a PowerTool export into its three configuration tables (OPC channel,
devices, items) and breaks the OPC topic out of each item id.
"""

from ..utils import read_stacked_csv


class Powertool():
    def __init__(self, path):
        self.path = path
        self._tables = self._get_tables()
        
        # Access the 3 configuration tables
        self.opc_channel = self._tables['table_0']
        self.devices = self._tables['table_1']
        self.items = self._get_items()
    
    @staticmethod
    def _clean_powertool_cols(columns):
        # Get rid of extraneous characters in col names
        columns = (columns.str.lower()
                        .str.replace(pat=r'#', repl='')
                        .str.replace(pat=r'!', repl='')
                        .str.replace(pat=r'@', repl=''))
        return columns

    def _get_tables(self):
        return read_stacked_csv(self.path, sep=r'^\n', offset=2, column_parser=Powertool._clean_powertool_cols)

    def _get_items(self):
        # break out opc topic from the itemid
        items = self._tables['table_2']
        items = items.merge(items['itemid'].str.extract(r'(?:\[(?P<opc_topic>\w+)\])?(?P<system_address>.*)'), left_index=True, right_index=True)
        
        # add a channel column
        items.merge(self.devices[['name', 'channel']], left_on='device', right_on='name')
        # items = items.drop('name_y', axis=1)
        return items
