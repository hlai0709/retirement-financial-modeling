"""Multi-file document library for AI / Lazy Mode.

Tracks uploads (both spouses, multiple employers/accounts) until the user
extracts and applies them. Files are copied under Application Support so
paths stay valid across sessions.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.lazy_mode.manifest import document_by_id
from app.core.planning_snapshot import PlanningSnapshot
from settings import app_data_dir

_STATUSES = frozenset({"queued", "extracted", "applied", "failed"})
_PERSONS = frozenset({"self", "wife"})


def library_dir() -> Path:
    path = app_data_dir() / "document_library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_path_in_library(stored: str) -> bool:
    """True only when a stored file sits inside the document library folder."""
    if not stored:
        return False
    try:
        root = library_dir().resolve()
        path = Path(stored).expanduser().resolve()
    except OSError:
        return False
    return path == root or root in path.parents


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^\w.\- ()]+", "_", base).strip("._ ") or "document"
    return cleaned[:120]


def file_content_hash(path: str | Path) -> str | None:
    """SHA-256 of file bytes — used to block duplicate imports."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _person_key(person: str | None) -> str:
    return person or ""


def find_duplicate(
    snapshot: PlanningSnapshot,
    *,
    document_id: str,
    content_hash: str,
    person: str | None,
) -> dict[str, Any] | None:
    """Same person and file bytes already in the library, whatever the document type."""
    del document_id
    if not content_hash:
        return None
    want_person = _person_key(person)
    for entry in list_library(snapshot):
        if _person_key(entry.get("person")) != want_person:
            continue
        existing = str(entry.get("content_hash") or "")
        if not existing:
            stored = str(entry.get("stored_path") or "")
            if stored:
                existing = file_content_hash(stored) or ""
                if existing:
                    entry["content_hash"] = existing
        if existing and existing == content_hash:
            return entry
    return None


def normalize_person(
    person: str | None,
    *,
    document_id: str,
    joint: bool,
) -> str | None:
    """Resolve person tag for a library entry."""
    doc = document_by_id(document_id) or {}
    scope = str(doc.get("person_scope") or "household")
    if scope == "household":
        return None
    raw = (person or "self").strip().lower()
    if raw in {"spouse", "partner"}:
        raw = "wife"
    if raw not in _PERSONS:
        raw = "self"
    if not joint and raw == "wife":
        return "self"
    return raw


def list_library(
    snapshot: PlanningSnapshot,
    *,
    document_id: str | None = None,
) -> list[dict[str, Any]]:
    entries = list(getattr(snapshot, "document_library", None) or [])
    if document_id:
        entries = [e for e in entries if str(e.get("document_id")) == document_id]
    return entries


def get_entry(snapshot: PlanningSnapshot, entry_id: str) -> dict[str, Any] | None:
    for entry in list_library(snapshot):
        if str(entry.get("id")) == entry_id:
            return entry
    return None


def library_summary_for_document(
    snapshot: PlanningSnapshot,
    document_id: str,
) -> dict[str, Any]:
    entries = list_library(snapshot, document_id=document_id)
    by_status: dict[str, int] = {}
    for entry in entries:
        st = str(entry.get("status") or "queued")
        by_status[st] = by_status.get(st, 0) + 1
    queued = by_status.get("queued", 0)
    extracted = by_status.get("extracted", 0)
    applied = by_status.get("applied", 0)
    failed = by_status.get("failed", 0)
    total = len(entries)

    if total == 0:
        status = "missing"
        detail = ""
    elif applied == total:
        status = "parsed"
        detail = f"{total} file(s) applied"
    elif applied or extracted:
        status = "parsed" if applied else "provided"
        parts = []
        if applied:
            parts.append(f"{applied} applied")
        if extracted:
            parts.append(f"{extracted} ready to review")
        if queued:
            parts.append(f"{queued} queued")
        if failed:
            parts.append(f"{failed} failed")
        detail = " · ".join(parts) if parts else f"{total} on file"
    elif failed and not queued:
        status = "failed"
        detail = f"{failed} failed — try re-extract or remove"
    else:
        status = "provided"
        detail = f"{total} file(s) queued — extract when ready"

    return {
        "entries": entries,
        "count": total,
        "by_status": by_status,
        "status": status,
        "status_detail": detail,
        "pending_extract": queued + failed,
    }


def add_files(
    snapshot: PlanningSnapshot,
    *,
    document_id: str,
    paths: list[str],
    person: str | None = None,
) -> dict[str, Any]:
    """Copy files into the library as queued entries (skips content duplicates)."""
    if not paths:
        return {"ok": False, "message": "No files selected.", "entries": []}

    doc = document_by_id(document_id)
    if doc is None:
        return {"ok": False, "message": f"Unknown document type: {document_id}", "entries": []}

    # Clean up accidental duplicate imports from earlier sessions
    prune = prune_duplicate_entries(snapshot, document_id=document_id)

    joint = snapshot.household_mode == "joint"
    person_tag = normalize_person(person, document_id=document_id, joint=joint)
    created: list[dict[str, Any]] = []
    skipped_dupes: list[str] = []
    errors: list[str] = []

    if not hasattr(snapshot, "document_library") or snapshot.document_library is None:
        snapshot.document_library = []

    dest_root = library_dir()
    for raw_path in paths:
        src = Path(raw_path).expanduser()
        if not src.is_file():
            errors.append(f"Not found: {src.name}")
            continue
        content_hash = file_content_hash(src)
        if content_hash:
            dup = find_duplicate(
                snapshot,
                document_id=document_id,
                content_hash=content_hash,
                person=person_tag,
            )
            if dup is not None:
                skipped_dupes.append(
                    f"{src.name} (already on file as {dup.get('original_name') or dup.get('id')})"
                )
                continue
        entry_id = uuid.uuid4().hex[:12]
        safe = _safe_filename(src.name)
        dest = dest_root / f"{entry_id}_{safe}"
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            errors.append(f"{src.name}: {exc}")
            continue
        entry = {
            "id": entry_id,
            "document_id": document_id,
            "document_label": str(doc.get("label") or document_id),
            "person": person_tag,
            "original_name": src.name,
            "stored_path": str(dest),
            "content_hash": content_hash or "",
            "status": "queued",
            "field_count": 0,
            "message": "",
            "added_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        snapshot.document_library.append(entry)
        created.append(entry)

    if not created and skipped_dupes and not errors:
        msg = "Already in your library — skipped duplicate(s):\n• " + "\n• ".join(skipped_dupes)
        if prune.get("removed"):
            msg += f"\nAlso removed {prune['removed']} older duplicate(s)."
        return {
            "ok": False,
            "duplicate": True,
            "message": msg,
            "errors": skipped_dupes,
            "entries": [],
            "library": list_library(snapshot, document_id=document_id),
            "pruned": prune.get("removed", 0),
        }

    if not created:
        return {
            "ok": False,
            "message": errors[0] if errors else "Could not add files.",
            "errors": errors,
            "entries": [],
        }

    msg = f"Added {len(created)} file(s) to your library."
    if skipped_dupes:
        msg += f" Skipped {len(skipped_dupes)} duplicate(s)."
    if errors:
        msg += f" ({len(errors)} failed)"
    if prune.get("removed"):
        msg += f" Removed {prune['removed']} older duplicate(s)."
    return {
        "ok": True,
        "message": msg,
        "entries": created,
        "errors": errors,
        "skipped_duplicates": skipped_dupes,
        "library": list_library(snapshot, document_id=document_id),
        "pruned": prune.get("removed", 0),
    }


def prune_duplicate_entries(
    snapshot: PlanningSnapshot,
    *,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Keep the oldest entry per (document_id, person, content_hash); remove the rest."""
    entries = list(getattr(snapshot, "document_library", None) or [])
    if not entries:
        return {"ok": True, "removed": 0, "removed_ids": []}

    keep: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    removed_ids: list[str] = []

    # Stable order: oldest first so we keep the first import
    ordered = sorted(entries, key=lambda e: str(e.get("added_at") or ""))
    for entry in ordered:
        doc_id = str(entry.get("document_id") or "")
        if document_id and doc_id != document_id:
            keep.append(entry)
            continue
        stored = str(entry.get("stored_path") or "")
        h = str(entry.get("content_hash") or "")
        if not h and stored:
            h = file_content_hash(stored) or ""
            if h:
                entry["content_hash"] = h
        if not h:
            keep.append(entry)
            continue
        key = (doc_id, _person_key(entry.get("person")), h)
        if key in seen:
            removed_ids.append(str(entry.get("id") or ""))
            if stored_path_in_library(stored):
                try:
                    Path(stored).unlink(missing_ok=True)
                except OSError:
                    pass
            continue
        seen.add(key)
        keep.append(entry)

    # Preserve original relative order among keepers
    keep_ids = {str(e.get("id")) for e in keep}
    snapshot.document_library = [e for e in entries if str(e.get("id")) in keep_ids]
    return {"ok": True, "removed": len(removed_ids), "removed_ids": removed_ids}


def queue_for_refresh(snapshot: PlanningSnapshot, entry_id: str) -> dict[str, Any]:
    """Mark an entry queued so it can be extracted again with updated parsers."""
    entry = get_entry(snapshot, entry_id)
    if entry is None:
        return {"ok": False, "message": "Document not found in library."}
    entry["status"] = "queued"
    entry["field_count"] = 0
    entry["message"] = "Queued for refresh"
    entry["updated_at"] = _now_iso()
    return {
        "ok": True,
        "entry": entry,
        "message": f"Ready to refresh {entry.get('original_name') or 'file'}.",
    }


def replace_entry_file(
    snapshot: PlanningSnapshot,
    entry_id: str,
    new_path: str,
) -> dict[str, Any]:
    """Replace the stored PDF and re-queue for extract (same library slot)."""
    entry = get_entry(snapshot, entry_id)
    if entry is None:
        return {"ok": False, "message": "Document not found in library."}
    src = Path(new_path).expanduser()
    if not src.is_file():
        return {"ok": False, "message": f"File not found: {src.name}"}

    content_hash = file_content_hash(src) or ""
    doc_id = str(entry.get("document_id") or "")
    dup = find_duplicate(
        snapshot,
        document_id=doc_id,
        content_hash=content_hash,
        person=entry.get("person"),
    )
    if dup is not None and str(dup.get("id")) != entry_id:
        return {
            "ok": False,
            "duplicate": True,
            "message": (
                f"{src.name} is already in your library as "
                f"{dup.get('original_name') or dup.get('id')}."
            ),
        }

    old_path = str(entry.get("stored_path") or "")
    entry_id_safe = str(entry.get("id") or entry_id)
    dest = library_dir() / f"{entry_id_safe}_{_safe_filename(src.name)}"
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        return {"ok": False, "message": f"Could not copy file: {exc}"}

    if (
        old_path
        and stored_path_in_library(old_path)
        and Path(old_path).resolve() != dest.resolve()
    ):
        try:
            Path(old_path).unlink(missing_ok=True)
        except OSError:
            pass

    entry["original_name"] = src.name
    entry["stored_path"] = str(dest)
    entry["content_hash"] = content_hash
    entry["status"] = "queued"
    entry["field_count"] = 0
    entry["message"] = "File replaced — extract to refresh fields"
    entry["updated_at"] = _now_iso()
    return {
        "ok": True,
        "entry": entry,
        "message": f"Replaced with {src.name}. Extract to apply updated values.",
    }


def remove_entry(snapshot: PlanningSnapshot, entry_id: str) -> dict[str, Any]:
    entries = list(getattr(snapshot, "document_library", None) or [])
    match = next((e for e in entries if str(e.get("id")) == entry_id), None)
    if match is None:
        return {"ok": False, "message": "Document not found in library."}

    reverted = _revert_applied_fields(snapshot, match)
    snapshot.document_library = [e for e in entries if str(e.get("id")) != entry_id]
    stored = str(match.get("stored_path") or "")
    if stored_path_in_library(stored):
        try:
            Path(stored).unlink(missing_ok=True)
        except OSError:
            pass
    message = f"Removed {match.get('original_name') or 'file'}."
    if reverted:
        message += f" Restored {reverted} field(s) this file had filled in."
    return {
        "ok": True,
        "message": message,
        "document_id": match.get("document_id"),
        "reverted": reverted,
    }


def merge_applied_revert(
    previous: list[dict[str, Any]] | None,
    changes: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep the value from before this document first wrote a field.

    A refresh of the same file would otherwise record its own last value as
    the one to restore, so deleting the file would leave that payment in place.
    """
    prior = {
        (str(item.get("field_key") or ""), item.get("person")): item
        for item in (previous or [])
    }
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for change in changes or []:
        key = (str(change.get("field_key") or ""), change.get("person"))
        seen.add(key)
        old = prior.get(key)
        if old is not None and str(change.get("previous") or "").strip() == str(
            old.get("applied") or ""
        ).strip():
            merged.append(
                {
                    **change,
                    "previous": old.get("previous") or "",
                    "previous_source": old.get("previous_source") or "Manual",
                }
            )
        else:
            merged.append(dict(change))
    for key, old in prior.items():
        if key not in seen:
            merged.append(dict(old))
    return merged


def _revert_applied_fields(snapshot: PlanningSnapshot, entry: dict[str, Any]) -> int:
    """Put back the values this entry wrote, when the plan still shows those values."""
    from config.model_fields import FIELDS_BY_KEY

    restored = 0
    for change in list(entry.get("applied_revert") or []):
        field = FIELDS_BY_KEY.get(str(change.get("field_key") or ""))
        if field is None:
            continue
        person = change.get("person")
        if field.scope == "person" and person not in {"self", "wife"}:
            continue
        scope = person if field.scope == "person" else None
        if field.key == "property_tax_annual" and scope in {"self", "wife"}:
            from app.core.lazy_mode.orchestrator import drop_property_tax_component

            component = str(change.get("bill_component") or "")
            if component and drop_property_tax_component(snapshot, scope, component):
                restored += 1
                continue
        current = snapshot.get_raw(field, person=scope)
        applied = str(change.get("applied") or "")
        if str(current).strip() != applied.strip():
            continue
        snapshot.set_raw(field, str(change.get("previous") or ""), person=scope)
        snapshot.set_source(scope, field.key, str(change.get("previous_source") or "Manual"))
        if field.key == "property_tax_annual" and scope in {"self", "wife"}:
            from app.core.lazy_mode.orchestrator import replace_property_tax_bill_list

            try:
                restored_total = float(
                    str(change.get("previous") or "").replace(",", "").replace("$", "").strip() or 0
                )
            except ValueError:
                restored_total = 0.0
            replace_property_tax_bill_list(snapshot, scope, restored_total)
        restored += 1
    return restored


def set_entry_person(
    snapshot: PlanningSnapshot,
    entry_id: str,
    person: str | None,
) -> dict[str, Any]:
    entry = get_entry(snapshot, entry_id)
    if entry is None:
        return {"ok": False, "message": "Document not found in library."}
    joint = snapshot.household_mode == "joint"
    entry["person"] = normalize_person(
        person,
        document_id=str(entry.get("document_id") or ""),
        joint=joint,
    )
    entry["updated_at"] = _now_iso()
    # Re-extract after person change
    if entry.get("status") in {"extracted", "applied"}:
        entry["status"] = "queued"
        entry["field_count"] = 0
        entry["message"] = "Person changed — re-extract when ready"
    return {"ok": True, "entry": entry, "message": "Updated person tag."}


def mark_entry_status(
    snapshot: PlanningSnapshot,
    entry_id: str,
    *,
    status: str,
    message: str = "",
    field_count: int | None = None,
) -> None:
    entry = get_entry(snapshot, entry_id)
    if entry is None:
        return
    if status in _STATUSES:
        entry["status"] = status
    if message:
        entry["message"] = message
    if field_count is not None:
        entry["field_count"] = int(field_count)
    entry["updated_at"] = _now_iso()


def person_label(person: str | None) -> str:
    if person == "wife":
        return "Spouse"
    if person == "self":
        return "You"
    return "Household"
