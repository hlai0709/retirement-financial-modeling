"""
Build a self-contained double-clickable macOS app with py2app.

Usage (from project venv):
    ./scripts/build_app.sh

Output:
    dist/Retirement Planner.app
"""

from __future__ import annotations

from pathlib import Path
from setuptools import setup

PROJECT_ROOT = Path(__file__).resolve().parent
ICON_PATH = PROJECT_ROOT / "design" / "AppIcon.icns"
MOCKUPS_DIR = PROJECT_ROOT / "design" / "mockups"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _data_files() -> list[tuple[str, list[str]]]:
    """Bundle policy tables, config templates, and helper scripts inside the .app."""
    entries: list[tuple[str, list[str]]] = []
    policy_root = PROJECT_ROOT / "config" / "policy"
    if policy_root.is_dir():
        for path in sorted(policy_root.rglob("*")):
            if path.is_file() and path.suffix in {".json", ".py"}:
                rel_dir = path.parent.relative_to(PROJECT_ROOT)
                dest = str(rel_dir)
                bucket = next((files for dest_dir, files in entries if dest_dir == dest), None)
                if bucket is None:
                    entries.append((dest, [str(path)]))
                else:
                    bucket.append(str(path))
    example = PROJECT_ROOT / "config" / "accounts.example.json"
    if example.is_file():
        entries.append(("config", [str(example)]))
    for config_subdir in ("lazy_mode", "ai_mode"):
        cfg_root = PROJECT_ROOT / "config" / config_subdir
        if not cfg_root.is_dir():
            continue
        for path in sorted(cfg_root.rglob("*")):
            if path.is_file() and path.suffix == ".json":
                rel_dir = path.parent.relative_to(PROJECT_ROOT)
                dest = str(rel_dir)
                bucket = next((files for dest_dir, files in entries if dest_dir == dest), None)
                if bucket is None:
                    entries.append((dest, [str(path)]))
                else:
                    bucket.append(str(path))
    for script in ("setup_plaid.py", "list_accounts.py"):
        path = SCRIPTS_DIR / script
        if path.is_file():
            bucket = next((files for dest_dir, files in entries if dest_dir == "scripts"), None)
            if bucket is None:
                entries.append(("scripts", [str(path)]))
            else:
                bucket.append(str(path))
    return entries


def _resources() -> list[str]:
    """Static assets copied to Contents/Resources (web shell, env template)."""
    items: list[str] = []
    if MOCKUPS_DIR.is_dir():
        items.append(str(MOCKUPS_DIR))
    if ENV_EXAMPLE.is_file():
        items.append(str(ENV_EXAMPLE))
    return items


APP = ["main.py"]

OPTIONS = {
    "argv_emulation": False,
    "strip": False,
    "iconfile": str(ICON_PATH) if ICON_PATH.is_file() else None,
    "plist": {
        "CFBundleName": "Retirement Planner",
        "CFBundleDisplayName": "Retirement Planner",
        "CFBundleIdentifier": "com.henrylai.retirementplanner",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.finance",
        "NSRequiresAquaSystemAppearance": False,
    },
    "packages": [
        "app",
        "gui",
        "gui_web",
        "webview",
        "model",
        "simulation",
        "services",
        "providers",
        "security",
        "adviser",
        "config",
        "validation",
        "pandas",
        "numpy",
        "matplotlib",
        "keyring",
        "cryptography",
        "dotenv",
        "plaid",
        "yfinance",
        "requests",
        "charset_normalizer",
        "pypdf",
        "pymupdf",
        "fitz",
        "objc",
        "Foundation",
        "AppKit",
        "WebKit",
    ],
    "includes": [
        "tkinter",
        "tkinter.ttk",
        "matplotlib.backends.backend_tkagg",
        "cmath",
        "decimal",
        "zoneinfo",
        "charset_normalizer",
        "bottle",
        "proxy_tools",
    ],
    "excludes": [
        "pytest",
        "test",
        "tests",
        "pandas.testing",
        "pandas.tests",
    ],
    "resources": _resources(),
}

setup(
    name="Retirement Planner",
    app=APP,
    data_files=_data_files(),
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)