#!/usr/bin/env python3
"""Convenience runner: build the graph for a repo and open it in the browser.

Usage:
    python visualize.py [REPO_PATH] [--out FILE] [--no-open]
"""

import sys

from gitgraph.cli import main

if __name__ == "__main__":
    sys.exit(main())
