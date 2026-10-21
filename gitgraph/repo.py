"""Discovery of a ``.git`` directory, its refs/HEAD and commit parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .objects import ObjectReader

HEAD_REF = "HEAD"


def find_git_dir(path: str) -> Optional[str]:
    """Walk up from ``path`` looking for a ``.git`` directory or gitfile."""
    current = os.path.abspath(path)
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.isdir(candidate):
            return candidate
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
                line = fh.read().strip()
            if line.startswith("gitdir:"):
                target = line.split(":", 1)[1].strip()
                if not os.path.isabs(target):
                    target = os.path.join(current, target)
                return os.path.abspath(target)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


@dataclass
class Ref:
    """A git reference (branch, remote branch or tag)."""

    name: str
    kind: str  # "branch" | "remote" | "tag"
    sha: str  # commit the ref ultimately points at
    target: str  # the object the ref directly points at (may be a tag object)
    symbolic: Optional[str] = None
    is_head: bool = False

    @property
    def short(self) -> str:
        if self.kind == "branch":
            return self.name[len("refs/heads/") :]
        if self.kind == "remote":
            return self.name[len("refs/remotes/") :]
        if self.kind == "tag":
            return self.name[len("refs/tags/") :]
        return self.name


@dataclass
class Commit:
    """A parsed commit object."""

    sha: str
    tree: Optional[str] = None
    parents: List[str] = field(default_factory=list)
    author: str = ""
    author_email: str = ""
    committer: str = ""
    committer_email: str = ""
    timestamp: int = 0
    tz: str = "+0000"
    subject: str = ""
    message: str = ""

    @property
    def short_sha(self) -> str:
        return self.sha[:8]


def _parse_person(line: str) -> Tuple[str, str, int, str]:
    """Parse ``Name <email> 1234567890 +0000`` (without the leading keyword)."""
    try:
        ident, ts, tz = line.rsplit(" ", 2)
    except ValueError:
        return line, "", 0, "+0000"
    if "<" in ident and ">" in ident:
        name = ident[: ident.index("<")].strip()
        email = ident[ident.index("<") + 1 : ident.index(">")].strip()
    else:
        name, email = ident.strip(), ""
    try:
        timestamp = int(ts)
    except ValueError:
        timestamp = 0
    return name, email, timestamp, tz


def parse_commit(sha: str, data: bytes) -> Commit:
    text = data.decode("utf-8", errors="replace")
    header, _, message = text.partition("\n\n")
    commit = Commit(sha=sha, message=message)

    for line in header.split("\n"):
        if line.startswith("tree "):
            commit.tree = line[5:].strip()
        elif line.startswith("parent "):
            commit.parents.append(line[7:].strip())
        elif line.startswith("author "):
            (
                commit.author,
                commit.author_email,
                commit.timestamp,
                commit.tz,
            ) = _parse_person(line[7:])
        elif line.startswith("committer "):
            (
                commit.committer,
                commit.committer_email,
                _ts,
                _tz,
            ) = _parse_person(line[10:])

    commit.subject = message.split("\n", 1)[0].strip()
    return commit


def _parse_packed_refs(git_dir: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return ``(refs, peeled)`` maps from ``packed-refs``."""
    refs: Dict[str, str] = {}
    peeled: Dict[str, str] = {}
    path = os.path.join(git_dir, "packed-refs")
    if not os.path.isfile(path):
        return refs, peeled
    last_ref: Optional[str] = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            if line.startswith("^"):
                if last_ref is not None:
                    peeled[last_ref] = line[1:].strip()
                continue
            try:
                sha, name = line.split(" ", 1)
            except ValueError:
                continue
            refs[name] = sha
            last_ref = name
    return refs, peeled


class Repo:
    """A locally cloned git repository read straight from its ``.git`` dir."""

    def __init__(self, path: str = "."):
        git_dir = find_git_dir(path)
        if git_dir is None:
            raise FileNotFoundError("no .git directory found from %r" % path)
        self.git_dir = git_dir
        self.workdir = (
            os.path.dirname(git_dir) if os.path.basename(git_dir) == ".git" else None
        )
        self.reader = ObjectReader(git_dir)
        self._packed, self._peeled = _parse_packed_refs(git_dir)
        self.head_ref, self.head_sha = self._read_head()
        self.refs = self._collect_refs()

    # -- HEAD ---------------------------------------------------------------
    def _read_head(self) -> Tuple[Optional[str], Optional[str]]:
        path = os.path.join(self.git_dir, HEAD_REF)
        if not os.path.isfile(path):
            return None, None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read().strip()
        if content.startswith("ref:"):
            name = content[4:].strip()
            return name, self._resolve(name)
        return None, content or None

    # -- refs ---------------------------------------------------------------
    def _read_ref_file(self, name: str) -> Optional[str]:
        path = os.path.join(self.git_dir, name)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()

    def _resolve(self, name: str, seen: Optional[set] = None) -> Optional[str]:
        seen = seen or set()
        if name in seen:
            return None
        seen.add(name)
        value = self._read_ref_file(name)
        if value is not None:
            if value.startswith("ref:"):
                return self._resolve(value[4:].strip(), seen)
            return value
        return self._packed.get(name)

    def _iter_loose_refs(self):
        base = os.path.join(self.git_dir, "refs")
        if not os.path.isdir(base):
            return
        for root, _dirs, files in os.walk(base):
            for fname in files:
                full = os.path.join(root, fname)
                name = os.path.relpath(full, self.git_dir).replace(os.sep, "/")
                yield name

    def _collect_refs(self) -> List[Ref]:
        names = set(self._packed)
        names.update(self._iter_loose_refs())

        refs: List[Ref] = []
        for name in sorted(names):
            if name.endswith("/HEAD"):
                continue
            if name.startswith("refs/heads/"):
                kind = "branch"
            elif name.startswith("refs/remotes/"):
                kind = "remote"
            elif name.startswith("refs/tags/"):
                kind = "tag"
            else:
                continue

            target = self._resolve(name)
            if not target:
                continue
            sha = self._peel_to_commit(target)
            if not sha:
                continue
            refs.append(
                Ref(
                    name=name,
                    kind=kind,
                    sha=sha,
                    target=target,
                    is_head=(name == self.head_ref),
                )
            )
        return refs

    # -- tags ---------------------------------------------------------------
    def _peel_to_commit(self, sha: str) -> Optional[str]:
        for _ in range(16):
            try:
                obj_type, data = self.reader.read(sha)
            except Exception:
                return None
            if obj_type == "commit":
                return sha
            if obj_type == "tag":
                first_line = data.split(b"\n", 1)[0].decode("ascii", "replace")
                if first_line.startswith("object "):
                    sha = first_line.split(" ", 1)[1].strip()
                    continue
                return None
            return None
        return None

    def current_branch(self) -> Optional[str]:
        if self.head_ref and self.head_ref.startswith("refs/heads/"):
            return self.head_ref[len("refs/heads/") :]
        return None

    def close(self) -> None:
        self.reader.close()
