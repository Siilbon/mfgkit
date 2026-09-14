import os
import re
import pandas as pd


def extract_block(text, start):
    """Return text from the first '{' after start to its matching closing '}'."""
    open_pos = text.find('{', start)
    if open_pos == -1:
        return ''
    depth = 0
    for i in range(open_pos, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[open_pos:i + 1]
    return text[open_pos:]


def rename_extensions(folder_path, current_ext, new_ext):
    """Change file extensions in a folder from one extension to another."""
    pattern = r'(.+)\.'
    for file in os.listdir(folder_path):
        if current_ext in file:
            current_path = f'{folder_path}/{file}'
            new_file = re.match(pattern=pattern, string=file).group(1)
            os.rename(current_path, f'{folder_path}/{new_file}.{new_ext}')
    return True


def parse_export(file_path, pattern, dropna=False):
    """Parse a DeltaV flat-file export line by line using a named-group regex pattern."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Export file not found: {file_path}")
    with open(file_path) as f:
        lines = f.readlines()
    df = pd.DataFrame(lines, columns=['line'])
    df = df['line'].str.extract(pattern, expand=True)
    if dropna:
        df = df.dropna()
    return df
