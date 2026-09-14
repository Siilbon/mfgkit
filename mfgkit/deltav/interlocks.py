import re
import pandas as pd
from .utils import extract_block

# Match to class based and unclassed modules
_MODULE_RE = re.compile(
    r'MODULE(_INSTANCE)? TAG="(?P<module>[^"]*)" PLANT_AREA="(?P<plant_area>[^"]*)"( MODULE_CLASS="(?P<module_class>[^"]*)")?'
)
_MODULE_DESC_RE = re.compile(r'DESCRIPTION="([^"]*)"')
_ATTR_INST_RE = re.compile(r'ATTRIBUTE_INSTANCE NAME="([^"]*)"')
# Matches ILK/ACT/TRK condition attribute names with either $ or / as separator
_ATTR_NAME_RE = re.compile(
    r'^(?P<block_type>ILK|ACT|TRK)(?P<block_num>\d+)[\$\/]CND(?P<cnd_num>\d+)[\$\/](?P<field>DESC|T_EXPRESSION)$'
)
_CV_RE = re.compile(r'CV="([^"]*)"')
# Handles "" as escaped quotes inside EXPRESSION="..."
_EXPR_RE = re.compile(r'EXPRESSION="((?:[^"]|"")*)"', re.S)


def _extract_value(attr_block):
    """Return the condition expression from an attribute block.

    ILK conditions use TYPE=CONDITION EXPRESSION="..." format.
    ACT/TRK conditions store their expression as a plain CV string.
    """
    expr_m = _EXPR_RE.search(attr_block)
    if expr_m:
        return expr_m.group(1).replace('""', '"')
    cv_m = _CV_RE.search(attr_block)
    return cv_m.group(1) if cv_m else ''


def parse_interlocks(fhx_data):
    """Extract ILK/ACT/TRK conditions from all MODULE_INSTANCEs, one row per condition.

    Column meanings:
      type       - ILK (interlock/trip), ACT (action), or TRK (track condition)
      block_num  - which ILK/ACT/TRK block this condition belongs to (1, 2, ...)
      cnd_num    - condition number within that block (1, 2, ...)
      description - human-readable condition description
      expression  - Boolean expression or module reference that triggers the condition
    """
    rows = []

    # Get all modules
    for mi_m in _MODULE_RE.finditer(fhx_data):
        module = mi_m.group('module')
        module_class = mi_m.group('module_class') or 'Classless'
        plant_area = mi_m.group('plant_area')
        block = extract_block(fhx_data, mi_m.end())

        mod_desc_m = _MODULE_DESC_RE.search(block)
        mod_desc = mod_desc_m.group(1) if mod_desc_m else ''

        # Collect DESC and T_EXPRESSION per (type, block_num, cnd_num)
        conditions = {}

        for attr_m in _ATTR_INST_RE.finditer(block):
            name_m = _ATTR_NAME_RE.match(attr_m.group(1))
            if not name_m:
                continue
            block_type = name_m.group('block_type')
            block_num = int(name_m.group('block_num'))
            cnd_num = int(name_m.group('cnd_num'))
            field = name_m.group('field')

            key = (block_type, block_num, cnd_num)
            if key not in conditions:
                conditions[key] = {}

            attr_block = extract_block(block, attr_m.end())

            if field == 'DESC':
                v = _CV_RE.search(attr_block)
                conditions[key]['desc'] = v.group(1) if v else ''
            elif field == 'T_EXPRESSION':
                conditions[key]['expression'] = _extract_value(attr_block)

        for (block_type, block_num, cnd_num), vals in conditions.items():
            rows.append({
                'module': module,
                'module_class': module_class,
                'plant_area': plant_area,
                'module_description': mod_desc,
                'type': block_type,
                'block_num': block_num,
                'cnd_num': cnd_num,
                'description': vals.get('desc', ''),
                'expression': vals.get('expression', ''),
            })

    return pd.DataFrame(rows)
