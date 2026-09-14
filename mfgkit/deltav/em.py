import re
import pandas as pd
from .utils import extract_block

# Matches the comment that identifies which EM command a FBD belongs to,
# followed by the FUNCTION_BLOCK_DEFINITION keyword itself.
_CMD_FBD_RE = re.compile(
    r'"[^"]+/([^"]+)"\s*\*/.*?FUNCTION_BLOCK_DEFINITION',
    re.S,
)
_STEP_RE = re.compile(r'STEP NAME="([^"]*)"')
_TRANS_RE = re.compile(r'TRANSITION NAME="([^"]*)"')
_ACTION_RE = re.compile(r'ACTION NAME="([^"]*)"')
_DESC_RE = re.compile(r'DESCRIPTION="([^"]*)"')
_ACTION_TYPE_RE = re.compile(r'ACTION_TYPE=(\w+)')
_QUALIFIER_RE = re.compile(r'QUALIFIER=(\w+)')
_TERMINATION_RE = re.compile(r'TERMINATION=([TF])')
_ST_CONN_RE = re.compile(r'STEP_TRANSITION_CONNECTION STEP="([^"]*)" TRANSITION="([^"]*)"')
_TS_CONN_RE = re.compile(r'TRANSITION_STEP_CONNECTION TRANSITION="([^"]*)" STEP="([^"]*)"')
_INITIAL_STEP_RE = re.compile(r'INITIAL_STEP="([^"]*)"')
# Handles "" as escaped quotes inside EXPRESSION="..."
_EXPRESSION_RE = re.compile(r'EXPRESSION="((?:[^"]|"")*)"', re.S)


def _get_em_name(fhx_data):
    m = re.search(r'BATCH_EQUIPMENT_UNIT_MODULE NAME="([^"]*)"', fhx_data)
    return m.group(1) if m else ''


def _parse_expression(text):
    m = _EXPRESSION_RE.search(text)
    return m.group(1).replace('""', '"') if m else ''


def _iter_commands(fhx_data):
    """Yield (command_name, sfc_block) for each command FBD that has an SFC_ALGORITHM."""
    for cmd_m in _CMD_FBD_RE.finditer(fhx_data):
        command = cmd_m.group(1)
        fbd_block = extract_block(fhx_data, cmd_m.end())
        sfc_start = fbd_block.find('SFC_ALGORITHM')
        if sfc_start == -1:
            continue
        yield command, extract_block(fbd_block, sfc_start)


def parse_em(fhx_data):
    """Extract EM command/step/action rows from FHX contents, one row per action."""
    em_name = _get_em_name(fhx_data)
    rows = []

    for command, sfc_block in _iter_commands(fhx_data):
        for step_m in _STEP_RE.finditer(sfc_block):
            step_name = step_m.group(1)
            step_block = extract_block(sfc_block, step_m.end())

            step_desc_m = _DESC_RE.search(step_block)
            step_desc = step_desc_m.group(1) if step_desc_m else ''

            for action_m in _ACTION_RE.finditer(step_block):
                action_name = action_m.group(1)
                action_block = extract_block(step_block, action_m.end())

                desc_m = _DESC_RE.search(action_block)
                type_m = _ACTION_TYPE_RE.search(action_block)
                qual_m = _QUALIFIER_RE.search(action_block)

                rows.append({
                    'em_name': em_name,
                    'command': command,
                    'step': step_name,
                    'step_description': step_desc,
                    'action': action_m.group(1),
                    'action_description': desc_m.group(1) if desc_m else '',
                    'action_type': type_m.group(1) if type_m else '',
                    'qualifier': qual_m.group(1) if qual_m else '',
                    'expression': _parse_expression(action_block),
                })

    return pd.DataFrame(rows)


def parse_em_transitions(fhx_data):
    """Extract EM command/transition rows from FHX contents, one row per transition."""
    em_name = _get_em_name(fhx_data)
    rows = []

    for command, sfc_block in _iter_commands(fhx_data):
        for trans_m in _TRANS_RE.finditer(sfc_block):
            trans_block = extract_block(sfc_block, trans_m.end())
            term_m = _TERMINATION_RE.search(trans_block)

            rows.append({
                'em_name': em_name,
                'command': command,
                'transition': trans_m.group(1),
                'termination': term_m.group(1) == 'T' if term_m else None,
                'expression': _parse_expression(trans_block),
            })

    return pd.DataFrame(rows)


def parse_em_connections(fhx_data):
    """Extract SFC step↔transition edges, one row per connection."""
    em_name = _get_em_name(fhx_data)
    rows = []

    for command, sfc_block in _iter_commands(fhx_data):
        for m in _ST_CONN_RE.finditer(sfc_block):
            rows.append({'em_name': em_name, 'command': command,
                         'from': m.group(1), 'from_type': 'step',
                         'to': m.group(2),   'to_type': 'transition'})
        for m in _TS_CONN_RE.finditer(sfc_block):
            rows.append({'em_name': em_name, 'command': command,
                         'from': m.group(1), 'from_type': 'transition',
                         'to': m.group(2),   'to_type': 'step'})

    return pd.DataFrame(rows)


def plot_sfc_graph(G):
    """Return an interactive Plotly figure of the SFC graph.

    Steps are drawn as blue squares; transitions as orange bars.
    Hover over any node to see its description or transition expression.
    """
    import plotly.graph_objects as go
    import networkx as nx

    # Assign positions: group nodes by topological depth, centre horizontally
    gens = list(nx.topological_generations(G))
    pos = {}
    for depth, gen in enumerate(sorted(g) for g in gens):
        for i, node in enumerate(gen):
            pos[node] = (i - (len(gen) - 1) / 2.0, -depth)

    # Edges
    edge_x, edge_y = [], []
    for u, v in G.edges():
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y, mode='lines',
        line=dict(width=1.5, color='#888'),
        hoverinfo='none', showlegend=False,
    )

    # Build one trace per node type
    def _node_trace(node_type, symbol, color, size):
        nodes = [(n, d) for n, d in G.nodes(data=True) if d.get('node_type') == node_type]
        xs = [pos[n][0] for n, _ in nodes]
        ys = [pos[n][1] for n, _ in nodes]
        labels = [n for n, _ in nodes]
        hovers = []
        for n, d in nodes:
            if node_type == 'step':
                marker = '★ ' if d.get('initial') else ''
                hovers.append(f"<b>{marker}{n}</b><br>{d.get('description', '')}")
            else:
                expr = d.get('expression', '').replace('\n', '<br>')
                term = ' [END]' if d.get('termination') else ''
                hovers.append(f"<b>{n}{term}</b><br>{expr}")
        return go.Scatter(
            x=xs, y=ys, mode='markers+text',
            marker=dict(symbol=symbol, size=size, color=color,
                        line=dict(width=1.5, color='white')),
            text=labels,
            textposition='middle center',
            textfont=dict(size=9, color='white'),
            hovertext=hovers, hoverinfo='text',
            name=node_type.capitalize(),
        )

    fig = go.Figure(
        data=[
            edge_trace,
            _node_trace('step',       'square',  '#2176ae', 32),
            _node_trace('transition', 'square',  '#e07b00', 14),
        ],
        layout=go.Layout(
            title=dict(
                text=f"{G.graph.get('em_name', '')} / {G.graph.get('command', '')}",
                font=dict(size=14),
            ),
            hovermode='closest',
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            margin=dict(l=20, r=20, t=50, b=20),
        ),
    )
    return fig


def build_sfc_graph(fhx_data, command):
    """Build a NetworkX DiGraph of the SFC flow for a single command.

    Step nodes carry: node_type='step', description
    Transition nodes carry: node_type='transition', expression, termination
    The initial step carries: initial=True
    """
    import networkx as nx

    connections = parse_em_connections(fhx_data)
    actions = parse_em(fhx_data)
    transitions = parse_em_transitions(fhx_data)

    cmd_conns = connections[connections['command'] == command]
    cmd_actions = actions[actions['command'] == command]
    cmd_transitions = transitions[transitions['command'] == command]

    G = nx.DiGraph(em_name=_get_em_name(fhx_data), command=command)

    # Add nodes and edges from connection records
    for _, row in cmd_conns.iterrows():
        if row['from'] not in G:
            G.add_node(row['from'], node_type=row['from_type'])
        if row['to'] not in G:
            G.add_node(row['to'], node_type=row['to_type'])
        G.add_edge(row['from'], row['to'])

    # Enrich step nodes with descriptions
    step_descs = cmd_actions.drop_duplicates('step').set_index('step')['step_description']
    for step, desc in step_descs.items():
        if step in G:
            G.nodes[step]['description'] = desc

    # Enrich transition nodes with expressions and termination flag
    for _, row in cmd_transitions.iterrows():
        if row['transition'] in G:
            G.nodes[row['transition']]['expression'] = row['expression']
            G.nodes[row['transition']]['termination'] = row['termination']

    # Mark the initial step
    for cmd, sfc_block in _iter_commands(fhx_data):
        if cmd == command:
            m = _INITIAL_STEP_RE.search(sfc_block)
            if m and m.group(1) in G:
                G.nodes[m.group(1)]['initial'] = True
            break

    return G
