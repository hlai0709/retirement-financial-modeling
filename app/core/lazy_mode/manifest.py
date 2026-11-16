"""Load Lazy Mode document manifest and resolve checklist for household."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from settings import PROJECT_ROOT

_MANIFEST_PATH = PROJECT_ROOT / "config" / "lazy_mode" / "document_manifest.json"


@lru_cache(maxsize=1)
def load_document_manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def manifest_documents(*, tier: str | None = None) -> list[dict[str, Any]]:
    docs = list(load_document_manifest().get("documents", []))
    if tier:
        docs = [doc for doc in docs if doc.get("tier") == tier]
    return docs


def document_by_id(document_id: str) -> dict[str, Any] | None:
    for doc in manifest_documents():
        if doc.get("id") == document_id:
            return doc
    return None


def accountant_intro() -> str:
    return str(load_document_manifest().get("accountant_intro", ""))


def required_core_documents(*, joint: bool) -> list[dict[str, Any]]:
    docs = [doc for doc in manifest_documents() if doc.get("required_for_core")]
    if not joint:
        docs = [
            doc
            for doc in docs
            if doc.get("person_scope") != "wife"
        ]
    return docs


def parser_config_path(relative: str) -> str:
    return str((PROJECT_ROOT / relative).resolve())