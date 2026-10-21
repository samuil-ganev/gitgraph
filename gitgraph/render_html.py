"""Render a :class:`~gitgraph.graph.Graph` as a self-contained HTML page."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import List

from .graph import Graph

VIS_CDN = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"

PALETTE = [
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
]

X_SPACING = 190  # horizontal distance between topological layers
Y_SPACING = 46  # vertical distance between vertices inside a layer
KIND_RANK = {"branch": 0, "remote": 1, "tag": 2}


def _color_index(graph: Graph) -> dict:
    """Colour each commit by the first ref (branch/remote/tag) that reaches it."""
    refs = sorted(graph.refs, key=lambda r: (KIND_RANK.get(r.kind, 9), r.name))
    color_of: dict = {}
    for index, ref in enumerate(refs):
        stack = [ref.sha]
        while stack:
            sha = stack.pop()
            if sha in color_of or sha not in graph.commits:
                continue
            color_of[sha] = index
            stack.extend(graph.commits[sha].parents)
    return color_of


def _tooltip(commit, refs) -> str:
    when = ""
    if commit.timestamp:
        when = datetime.fromtimestamp(commit.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    rows = [
        "<div class='tt-sha'>%s</div>" % html.escape(commit.sha),
        "<div class='tt-subject'>%s</div>"
        % html.escape(commit.subject or "(no message)"),
        "<div class='tt-meta'>%s &lt;%s&gt;</div>"
        % (html.escape(commit.author), html.escape(commit.author_email)),
        "<div class='tt-meta'>%s</div>" % html.escape(when),
    ]
    if refs:
        badges = "".join(
            "<span class='badge %s'>%s</span>" % (r.kind, html.escape(r.short))
            for r in refs
        )
        rows.append("<div class='tt-refs'>%s</div>" % badges)
    if len(commit.parents) > 1:
        rows.append(
            "<div class='tt-meta'>merge of %d parents</div>" % len(commit.parents)
        )
    return "".join(rows)


def _build_payload(graph: Graph) -> dict:
    nodes: List[dict] = []
    edges: List[dict] = []
    color_of = _color_index(graph)

    # Commits reachable without any remote branch; everything else is
    # "origin only" and can be hidden by the toggle in the UI.
    local_reachable = set()
    stack = [r.sha for r in graph.refs if r.kind in ("branch", "tag")]
    if graph.head_sha:
        stack.append(graph.head_sha)
    while stack:
        node_sha = stack.pop()
        if node_sha in local_reachable or node_sha not in graph.commits:
            continue
        local_reachable.add(node_sha)
        stack.extend(graph.commits[node_sha].parents)

    for sha in graph.order:
        commit = graph.commits[sha]
        refs = graph.refs_for(sha)
        layer = graph.layers.get(sha, 0)
        slot = graph.slots.get(sha, 0)
        color = PALETTE[color_of.get(sha, 0) % len(PALETTE)]

        label = ""
        if refs:
            label = ", ".join(r.short for r in refs)

        is_head = sha == graph.head_sha
        node = {
            "id": sha,
            "x": layer * X_SPACING,
            "y": slot * Y_SPACING,
            "label": label,
            "title": _tooltip(commit, refs),
            "shape": "dot",
            "size": 11 if refs else 6,
            "color": {
                "background": color,
                "border": "#111111" if is_head else color,
                "highlight": {"background": "#ffffff", "border": color},
            },
            "borderWidth": 4 if is_head else 1,
            "font": {"size": 13, "face": "monospace", "color": "#222"},
            "_refs": [r.short for r in refs],
            "_localRefs": [r.short for r in refs if r.kind != "remote"],
            "_remoteRefs": [r.short for r in refs if r.kind == "remote"],
            "_remoteOnly": sha not in local_reachable,
            "_subject": commit.subject,
            "_author": commit.author,
            "_sha": sha,
        }
        nodes.append(node)

        for parent in commit.parents:
            if parent in graph.commits:
                edges.append({"from": sha, "to": parent})

    refs_list = []
    for index, ref in enumerate(
        sorted(graph.refs, key=lambda r: (KIND_RANK.get(r.kind, 9), r.name))
    ):
        refs_list.append(
            {
                "name": ref.short,
                "kind": ref.kind,
                "sha": ref.sha,
                "color": PALETTE[index % len(PALETTE)],
                "is_head": ref.is_head,
            }
        )

    head = graph.head_ref or ""
    if head.startswith("refs/heads/"):
        head = head[len("refs/heads/") :]
    elif graph.head_sha:
        head = graph.head_sha[:8]

    max_width = graph.max_layer_width
    return {
        "nodes": nodes,
        "edges": edges,
        "refs": refs_list,
        "stats": {
            "commits": graph.num_commits,
            "layers": graph.num_layers,
            "width": max_width,
            "branches": sum(1 for r in refs_list if r["kind"] == "branch"),
            "remotes": sum(1 for r in refs_list if r["kind"] == "remote"),
            "tags": sum(1 for r in refs_list if r["kind"] == "tag"),
            "git_dir": graph.git_dir,
            "head": head,
        },
    }


def render_html(graph: Graph, out_path: str, title: str = "git graph") -> str:
    payload = _build_payload(graph)
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    page = _TEMPLATE.replace("__TITLE__", html.escape(title))
    page = page.replace("__GENERATED__", generated)
    page = page.replace("__VIS_CDN__", VIS_CDN)
    page = page.replace("__DATA__", data_json)

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out_path


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link href="https://unpkg.com/vis-network/styles/vis-network.min.css" rel="stylesheet">
<script src="__VIS_CDN__"></script>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  #app { display: flex; height: 100%; }
  #sidebar {
    width: 300px; min-width: 300px; height: 100%; overflow-y: auto;
    border-right: 1px solid #e0e0e0; background: #fafafa; padding: 14px;
  }
  #graph { flex: 1; height: 100%; background: #ffffff; }
  h1 { font-size: 15px; margin: 0 0 4px; }
  .muted { color: #777; font-size: 12px; }
  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 12px 0; }
  .stat { background: #fff; border: 1px solid #e6e6e6; border-radius: 6px; padding: 6px 8px; }
  .stat b { display: block; font-size: 18px; }
  .stat span { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: .04em; }
  input[type=search] {
    width: 100%; padding: 7px 9px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px;
  }
  .fit-btn {
    margin-top: 8px; width: 100%; padding: 6px 9px; font-size: 12.5px; cursor: pointer;
    border: 1px solid #ccc; border-radius: 6px; background: #fff;
  }
  .fit-btn:hover { background: #f0f0f0; }
  .section-title { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #888; margin: 16px 0 6px; }
  .reflist { list-style: none; margin: 0; padding: 0; }
  .reflist li { display: flex; align-items: center; gap: 7px; padding: 3px 0; font-size: 12.5px; cursor: pointer; }
  .reflist li:hover { background: #f0f0f0; border-radius: 4px; }
  .swatch { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .ref-kind { color: #aaa; font-size: 10px; margin-left: auto; text-transform: uppercase; }
  .head-pill { background: #111; color: #fff; border-radius: 8px; padding: 0 6px; font-size: 10px; }
  div.vis-tooltip {
    position: absolute; background: #1e1e1e; color: #eee; border-radius: 6px;
    padding: 9px 11px; font-size: 12px; max-width: 420px; box-shadow: 0 4px 14px rgba(0,0,0,.3);
    pointer-events: none; z-index: 999;
  }
  .vis-tooltip .tt-sha { font-family: monospace; color: #8ab4f8; margin-bottom: 3px; }
  .vis-tooltip .tt-subject { font-weight: 600; margin-bottom: 5px; }
  .vis-tooltip .tt-meta { color: #bbb; }
  .vis-tooltip .badge { display: inline-block; padding: 1px 6px; border-radius: 8px; margin: 2px 3px 0 0; font-size: 11px; color: #111; }
  .vis-tooltip .badge.branch { background: #8ab4f8; }
  .vis-tooltip .badge.remote { background: #f8b88a; }
  .vis-tooltip .badge.tag { background: #b6f88a; }
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <h1>__TITLE__</h1>
    <div class="muted">generated __GENERATED__</div>
    <div class="stats" id="stats"></div>
    <input type="search" id="search" placeholder="search sha / message / author">
    <button id="fit" class="fit-btn">fit all</button>
    <button id="toggle-origin" class="fit-btn">hide origin</button>
    <div class="section-title">Refs</div>
    <ul class="reflist" id="reflist"></ul>
  </aside>
  <div id="graph"></div>
</div>
<script>
const DATA = __DATA__;

const statsEl = document.getElementById('stats');
const s = DATA.stats;
const statItems = [
  ['commits', s.commits], ['layers', s.layers],
  ['width', s.width], ['branches', s.branches],
  ['remotes', s.remotes], ['tags', s.tags],
  ['head', s.head],
];
statsEl.innerHTML = statItems.map(([k, v]) =>
  `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');

const nodes = new vis.DataSet(DATA.nodes.map(n => ({
  id: n.id, x: n.x, y: n.y, label: n.label, title: n.title,
  shape: n.shape, size: n.size, color: n.color, borderWidth: n.borderWidth,
  font: n.font, fixed: true, physics: false,
})));
const edges = new vis.DataSet(DATA.edges.map((e, i) => ({
  id: i, from: e.from, to: e.to,
})));

const container = document.getElementById('graph');
const network = new vis.Network(container, { nodes, edges }, {
  physics: false,
  layout: { improvedLayout: false },
  interaction: { hover: true, tooltipDelay: 120, navigationButtons: true, keyboard: true, multiselect: true },
  edges: {
    arrows: { to: { enabled: true, scaleFactor: 0.55 } },
    color: { color: '#9aa0a6', highlight: '#111', opacity: 0.8 },
    smooth: { type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.35 },
    width: 1.3,
  },
  nodes: { shape: 'dot' },
});

// Start at the newest commits (leftmost layer) so the user sees the tips first.
network.moveTo({ position: { x: 0, y: 0 }, scale: 0.85, animation: false });
document.getElementById('fit').addEventListener('click', () =>
  network.fit({ animation: { duration: 400 } }));

const refList = document.getElementById('reflist');
DATA.refs.forEach(r => {
  const li = document.createElement('li');
  li.dataset.kind = r.kind;
  li.innerHTML = `<span class="swatch" style="background:${r.color}"></span>`
    + `<span>${r.name}</span>`
    + (r.is_head ? '<span class="head-pill">HEAD</span>' : '')
    + `<span class="ref-kind">${r.kind}</span>`;
  li.addEventListener('click', () => {
    network.focus(r.sha, { scale: 1.1, animation: { duration: 500 } });
    network.selectNodes([r.sha]);
  });
  refList.appendChild(li);
});

// Show/hide remote ("origin") branches: hides origin-only commits and their
// edges, drops origin labels and filters them out of the ref list.
const remoteOnly = new Set(DATA.nodes.filter(n => n._remoteOnly).map(n => n.id));
const toggleOrigin = document.getElementById('toggle-origin');
let showOrigin = true;
function applyOrigin() {
  nodes.update(DATA.nodes.map(n => ({
    id: n.id,
    hidden: !showOrigin && n._remoteOnly,
    label: (showOrigin ? n._refs : n._localRefs).join(', '),
  })));
  edges.update(DATA.edges.map((e, i) => ({
    id: i,
    hidden: !showOrigin && (remoteOnly.has(e.from) || remoteOnly.has(e.to)),
  })));
  refList.querySelectorAll('li[data-kind="remote"]').forEach(li => {
    li.style.display = showOrigin ? '' : 'none';
  });
  toggleOrigin.textContent = showOrigin ? 'hide origin' : 'show origin';
}
toggleOrigin.addEventListener('click', () => {
  showOrigin = !showOrigin;
  applyOrigin();
});

const search = document.getElementById('search');
search.addEventListener('input', () => {
  const q = search.value.trim().toLowerCase();
  if (!q) {
    nodes.update(DATA.nodes.map(n => ({ id: n.id, opacity: 1, borderWidth: n.borderWidth, color: n.color })));
    return;
  }
  const updates = DATA.nodes.map(n => {
    const hay = (n._sha + ' ' + n._subject + ' ' + n._author + ' ' + n._refs.join(' ')).toLowerCase();
    const hit = hay.includes(q);
    return { id: n.id, opacity: hit ? 1 : 0.15, borderWidth: hit ? 4 : n.borderWidth,
             color: hit ? { background: '#ff2d55', border: '#ff2d55' } : n.color };
  });
  nodes.update(updates);
});
</script>
</body>
</html>
"""
