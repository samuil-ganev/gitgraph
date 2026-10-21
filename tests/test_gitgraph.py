"""Verify the pure-python reader against the real git binary."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitgraph.graph import build_graph
from gitgraph.repo import Repo

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git not available")

ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test",
    GIT_AUTHOR_EMAIL="test@example.com",
    GIT_COMMITTER_NAME="Test",
    GIT_COMMITTER_EMAIL="test@example.com",
    GIT_CONFIG_NOSYSTEM="1",
    HOME=os.environ.get("HOME", "/tmp"),
)


def run(cwd, *args, check=True):
    return subprocess.run(
        [GIT, *args], cwd=cwd, env=ENV, check=check, capture_output=True, text=True
    )


def commit(cwd, message, when):
    env = dict(ENV, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(
        [GIT, "commit", "--allow-empty", "-m", message],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return run(cwd, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo_dir(tmp_path):
    path = str(tmp_path / "sample")
    os.makedirs(path)
    run(path, "init", "-b", "main")
    run(path, "config", "user.name", "Test")
    run(path, "config", "user.email", "test@example.com")
    run(path, "config", "commit.gpgsign", "false")

    commit(path, "initial", "2024-01-01T10:00:00 +0000")
    commit(path, "second", "2024-01-02T10:00:00 +0000")

    run(path, "checkout", "-b", "feature")
    commit(path, "feature work", "2024-01-03T10:00:00 +0000")

    run(path, "checkout", "main")
    commit(path, "main work", "2024-01-04T10:00:00 +0000")

    run(path, "merge", "--no-ff", "feature", "-m", "merge feature")

    run(path, "tag", "-a", "v1.0", "-m", "release 1.0")
    run(path, "tag", "lightweight")

    # Force everything into a packfile so delta resolution is exercised.
    run(path, "repack", "-a", "-d", "-f")
    run(path, "prune")
    return path


def refs_under(repo_dir):
    out = run(
        repo_dir,
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    ).stdout.split()
    return [r for r in out if not r.endswith("/HEAD")]


def test_reachable_set_matches_git(repo_dir):
    repo = Repo(repo_dir)
    graph = build_graph(repo)
    ours = set(graph.commits)
    git_set = set(run(repo_dir, "rev-list", *refs_under(repo_dir)).stdout.split())
    assert ours == git_set


def test_parents_match_git(repo_dir):
    repo = Repo(repo_dir)
    graph = build_graph(repo)
    for sha, parsed in graph.commits.items():
        real = run(repo_dir, "rev-list", "-1", "--parents", sha).stdout.split()[1:]
        assert list(parsed.parents) == real, sha


def test_annotated_tag_is_peeled(repo_dir):
    repo = Repo(repo_dir)
    tags = {r.short: r for r in repo.refs if r.kind == "tag"}
    assert "v1.0" in tags and "lightweight" in tags
    expected = run(repo_dir, "rev-list", "-1", "v1.0").stdout.strip()
    assert tags["v1.0"].sha == expected
    assert (
        tags["lightweight"].sha
        == run(repo_dir, "rev-list", "-1", "lightweight").stdout.strip()
    )


def test_object_content_matches_git(repo_dir):
    repo = Repo(repo_dir)
    head = run(repo_dir, "rev-parse", "HEAD").stdout.strip()
    obj_type, data = repo.reader.read(head)
    assert obj_type == "commit"
    expected = run(repo_dir, "cat-file", "commit", head).stdout
    assert data.decode() == expected


def test_loose_and_packed_agree(tmp_path):
    path = str(tmp_path / "loose")
    os.makedirs(path)
    run(path, "init", "-b", "main")
    run(path, "config", "user.name", "Test")
    run(path, "config", "user.email", "test@example.com")
    sha = commit(path, "only", "2024-02-01T10:00:00 +0000")

    loose_repo = Repo(path)
    loose_data = loose_repo.reader.read(sha)

    run(path, "repack", "-a", "-d")
    packed_repo = Repo(path)
    packed_data = packed_repo.reader.read(sha)

    assert loose_data == packed_data


def test_layered_dag_invariants(repo_dir):
    repo = Repo(repo_dir)
    graph = build_graph(repo)

    children = {}
    for sha, commit in graph.commits.items():
        for parent in commit.parents:
            if parent in graph.commits:
                children.setdefault(parent, []).append(sha)

    # Every directed edge goes from a lower layer to a strictly higher layer,
    # which also proves the graph is acyclic.
    for sha, commit in graph.commits.items():
        for parent in commit.parents:
            if parent in graph.commits:
                assert graph.layers[sha] < graph.layers[parent]

    # Sources (no incoming edges, i.e. branch tips) sit on layer 0.
    for sha in graph.commits:
        if not children.get(sha):
            assert graph.layers[sha] == 0


def test_head_detection(repo_dir):
    repo = Repo(repo_dir)
    assert repo.current_branch() == "main"
    assert repo.head_sha == run(repo_dir, "rev-parse", "HEAD").stdout.strip()
