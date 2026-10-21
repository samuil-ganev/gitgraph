# gitgraph

Read a locally cloned git repository **straight from its `.git` directory** and
render an interactive visualisation of the full commit DAG — every commit,
local branch, remote branch, tag and HEAD.

No third-party dependencies: git objects are parsed by hand (loose objects and
packfiles, including both delta encodings). The visualisation is a single
self-contained HTML file that opens in your browser.

## Features

- Parses `.git/objects` directly: loose objects **and** packfiles (`*.idx` v2,
  `OBJ_OFS_DELTA` / `OBJ_REF_DELTA` delta resolution).
- Collects `refs/heads`, `refs/remotes`, `refs/tags` and `HEAD`
  (symbolic or detached), peeling annotated tags down to commits.
- Builds the complete acyclic commit graph from all ref tips.
- Lays the graph out **horizontally as a proper layered DAG** (Sugiyama style):
  vertices are ranked by the longest path from a source, so every edge goes
  strictly from a lower layer to a higher layer; long edges are split with
  dummy routing vertices so a connection never cuts through an unrelated
  commit; barycenter sweeps reduce edge crossings; vertices are stacked inside
  each layer. Newest commits are on the left, root commits on the right, and
  every edge points child -> parent.
- Interactive HTML: zoom, pan, hover tooltips (sha, author, date, subject),
  clickable ref list, live search, "fit all", a **hide/show origin** toggle
  (hides remote-only commits and remote labels) and HEAD highlighting.

## Usage

```bash
# current repository, writes gitgraph.html next to .git and opens the browser
python visualize.py

# a specific repository
python visualize.py /path/to/repo

# no browser, custom output
python visualize.py /path/to/repo --out /tmp/graph.html --no-open
```

Or as a module / installed console script:

```bash
python -m gitgraph /path/to/repo
pip install -e . && gitgraph /path/to/repo
```

### Options

| flag | description |
| --- | --- |
| `repo` | path inside the repository (default: `.`) |
| `-o, --out FILE` | output HTML file (default: `<repo>/gitgraph.html`) |
| `--no-open` | do not open the browser |
| `--title TEXT` | page title |

The output file is overwritten on every run, so re-running always shows the
current tree.

## Library

```python
from gitgraph import Repo, build_graph, render_html

repo = Repo(".")              # discovers the .git directory
graph = build_graph(repo)     # commits, parents, lanes, ref labels
print(graph.num_commits, repo.current_branch())

render_html(graph, "gitgraph.html")
```

Key types:

- `Repo` — `.git` discovery, refs, HEAD, commit parsing (`repo.reader` exposes
  raw object access: `repo.reader.read(sha) -> (type, bytes)`).
- `Graph` — `commits`, `order` (newest first), `lanes`, `rows`, `labels`.
- `render_html(graph, path, title)` — writes the interactive page.

## Tests

The tests compare the hand-written parser against the real `git` binary
(reachability, parents, tag peeling, object contents, loose vs packed):

```bash
python -m pytest tests -q
```

## Notes

- The interactive page loads [vis-network](https://visjs.github.io/vis-network/)
  from a CDN, so viewing it needs an internet connection.
- Only `.idx` version 2 pack indexes are supported (the format produced by git
  since 2007).
