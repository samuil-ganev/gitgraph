"""Command line entry point: build the graph and open the visualisation."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from typing import List, Optional

from .diff import collect_changes
from .graph import build_graph
from .render_html import render_html
from .repo import Repo


def _default_output(repo: Repo) -> str:
    base = repo.workdir or os.path.dirname(repo.git_dir) or os.getcwd()
    return os.path.join(base, "gitgraph.html")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gitgraph",
        description="Read a local .git directory and render an interactive commit DAG.",
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="path inside the git repository (default: current directory)",
    )
    parser.add_argument("-o", "--out", default=None, help="output HTML file")
    parser.add_argument(
        "--no-open", action="store_true", help="do not open the browser"
    )
    parser.add_argument("--title", default=None, help="page title")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="skip per-commit file changes and diffs (faster, smaller output)",
    )
    args = parser.parse_args(argv)

    try:
        repo = Repo(args.repo)
    except FileNotFoundError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    graph = build_graph(repo)
    out_path = args.out or _default_output(repo)
    title = args.title or ("git graph - %s" % (repo.workdir or repo.git_dir))
    changes = None if args.no_details else collect_changes(repo, graph)
    render_html(graph, out_path, title, changes=changes)

    branches = sum(1 for r in repo.refs if r.kind == "branch")
    remotes = sum(1 for r in repo.refs if r.kind == "remote")
    tags = sum(1 for r in repo.refs if r.kind == "tag")
    print("repo:     %s" % (repo.workdir or repo.git_dir))
    print(
        "commits:  %d   branches: %d   remotes: %d   tags: %d"
        % (graph.num_commits, branches, remotes, tags)
    )
    print("head:     %s" % (repo.current_branch() or (repo.head_sha or "")[:8]))
    print("output:   %s" % os.path.abspath(out_path))

    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(out_path))

    repo.close()
    return 0
