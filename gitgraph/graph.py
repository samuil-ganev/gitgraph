"""Build the commit DAG and lay it out as a layered directed graph.

The layout follows the classical (Sugiyama style) approach for drawing a DAG:

1. rank every vertex into a layer using the longest path from a source, so that
   every directed edge goes from a lower layer to a higher layer;
2. break long edges (spanning more than one layer) with dummy vertices so that
   every drawn edge only connects two adjacent layers -- otherwise the curve
   would cut straight through the commits sitting in the layers in between;
3. reduce edge crossings by a few barycenter sweeps within each layer;
4. stack vertices inside each layer.

Because git stores each commit with a pointer to its parent(s), the natural
direction of an edge is ``child -> parent``. Sources (no incoming edges) are the
branch tips, sinks (no outgoing edges) are the root commits.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .repo import Commit, Ref, Repo, parse_commit


@dataclass
class Dummy:
    """A synthetic routing vertex on a long edge (not a real commit)."""

    id: str
    layer: int
    slot: float = 0.0
    remote_only: bool = False


@dataclass
class Edge:
    """A drawn edge between two adjacent layers (real or dummy endpoints)."""

    source: str  # child side
    target: str  # parent side
    terminal: bool  # True when ``target`` is a real commit (gets an arrow head)
    remote_only: bool = False


@dataclass
class Graph:
    git_dir: str
    commits: Dict[str, Commit] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)  # children before parents
    layers: Dict[str, int] = field(default_factory=dict)  # x: topological rank
    slots: Dict[str, float] = field(default_factory=dict)  # y: position in layer
    refs: List[Ref] = field(default_factory=list)
    labels: Dict[str, List[Ref]] = field(default_factory=dict)
    head_sha: Optional[str] = None
    head_ref: Optional[str] = None
    edges: List[Edge] = field(default_factory=list)
    dummies: Dict[str, Dummy] = field(default_factory=dict)
    remote_only: Set[str] = field(default_factory=set)

    @property
    def num_commits(self) -> int:
        return len(self.commits)

    @property
    def num_layers(self) -> int:
        return (max(self.layers.values()) + 1) if self.layers else 0

    @property
    def max_layer_width(self) -> int:
        counts: Dict[int, int] = {}
        for layer_id in self.layers.values():
            counts[layer_id] = counts.get(layer_id, 0) + 1
        return max(counts.values(), default=0)

    def refs_for(self, sha: str) -> List[Ref]:
        return self.labels.get(sha, [])


def _reachable_commits(repo: Repo, tips: List[str]) -> Dict[str, Commit]:
    commits: Dict[str, Commit] = {}
    stack = [sha for sha in tips if sha]
    while stack:
        sha = stack.pop()
        if sha in commits:
            continue
        try:
            obj_type, data = repo.reader.read(sha)
        except Exception:
            continue
        if obj_type != "commit":
            continue
        commit = parse_commit(sha, data)
        commits[sha] = commit
        for parent in commit.parents:
            if parent not in commits:
                stack.append(parent)
    return commits


def _topological_order(commits: Dict[str, Commit]) -> List[str]:
    """Order commits so every child precedes its parents (newest first)."""
    children_count: Dict[str, int] = {sha: 0 for sha in commits}
    for commit in commits.values():
        for parent in commit.parents:
            if parent in children_count:
                children_count[parent] += 1

    heap: List[Tuple[int, str]] = []
    for sha, count in children_count.items():
        if count == 0:
            heapq.heappush(heap, (-commits[sha].timestamp, sha))

    order: List[str] = []
    while heap:
        _neg_ts, sha = heapq.heappop(heap)
        order.append(sha)
        for parent in commits[sha].parents:
            if parent in children_count:
                children_count[parent] -= 1
                if children_count[parent] == 0:
                    heapq.heappush(heap, (-commits[parent].timestamp, parent))
    return order


def _children_map(commits: Dict[str, Commit]) -> Dict[str, List[str]]:
    children: Dict[str, List[str]] = {}
    for sha, commit in commits.items():
        for parent in commit.parents:
            if parent in commits:
                children.setdefault(parent, []).append(sha)
    return children


def _local_reachable(
    commits: Dict[str, Commit], refs: List[Ref], head_sha: Optional[str]
) -> Set[str]:
    """Commits reachable without any remote branch (branches, tags and HEAD)."""
    reachable: Set[str] = set()
    stack = [r.sha for r in refs if r.kind in ("branch", "tag")]
    if head_sha:
        stack.append(head_sha)
    while stack:
        sha = stack.pop()
        if sha in reachable or sha not in commits:
            continue
        reachable.add(sha)
        stack.extend(commits[sha].parents)
    return reachable


def _assign_layers(order: List[str], children: Dict[str, List[str]]) -> Dict[str, int]:
    """Longest-path layering: layer(v) = 0 if no children, else max(layer(c))+1.

    ``order`` is topological with children before parents, so a vertex's
    children are always already ranked when the vertex is visited.
    """
    layer: Dict[str, int] = {}
    for sha in order:
        kids = children.get(sha)
        layer[sha] = max((layer[kid] + 1 for kid in kids), default=0) if kids else 0
    return layer


def _expand(
    commits: Dict[str, Commit],
    layer: Dict[str, int],
    remote_only: Set[str],
) -> Tuple[
    Dict[str, int],
    Dict[str, List[str]],
    Dict[str, List[str]],
    List[Edge],
    Dict[str, Dummy],
]:
    """Split every multi-layer edge into single-layer edges via dummy vertices.

    Returns ``(node_layer, parents, children, edges, dummies)`` for the expanded
    graph. Every edge then connects two adjacent layers, which is what keeps the
    drawn curves from cutting through commits in the layers in between.
    """
    node_layer: Dict[str, int] = dict(layer)
    edges: List[Edge] = []
    dummies: Dict[str, Dummy] = {}

    for sha in commits:
        for parent in commits[sha].parents:
            if parent not in commits:
                continue
            ro = sha in remote_only or parent in remote_only
            span = layer[parent] - layer[sha]
            prev = sha
            for step in range(1, span):
                did = "%s~%s~%d" % (sha, parent, step)
                node_layer[did] = layer[sha] + step
                dummies[did] = Dummy(
                    id=did, layer=layer[sha] + step, remote_only=ro
                )
                edges.append(Edge(prev, did, terminal=False, remote_only=ro))
                prev = did
            edges.append(Edge(prev, parent, terminal=True, remote_only=ro))

    parents: Dict[str, List[str]] = {}
    children: Dict[str, List[str]] = {}
    for edge in edges:
        children.setdefault(edge.target, []).append(edge.source)
        parents.setdefault(edge.source, []).append(edge.target)

    return node_layer, parents, children, edges, dummies


def _group_by_layer(order: List[str], layer: Dict[str, int]) -> Dict[int, List[str]]:
    groups: Dict[int, List[str]] = {}
    for sha in order:
        groups.setdefault(layer[sha], []).append(sha)
    return groups


def _reduce_crossings(
    groups: Dict[int, List[str]],
    children: Dict[str, List[str]],
    parents: Dict[str, List[str]],
    layer: Dict[str, int],
    sweeps: int = 4,
) -> None:
    """Barycenter heuristic: reorder each layer by neighbour averages."""
    layer_ids = sorted(groups)

    def positions(layer_id: int) -> Dict[str, int]:
        return {sha: i for i, sha in enumerate(groups[layer_id])}

    def neighbours(sha: str, mapping: Dict[str, List[str]], want_layer: int):
        return [n for n in mapping.get(sha, ()) if layer[n] == want_layer]

    for sweep in range(sweeps):
        forward = sweep % 2 == 0
        mapping = children if forward else parents
        ordered_ids = layer_ids if forward else list(reversed(layer_ids))
        for layer_id in ordered_ids:
            ref_id = layer_id - 1 if forward else layer_id + 1
            if ref_id not in groups:
                continue
            pos = positions(ref_id)
            current = groups[layer_id]

            def barycenter(sha: str, _pos=pos, _ref=ref_id, _map=mapping):
                ns = [n for n in neighbours(sha, _map, _ref) if n in _pos]
                if not ns:
                    return None
                return sum(_pos[n] for n in ns) / len(ns)

            decorated = [(barycenter(sha), i, sha) for i, sha in enumerate(current)]
            decorated.sort(key=lambda item: (item[0] is None, item[0] or 0.0, item[1]))
            groups[layer_id] = [sha for _b, _i, sha in decorated]


def _assign_slots(groups: Dict[int, List[str]]) -> Dict[str, float]:
    """Vertically centre each layer and stack its vertices."""
    max_len = max((len(v) for v in groups.values()), default=0)
    slots: Dict[str, float] = {}
    for layer_id, shas in groups.items():
        offset = (max_len - len(shas)) / 2.0
        for i, sha in enumerate(shas):
            slots[sha] = offset + i
    return slots


def build_graph(repo: Repo) -> Graph:
    tips = [ref.sha for ref in repo.refs]
    if repo.head_sha:
        tips.append(repo.head_sha)

    commits = _reachable_commits(repo, tips)
    order = _topological_order(commits)
    children = _children_map(commits)
    layer = _assign_layers(order, children)

    reachable = _local_reachable(commits, repo.refs, repo.head_sha)
    remote_only = {sha for sha in commits if sha not in reachable}

    node_layer, parents, expanded_children, edges, dummies = _expand(
        commits, layer, remote_only
    )

    groups = _group_by_layer(order + list(dummies), node_layer)
    _reduce_crossings(groups, expanded_children, parents, node_layer)
    slots = _assign_slots(groups)

    for dummy in dummies.values():
        dummy.slot = slots.get(dummy.id, 0.0)

    graph = Graph(
        git_dir=repo.git_dir,
        commits=commits,
        order=order,
        layers=dict(layer),
        slots={sha: slots[sha] for sha in commits},
        refs=list(repo.refs),
        head_sha=repo.head_sha,
        head_ref=repo.head_ref,
        edges=edges,
        dummies=dummies,
        remote_only=remote_only,
    )

    for ref in repo.refs:
        if ref.sha in commits:
            graph.labels.setdefault(ref.sha, []).append(ref)

    return graph
