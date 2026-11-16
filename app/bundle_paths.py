"""Resolve project and resource paths for dev trees vs py2app .app bundles."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


@lru_cache(maxsize=1)
def bundle_resources_root() -> Path:
    """``Contents/Resources`` when running inside a macOS .app bundle."""
    return Path(sys.executable).resolve().parent.parent / "Resources"


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Repo root in dev; ``Contents/Resources`` when frozen."""
    if is_frozen():
        return bundle_resources_root()
    return Path(__file__).resolve().parent.parent


def mockups_dir() -> Path:
    root = project_root()
    dev = root / "design" / "mockups"
    if dev.is_dir():
        return dev
    # py2app copies resource folders by basename → Resources/mockups
    bundled = root / "mockups"
    if bundled.is_dir():
        return bundled
    return dev


def env_example_path() -> Path:
    return project_root() / ".env.example"