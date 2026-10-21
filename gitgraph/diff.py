"""Compute the file-level changes of a commit against its first parent.

Trees are parsed by hand (``mode SP name NUL sha`` entries) and blobs are
compared line by line with :mod:`difflib` from the standard library. The result
is a plain, JSON-friendly structure that :mod:`gitgraph.render_html` embeds in
the page so the details can be browsed offline.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .graph import Graph
from .repo import Commit, Repo

MAX_DIFF_LINES = 4000
_BINARY_SNIFF = b"\0"
_TREE_MODES = ("40000", "040000")
_SUBMODULE_MODE = "160000"


@dataclass
class FileChange:
    path: str
    status: str  # "added" | "deleted" | "modified" | "submodule"
    insertions: int = 0
    deletions: int = 0
    binary: bool = False
    diff: str = ""

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "binary": self.binary,
            "diff": self.diff,
        }


def parse_tree(data: bytes) -> List[Tuple[str, str, str]]:
    """Parse a tree object into ``(mode, name, sha_hex)`` entries."""
    entries: List[Tuple[str, str, str]] = []
    pos = 0
    end = len(data)
    while pos < end:
        space = data.index(b" ", pos)
        mode = data[pos:space].decode("ascii")
        nul = data.index(b"\0", space)
        name = data[space + 1 : nul].decode("utf-8", "replace")
        sha = data[nul + 1 : nul + 21].hex()
        entries.append((mode, name, sha))
        pos = nul + 21
    return entries


class _TreeReader:
    """Recursively flatten trees into ``path -> (mode, blob_sha)`` maps."""

    def __init__(self, reader):
        self.reader = reader
        self._cache: Dict[str, Dict[str, Tuple[str, str]]] = {}

    def flatten(self, tree_sha: Optional[str]) -> Dict[str, Tuple[str, str]]:
        if not tree_sha:
            return {}
        cached = self._cache.get(tree_sha)
        if cached is not None:
            return cached

        flat: Dict[str, Tuple[str, str]] = {}
        try:
            obj_type, data = self.reader.read(tree_sha)
        except Exception:
            obj_type, data = "", b""
        if obj_type == "tree":
            for mode, name, sha in parse_tree(data):
                if mode in _TREE_MODES:
                    for sub_path, entry in self.flatten(sha).items():
                        flat[name + "/" + sub_path] = entry
                else:
                    flat[name] = (mode, sha)
        self._cache[tree_sha] = flat
        return flat


class Differ:
    """Turn commits into their file changes against the first parent."""

    def __init__(self, reader, max_diff_lines: int = MAX_DIFF_LINES):
        self.reader = reader
        self.trees = _TreeReader(reader)
        self.max_diff_lines = max_diff_lines

    def commit_changes(
        self, commit: Commit, parent: Optional[Commit]
    ) -> List[FileChange]:
        old = self.trees.flatten(parent.tree) if parent else {}
        new = self.trees.flatten(commit.tree)
        changes: List[FileChange] = []
        for path in sorted(set(old) | set(new)):
            before = old.get(path)
            after = new.get(path)
            if before is not None and after is not None and before == after:
                continue
            changes.append(self._change(path, before, after))
        return changes

    def _blob(self, sha: str) -> bytes:
        try:
            obj_type, data = self.reader.read(sha)
        except Exception:
            return b""
        return data if obj_type == "blob" else b""

    def _change(
        self,
        path: str,
        before: Optional[Tuple[str, str]],
        after: Optional[Tuple[str, str]],
    ) -> FileChange:
        if before is None:
            status = "added"
        elif after is None:
            status = "deleted"
        else:
            status = "modified"

        mode = (after or before)[0]
        if mode == _SUBMODULE_MODE:
            return FileChange(path=path, status="submodule")

        old_data = self._blob(before[1]) if before else b""
        new_data = self._blob(after[1]) if after else b""
        binary = _BINARY_SNIFF in old_data or _BINARY_SNIFF in new_data
        change = FileChange(path=path, status=status, binary=binary)
        if binary:
            return change

        old_lines = old_data.decode("utf-8", "replace").splitlines()
        new_lines = new_data.decode("utf-8", "replace").splitlines()
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile="a/" + path,
                tofile="b/" + path,
                lineterm="",
            )
        )
        for line in diff_lines:
            if line.startswith("+") and not line.startswith("+++"):
                change.insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                change.deletions += 1

        truncated = len(diff_lines) > self.max_diff_lines
        if truncated:
            diff_lines = diff_lines[: self.max_diff_lines]
        change.diff = "\n".join(diff_lines)
        if truncated:
            change.diff += "\n... diff truncated at %d lines ..." % self.max_diff_lines
        return change


def collect_changes(
    repo: Repo, graph: Graph, max_diff_lines: int = MAX_DIFF_LINES
) -> Dict[str, List[dict]]:
    """Return ``{commit_sha: [file change dicts]}`` for every commit."""
    differ = Differ(repo.reader, max_diff_lines)
    result: Dict[str, List[dict]] = {}
    for sha, commit in graph.commits.items():
        parent: Optional[Commit] = None
        for parent_sha in commit.parents:
            if parent_sha in graph.commits:
                parent = graph.commits[parent_sha]
                break
        result[sha] = [c.as_dict() for c in differ.commit_changes(commit, parent)]
    return result
