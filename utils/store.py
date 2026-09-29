"""Where the owner's personal data store lives on this machine.

The store is cloned at different paths on different Macs (~/Sideproject on the
company laptop, ~/Code on the personal one), so a hardcoded path silently
disabled the store push and the realsmart fetcher on whichever machine didn't
match. PF_STORE still overrides; otherwise the first existing candidate wins.
"""

from __future__ import annotations

import os

_CANDIDATES = (
    "~/Code/personal-data-store",
    "~/Sideproject/personal-data-store",
)

# The store's page-fetch skill, under its current and legacy names.
_FETCH_SKILLS = ("web-extract", "read-webpage")


def store_root() -> str:
    env = os.environ.get("PF_STORE")
    if env:
        return os.path.expanduser(env)
    for c in _CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.isdir(p):
            return p
    return os.path.expanduser(_CANDIDATES[0])


def fetch_script(name: str) -> str:
    """Path to one of the store's fetch-skill scripts (fetch.py, reader.py, ...)."""
    root = store_root()
    paths = [os.path.join(root, ".claude", "skills", s, "scripts", name)
             for s in _FETCH_SKILLS]
    return next((p for p in paths if os.path.exists(p)), paths[0])
