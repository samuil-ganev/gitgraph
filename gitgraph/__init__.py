"""gitgraph - read a local .git directory and render its commit DAG.

The package has no third-party dependencies: it parses the ``.git`` directory
itself (loose objects and packfiles, including delta objects) and produces a
self-contained interactive HTML visualisation.
"""

from .objects import GitObjectError, ObjectReader
from .repo import Commit, Ref, Repo, find_git_dir
from .graph import Graph, build_graph
from .diff import FileChange, collect_changes
from .render_html import render_html

__all__ = [
    "GitObjectError",
    "ObjectReader",
    "Commit",
    "Ref",
    "Repo",
    "find_git_dir",
    "Graph",
    "build_graph",
    "FileChange",
    "collect_changes",
    "render_html",
]

__version__ = "0.1.0"
