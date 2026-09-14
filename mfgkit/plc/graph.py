"""
Binary logic dependency graph for ControlLogix ladder rungs.

Each node in the graph is either:
  - A PLC tag  (node_type='tag')   — shared across rungs
  - An AND gate (node_type='and')  — named AND:{program}:{routine}:{rung}:{n}
  - An OR  gate (node_type='or')   — named  OR:{program}:{routine}:{rung}:{n}

A directed edge A→B means "A is an input to B."  Edges carry:
  edge_type : instruction name lower-cased (xic, xio, ote, otl, otu, mov, …)
  program, routine, rung_num : rung coordinates for traceability

Typical usage
-------------
    from plc_rungs import ControlLogix_Rungs
    from plc_tags  import ControlLogix_Tags
    from plc_graph import build_logic_graph, plot_logic_graph, subgraph_around

    rungs = ControlLogix_Rungs('data/raw_rungs/WT_CLX.L5X')
    tags  = ControlLogix_Tags('data/raw_tags/WT_CLX-Controller-Tags.CSV')

    G = build_logic_graph(rungs.df, tags=tags)

    sub = subgraph_around(G, 'MY_OUTPUT_TAG', depth=2)
    fig = plot_logic_graph(sub)
    fig.show()
"""

import re
import itertools
import networkx as nx
import pandas as pd


# ============================================================
# Instruction categories
# ============================================================

_CONTACT_INSTRS = frozenset({'XIC', 'XIO', 'ONS', 'OSR', 'OSF'})
_COIL_INSTRS    = frozenset({'OTE', 'OTL', 'OTU'})
_DATA_SRC_DST   = frozenset({'MOV', 'COP', 'FFL', 'FFU', 'CLR'})
_MATH_INSTRS    = frozenset({
    'ADD', 'SUB', 'MUL', 'DIV', 'MOD',
    'AND', 'OR',  'XOR', 'NOT',
    'SQR', 'ABS', 'NEG', 'SCP', 'MVM',
})
_CMP_INSTRS = frozenset({'EQU', 'NEQ', 'GRT', 'GEQ', 'LES', 'LEQ'})

_LITERAL_RE = re.compile(r'^[\d\-\.\?#]+$')


def _is_tag(s: str) -> bool:
    s = s.strip()
    return bool(s) and not _LITERAL_RE.match(s)


def _first_tag(args_str: str):
    first = args_str.split(',')[0].strip()
    return first if _is_tag(first) else None


def _ensure_node(G, node_id, desc_lookup, node_type='tag'):
    if node_id not in G:
        desc = desc_lookup.get(node_id, '') if node_type == 'tag' else ''
        G.add_node(node_id, node_type=node_type, description=desc)


# ============================================================
# Tokenizer
# ============================================================
#
# Tokens:
#   ('instr', NAME, args_str)   — instruction with parenthesised args
#   ('open',)                   — '[' outside parens
#   ('close',)                  — ']' outside parens
#   ('sep',)                    — ',' outside parens and inside brackets

def _tokenize(rung_text: str):
    text = rung_text.strip().rstrip(';').strip()
    i, n = 0, len(text)
    paren_depth   = 0
    bracket_depth = 0

    while i < n:
        c = text[i]

        if c == '[' and paren_depth == 0:
            yield ('open',)
            bracket_depth += 1
            i += 1

        elif c == ']' and paren_depth == 0:
            yield ('close',)
            bracket_depth = max(bracket_depth - 1, 0)
            i += 1

        elif c == ',' and paren_depth == 0 and bracket_depth > 0:
            yield ('sep',)
            i += 1

        elif c.isalpha() or c == '_':
            j = i
            while j < n and (text[j].isalnum() or text[j] == '_'):
                j += 1
            name = text[i:j]
            i = j
            if i < n and text[i] == '(':
                # Find matching closing paren
                k, depth = i + 1, 1
                while k < n and depth > 0:
                    if text[k] == '(':
                        depth += 1
                    elif text[k] == ')':
                        depth -= 1
                    k += 1
                args = text[i + 1:k - 1]
                yield ('instr', name.upper(), args)
                i = k

        else:
            i += 1


# ============================================================
# Parser: token list → parse tree
# ============================================================
#
# Tree nodes:
#   ('instr', NAME, args_str)         — single instruction (leaf)
#   ('branch', [seq1, seq2, ...])     — parallel branches (OR of sequences)
# A sequence is a list of tree nodes.

def _parse(tokens) -> list:
    tlist = list(tokens)
    pos   = [0]

    def parse_seq(stop_on: set) -> list:
        seq = []
        while pos[0] < len(tlist):
            tok = tlist[pos[0]]
            if tok[0] in stop_on:
                break
            elif tok[0] == 'open':
                pos[0] += 1
                branches = []
                branches.append(parse_seq({'sep', 'close'}))
                while pos[0] < len(tlist) and tlist[pos[0]][0] == 'sep':
                    pos[0] += 1
                    branches.append(parse_seq({'sep', 'close'}))
                if pos[0] < len(tlist) and tlist[pos[0]][0] == 'close':
                    pos[0] += 1
                seq.append(('branch', branches))
            elif tok[0] == 'instr':
                seq.append(tok)
                pos[0] += 1
            else:
                pos[0] += 1
        return seq

    return parse_seq(set())


# ============================================================
# Condition AST extraction
# ============================================================
#
# Condition AST types:
#   ('contact', INSTR, tag)       — leaf: XIC / XIO / etc.
#   ('and',     [child, ...])     — AND
#   ('or',      [child, ...])     — OR

def _seq_to_condition(seq: list):
    """Return a condition AST for the contacts/branches in a sequence, or None."""
    parts = []
    for item in seq:
        if item[0] == 'instr':
            name = item[1]
            if name in _CONTACT_INSTRS:
                tag = _first_tag(item[2])
                if tag:
                    parts.append(('contact', name, tag))
            elif name in _CMP_INSTRS:
                for arg in item[2].split(',')[:2]:
                    if _is_tag(arg.strip()):
                        parts.append(('contact', name, arg.strip()))
        elif item[0] == 'branch':
            branch_conds = [_seq_to_condition(b) for b in item[1]]
            branch_conds = [c for c in branch_conds if c is not None]
            if len(branch_conds) == 1:
                parts.append(branch_conds[0])
            elif len(branch_conds) > 1:
                parts.append(('or', branch_conds))
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else ('and', parts)


def _combine(a, b):
    """AND two optional condition ASTs together."""
    if a is None:
        return b
    if b is None:
        return a
    return ('and', [a, b])


# ============================================================
# Output collection with driving conditions
# ============================================================
#
# Yields tuples:
#   ('coil', instr, tag,        driving_cond)
#   ('data', instr, (src, dst), driving_cond)
#   ('math', instr, (src, dst), None)

def _collect_outputs(seq: list, outer_cond=None):
    """
    Walk a sequence and yield output tuples with their boolean driving conditions.

    outer_cond is the condition inherited from the enclosing bracket context.
    Contacts accumulate in 'preceding' and feed _seq_to_condition for each output
    we encounter further along the sequence.

    When a bracket is encountered the processing is recursive:
    - each branch receives the conditions accumulated so far (before the bracket)
      combined with outer_cond as its outer context.
    - After the bracket, the bracket itself is added to 'preceding' so that
      subsequent top-level outputs see the combined OR condition.
    """
    preceding = []   # contact/branch items only

    for item in seq:
        if item[0] == 'instr':
            name = item[1]
            args = item[2]

            if name in _CONTACT_INSTRS or name in _CMP_INSTRS:
                preceding.append(item)

            elif name in _COIL_INSTRS:
                tag = _first_tag(args)
                if tag:
                    yield ('coil', name, tag,
                           _combine(outer_cond, _seq_to_condition(preceding)))

            elif name in _DATA_SRC_DST:
                parts = [a.strip() for a in args.split(',')]
                if len(parts) >= 2 and _is_tag(parts[0]) and _is_tag(parts[1]):
                    yield ('data', name, (parts[0], parts[1]),
                           _combine(outer_cond, _seq_to_condition(preceding)))

            elif name in _MATH_INSTRS:
                parts = [a.strip() for a in args.split(',')]
                if len(parts) >= 2 and _is_tag(parts[-1]):
                    for src in parts[:-1]:
                        if _is_tag(src):
                            yield ('math', name, (src, parts[-1]), None)

        elif item[0] == 'branch':
            # Build the combined outer condition for everything inside the bracket:
            # everything that was true *before* this bracket in the current sequence.
            branch_outer = _combine(outer_cond, _seq_to_condition(preceding))

            for branch_seq in item[1]:
                yield from _collect_outputs(branch_seq, branch_outer)

            # The bracket itself becomes part of the preceding context for
            # any outputs that come *after* this bracket at the top level.
            preceding.append(item)


# ============================================================
# Condition AST → graph nodes
# ============================================================

def _materialize(G, ast, prefix: str, counter, desc_lookup: dict):
    """
    Create graph nodes/edges for a condition AST.
    Returns (node_id, edge_label) where edge_label describes the edge
    type that should be used when connecting this node to its parent.
    Returns (None, None) for an empty AST.
    """
    if ast is None:
        return None, None

    if ast[0] == 'contact':
        _, instr, tag = ast
        _ensure_node(G, tag, desc_lookup, 'tag')
        return tag, instr.lower()

    elif ast[0] in ('and', 'or'):
        op, children = ast[0], ast[1]
        if len(children) == 1:
            return _materialize(G, children[0], prefix, counter, desc_lookup)

        node_id = f'{op.upper()}:{prefix}:{next(counter)}'
        G.add_node(node_id, node_type=op, label=op.upper())

        for child in children:
            child_node, child_label = _materialize(G, child, prefix, counter, desc_lookup)
            if child_node:
                G.add_edge(child_node, node_id, edge_type=child_label or op)

        return node_id, op

    return None, None


# ============================================================
# Public: graph builder
# ============================================================

def build_logic_graph(rungs_df: pd.DataFrame, tags=None) -> nx.MultiDiGraph:
    """
    Build a binary logic dependency graph from a ControlLogix rung DataFrame.

    Parameters
    ----------
    rungs_df : DataFrame with columns program, routine, rung_num, rung_text.
               Typically ControlLogix_Rungs(...).df
    tags     : optional ControlLogix_Tags instance to populate node descriptions

    Returns
    -------
    nx.MultiDiGraph
      Nodes carry:  node_type ('tag' | 'and' | 'or'),  description (str)
      Edges carry:  edge_type (str),  program, routine, rung_num
    """
    G = nx.MultiDiGraph()

    desc_lookup: dict = {}
    if tags is not None and 'tag_description' in tags.tags.columns:
        desc_lookup = (
            tags.tags.dropna(subset=['tag_name'])
                     .set_index('tag_name')['tag_description']
                     .to_dict()
        )

    for _, row in rungs_df.iterrows():
        rung_text = row.get('rung_text') or ''
        if not rung_text:
            continue

        program  = str(row.get('program',  ''))
        routine  = str(row.get('routine',  ''))
        rung_num = str(row.get('rung_num', ''))
        prefix   = f'{program}:{routine}:{rung_num}'
        meta     = {'program': program, 'routine': routine, 'rung_num': rung_num}
        counter  = itertools.count()

        try:
            tree = _parse(_tokenize(rung_text))
        except Exception:
            continue

        for kind, instr, payload, driving_cond in _collect_outputs(tree):

            if kind == 'coil':
                tag = payload
                _ensure_node(G, tag, desc_lookup, 'tag')
                if driving_cond is not None:
                    cond_node, _ = _materialize(G, driving_cond, prefix, counter, desc_lookup)
                    if cond_node:
                        G.add_edge(cond_node, tag, edge_type=instr.lower(), **meta)

            elif kind == 'data':
                src, dst = payload
                _ensure_node(G, src, desc_lookup, 'tag')
                _ensure_node(G, dst, desc_lookup, 'tag')
                G.add_edge(src, dst, edge_type=instr.lower(), **meta)

            elif kind == 'math':
                src, dst = payload
                _ensure_node(G, src, desc_lookup, 'tag')
                _ensure_node(G, dst, desc_lookup, 'tag')
                G.add_edge(src, dst, edge_type=instr.lower(), **meta)

    return G


# ============================================================
# Public: query helpers
# ============================================================

def edges_to_df(G: nx.MultiDiGraph) -> pd.DataFrame:
    """Flatten all graph edges to a DataFrame for inspection or export."""
    rows = [{'src': u, 'dst': v, **data} for u, v, data in G.edges(data=True)]
    return pd.DataFrame(rows)


def subgraph_around(G: nx.MultiDiGraph, tag: str, depth: int = 2) -> nx.MultiDiGraph:
    """
    Return the induced subgraph of all nodes within *depth* hops of *tag*
    in either direction (predecessors and successors), following through
    AND/OR logic nodes transparently.
    """
    nodes   = {tag}
    frontier = {tag}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            nxt |= set(G.predecessors(n))
            nxt |= set(G.successors(n))
        nxt -= nodes
        nodes   |= nxt
        frontier = nxt
    return G.subgraph(nodes).copy()


# ============================================================
# Public: visualisation  (requires plotly)
# ============================================================

_NODE_STYLE = {
    #            marker symbol     fill colour   base size  text anchor          text colour
    'tag': dict(symbol='circle',  color='#2176ae', size=14, anchor='middle left',  tcolor='#222'),
    'and': dict(symbol='square',  color='#2ca02c', size=20, anchor='middle center', tcolor='white'),
    'or':  dict(symbol='diamond', color='#e07b00', size=20, anchor='middle center', tcolor='white'),
}


def _lr_layout(G: nx.DiGraph) -> dict:
    """
    Left-to-right hierarchical layout, cycle-tolerant.

    Collapses strongly-connected components (feedback loops in PLC logic)
    into single virtual nodes, computes longest-path ranks on the resulting
    DAG, then maps ranks back to original nodes.  Members of the same SCC
    share a rank and are stacked vertically within their column.

    Returns {node_id: (x, y)} with x in [0, 1] (left = inputs, right = outputs)
    and y centred around 0 within each column.
    """
    from collections import defaultdict

    if not G.nodes:
        return {}

    # Condense SCCs → always a DAG, even with feedback loops
    cond = nx.condensation(G)

    c_rank = {n: 0 for n in cond.nodes()}
    for n in nx.topological_sort(cond):       # safe: condensation is always a DAG
        for s in cond.successors(n):
            c_rank[s] = max(c_rank[s], c_rank[n] + 1)

    # Map ranks back to original nodes
    rank: dict = {}
    for c_node, data in cond.nodes(data=True):
        for orig in data['members']:
            rank[orig] = c_rank[c_node]

    by_rank: dict = defaultdict(list)
    for n, r in rank.items():
        by_rank[r].append(n)

    max_rank = max(by_rank) if by_rank else 0
    max_col  = max(len(v) for v in by_rank.values())
    y_step   = max(1.3, max_col / max(max_rank + 1, 1) * 0.5)

    # --- Variable column x-positions based on label width ----------------
    # Gate nodes show "AND"/"OR" (3 chars); tag nodes up to 28 chars.
    # Each column gap = widest label in the PREVIOUS column + a fixed margin,
    # so 'middle left' labels never overlap the next column's node markers.
    _GATE_CHARS = 3
    _GAP        = 5   # extra char-units of breathing room

    def _label_chars(n):
        ntype = G.nodes[n].get('node_type', 'tag')
        return _GATE_CHARS if ntype in ('and', 'or') else min(len(n), 28)

    sorted_ranks = sorted(by_rank.keys())
    col_width    = {r: max(_label_chars(n) for n in by_rank[r])
                    for r in sorted_ranks}

    cum_x: dict = {sorted_ranks[0]: 0}
    for i in range(1, len(sorted_ranks)):
        prev_r        = sorted_ranks[i - 1]
        cum_x[sorted_ranks[i]] = cum_x[prev_r] + col_width[prev_r] + _GAP

    max_cum = max(cum_x.values()) or 1

    pos = {}
    for r, nodes in by_rank.items():
        nodes.sort()
        n_col = len(nodes)
        x     = cum_x[r] / max_cum
        for i, n in enumerate(nodes):
            y = -(i - (n_col - 1) / 2.0) * y_step
            pos[n] = (x, y)
    return pos


def plot_logic_graph(G: nx.MultiDiGraph, center_tag: str = None,
                     depth: int = 2):
    """
    Return an interactive Plotly figure of the logic graph, laid out
    left-to-right (inputs → AND/OR gates → outputs).

    Parameters
    ----------
    G          : graph from build_logic_graph (or subgraph_around)
    center_tag : if given, restrict to the neighbourhood within *depth* hops
    depth      : neighbourhood depth when center_tag is specified
    """
    import plotly.graph_objects as go

    if center_tag is not None:
        G = subgraph_around(G, center_tag, depth=depth)
    if len(G) == 0:
        raise ValueError('Graph (or subgraph) is empty.')

    simple = nx.DiGraph(G)
    pos    = _lr_layout(simple)

    in_deg  = dict(simple.in_degree())
    out_deg = dict(simple.out_degree())

    all_x = [p[0] for p in pos.values()]
    all_y = [p[1] for p in pos.values()]
    x_range = max(all_x) - min(all_x) if all_x else 1
    y_range = max(all_y) - min(all_y) if all_y else 1

    # Pull the arrowhead tip back ~half a marker radius so it sits at the
    # edge rather than dead-centre.  Express in data units: assume ~600 px
    # per x-unit as a rough middle ground; node radius ≈ 9 px → 0.015 units.
    shrink_scale = x_range * 0.015 or 0.01

    # Figure size: ~150 px per column, 45 px per row, min 500×400
    n_cols = len(set(round(x * 1000) for x in all_x)) if all_x else 1
    n_rows = max(
        len([n for n in G.nodes()
             if round(pos[n][0] * 1000) == round(x * 1000)])
        for x in all_x
    ) if all_x else 1
    fig_w = max(700,  n_cols * 160)
    fig_h = max(400,  n_rows * 55 + 100)

    # --- Node display label -----------------------------------------------
    def _display_label(node_id, ntype):
        if ntype in ('and', 'or'):
            return ntype.upper()
        return node_id if len(node_id) <= 28 else node_id[:26] + '…'

    # --- Arrow annotations ------------------------------------------------
    # Group parallel edges (same u→v pair) so we can fan them out
    # perpendicularly to avoid stacking.
    from collections import defaultdict
    edge_groups: dict = defaultdict(list)
    for u, v, data in G.edges(data=True):
        edge_groups[(u, v)].append(data)

    jitter = max(0.06, y_range * 0.018)   # perpendicular offset per step

    annotations = []
    for (u, v), edge_list in edge_groups.items():
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        dx, dy = x1 - x0, y1 - y0
        length = (dx**2 + dy**2) ** 0.5 or 1e-9
        # Unit perpendicular (rotate 90°)
        px, py = -dy / length, dx / length

        n = len(edge_list)
        for i, _ in enumerate(edge_list):
            offset = (i - (n - 1) / 2.0) * jitter
            x0j = x0 + px * offset
            y0j = y0 + py * offset
            x1j = x1 + px * offset
            y1j = y1 + py * offset
            # Shrink tip away from destination marker
            dxj = x1j - x0j
            dyj = y1j - y0j
            lj = (dxj**2 + dyj**2) ** 0.5 or 1e-9
            annotations.append(dict(
                ax=x0j, ay=y0j, axref='x', ayref='y',
                x=x1j - dxj / lj * shrink_scale,
                y=y1j - dyj / lj * shrink_scale,
                xref='x', yref='y',
                showarrow=True,
                arrowhead=2, arrowsize=1.1, arrowwidth=1.2,
                arrowcolor='#999',
            ))

    # --- Node traces (one per node type for legend) -----------------------
    traces = []
    for ntype, style in _NODE_STYLE.items():
        ns = [(n, d) for n, d in G.nodes(data=True) if d.get('node_type') == ntype]
        if not ns:
            continue

        xs      = [pos[n][0] for n, _ in ns]
        ys      = [pos[n][1] for n, _ in ns]
        labels  = [_display_label(n, ntype) for n, _ in ns]
        hovers  = []
        for n, d in ns:
            desc  = d.get('description', '')
            hover = f'<b>{n}</b>'
            if desc:
                hover += f'<br><i>{desc}</i>'
            hover += f'<br>in={in_deg[n]}  out={out_deg[n]}'
            hovers.append(hover)

        sizes = [style['size'] + min(in_deg[n] + out_deg[n], 12) for n, _ in ns]

        traces.append(go.Scatter(
            x=xs, y=ys,
            mode='markers+text',
            marker=dict(
                symbol=style['symbol'],
                size=sizes,
                color=style['color'],
                line=dict(width=1.5, color='white'),
            ),
            text=labels,
            textposition=style['anchor'],
            textfont=dict(size=9, color=style['tcolor']),
            hovertext=hovers,
            hoverinfo='text',
            name=ntype.upper(),
        ))

    title = (f'Logic graph — {center_tag}  (depth {depth})'
             if center_tag else 'Logic graph')

    pad_x = x_range * 0.05 or 0.05
    pad_y = y_range * 0.10 or 0.5

    fig = go.Figure(
        data=traces,
        layout=go.Layout(
            title=dict(text=title, font=dict(size=14)),
            width=fig_w, height=fig_h,
            hovermode='closest',
            annotations=annotations,
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                       range=[min(all_x) - pad_x, max(all_x) + pad_x * 5]),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                       range=[min(all_y) - pad_y, max(all_y) + pad_y]),
            margin=dict(l=10, r=10, t=50, b=40),
            legend=dict(orientation='h', y=-0.07),
        ),
    )
    return fig


# ============================================================
# Force-directed alternative: minimises overlap
# ============================================================

def _push_apart(pos: dict, min_dist: float = 0.12, iterations: int = 40) -> None:
    """
    In-place iterative overlap removal.  Pairs of nodes closer than
    min_dist are pushed apart along their connecting vector until no
    pair violates the threshold or max iterations is reached.
    Skipped for very large graphs (n > 200) where it becomes slow.
    """
    nodes = list(pos.keys())
    n = len(nodes)
    if n > 200:
        return
    for _ in range(iterations):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                xi, yi = pos[nodes[i]]
                xj, yj = pos[nodes[j]]
                dx, dy = xi - xj, yi - yj
                dist = (dx ** 2 + dy ** 2) ** 0.5
                if dist < min_dist:
                    if dist < 1e-9:          # coincident: push in arbitrary dir
                        dx, dy, dist = 1.0, 0.0, 1.0
                    push = (min_dist - dist) / 2.0
                    nx_ = dx / dist * push
                    ny_ = dy / dist * push
                    pos[nodes[i]] = (xi + nx_, yi + ny_)
                    pos[nodes[j]] = (xj - nx_, yj - ny_)
                    moved = True
        if not moved:
            break


def plot_logic_graph_force(G: nx.MultiDiGraph, center_tag: str = None,
                           depth: int = 2, show_labels: str | bool = 'auto'):
    """
    Force-directed layout that minimises node overlap.

    Uses Kamada-Kawai for small subgraphs (≤ 50 nodes) and spring layout
    for larger ones, followed by an iterative push-apart pass.
    No left-to-right direction is enforced; arrows still show direction.

    Labels are shown automatically when the subgraph has ≤ 30 nodes and
    hidden (hover only) above that threshold.  Pass show_labels=True/False
    to override.

    Parameters
    ----------
    G          : graph from build_logic_graph (or subgraph_around)
    center_tag : if given, restrict to the neighbourhood within *depth* hops
    depth      : neighbourhood depth when center_tag is specified
    show_labels: True / False / 'auto'
    """
    import math
    import plotly.graph_objects as go
    from collections import defaultdict

    if center_tag is not None:
        G = subgraph_around(G, center_tag, depth=depth)
    if len(G) == 0:
        raise ValueError('Graph (or subgraph) is empty.')

    simple = nx.DiGraph(G)
    n      = len(simple)

    # --- Layout -----------------------------------------------------------
    if n <= 50:
        try:
            pos = nx.kamada_kawai_layout(simple)
        except Exception:
            pos = nx.spring_layout(simple,
                                   k=2.5 / math.sqrt(n), iterations=300, seed=42)
    else:
        pos = nx.spring_layout(simple,
                               k=2.5 / math.sqrt(n), iterations=150, seed=42)

    _push_apart(pos)

    in_deg  = dict(simple.in_degree())
    out_deg = dict(simple.out_degree())

    all_x  = [p[0] for p in pos.values()]
    all_y  = [p[1] for p in pos.values()]
    x_range = max(all_x) - min(all_x) or 1
    y_range = max(all_y) - min(all_y) or 1
    shrink  = x_range * 0.015

    # --- Labels -----------------------------------------------------------
    if show_labels == 'auto':
        show_labels = n <= 30

    def _display_label(node_id, ntype):
        if not show_labels:
            return ''
        if ntype in ('and', 'or'):
            return ntype.upper()
        return node_id if len(node_id) <= 28 else node_id[:26] + '…'

    # --- Arrows -----------------------------------------------------------
    edge_groups: dict = defaultdict(list)
    for u, v, data in G.edges(data=True):
        edge_groups[(u, v)].append(data)

    jitter = max(0.03, y_range * 0.018)

    annotations = []
    for (u, v), edge_list in edge_groups.items():
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        dx, dy = x1 - x0, y1 - y0
        length = (dx ** 2 + dy ** 2) ** 0.5 or 1e-9
        px, py = -dy / length, dx / length
        n_e = len(edge_list)
        for i, _ in enumerate(edge_list):
            offset = (i - (n_e - 1) / 2.0) * jitter
            x0j = x0 + px * offset
            y0j = y0 + py * offset
            x1j = x1 + px * offset
            y1j = y1 + py * offset
            dxj = x1j - x0j
            dyj = y1j - y0j
            lj  = (dxj ** 2 + dyj ** 2) ** 0.5 or 1e-9
            annotations.append(dict(
                ax=x0j, ay=y0j, axref='x', ayref='y',
                x=x1j - dxj / lj * shrink,
                y=y1j - dyj / lj * shrink,
                xref='x', yref='y',
                showarrow=True,
                arrowhead=2, arrowsize=1.1, arrowwidth=1.2,
                arrowcolor='#999',
            ))

    # --- Node traces (one per type) ---------------------------------------
    traces = []
    for ntype, style in _NODE_STYLE.items():
        ns = [(node_id, d) for node_id, d in G.nodes(data=True)
              if d.get('node_type') == ntype]
        if not ns:
            continue

        xs     = [pos[node_id][0] for node_id, _ in ns]
        ys     = [pos[node_id][1] for node_id, _ in ns]
        labels = [_display_label(node_id, ntype) for node_id, _ in ns]
        hovers = []
        for node_id, d in ns:
            desc  = d.get('description', '')
            hover = f'<b>{node_id}</b>'
            if desc:
                hover += f'<br><i>{desc}</i>'
            hover += f'<br>in={in_deg[node_id]}  out={out_deg[node_id]}'
            hovers.append(hover)

        sizes   = [style['size'] + min(in_deg[node_id] + out_deg[node_id], 12)
                   for node_id, _ in ns]
        txtpos  = style['anchor'] if show_labels else 'middle center'

        traces.append(go.Scatter(
            x=xs, y=ys, mode='markers+text',
            marker=dict(symbol=style['symbol'], size=sizes, color=style['color'],
                        line=dict(width=1.5, color='white')),
            text=labels,
            textposition=txtpos,
            textfont=dict(size=9, color=style['tcolor']),
            hovertext=hovers, hoverinfo='text',
            name=ntype.upper(),
        ))

    # --- Figure -----------------------------------------------------------
    side  = max(550, min(1100, int(220 * math.sqrt(n))))
    pad   = max(x_range, y_range) * 0.12

    title = (f'Logic graph — {center_tag}  (depth {depth})'
             if center_tag else 'Logic graph')

    fig = go.Figure(
        data=traces,
        layout=go.Layout(
            title=dict(text=title, font=dict(size=14)),
            width=side, height=side,
            hovermode='closest',
            annotations=annotations,
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                       range=[min(all_x) - pad, max(all_x) + pad]),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                       range=[min(all_y) - pad, max(all_y) + pad]),
            margin=dict(l=10, r=10, t=50, b=40),
            legend=dict(orientation='h', y=-0.07),
        ),
    )
    return fig