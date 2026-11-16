"""Versioned document parsers for Lazy Mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.document_ingestion.extraction import ExtractedFieldSuggestion
from app.core.document_ingestion.ingestion_controller import ingest_document
from app.core.lazy_mode.manifest import document_by_id, parser_config_path
from app.core.lazy_mode.types import ParserFailure, ParserResult
from app.core.planning_snapshot import PlanningSnapshot
from settings import PROJECT_ROOT


def _failure_from_notes(
    *,
    doc: dict[str, Any],
    path: str,
    notes: str,
    excerpt: str = "",
) -> ParserFailure:
    config_rel = str(doc.get("config_path", ""))
    config_abs = parser_config_path(config_rel) if config_rel else ""
    return ParserFailure(
        parser_id=str(doc.get("parser_id", "unknown")),
        config_version=str(doc.get("parser_config_version", "")),
        document_label=str(doc.get("label", Path(path).name)),
        user_message=notes,
        developer_message=(
            f"Parser {doc.get('parser_id')} v{doc.get('parser_config_version')} failed on {path}. "
            f"Update rules in {config_rel or '(no config)'} and add regression fixtures."
        ),
        config_path=config_abs,
        excerpt=excerpt[:500],
    )


def parse_uploaded_document(
    snapshot: PlanningSnapshot,
    *,
    document_id: str,
    path: str,
    person: str | None = None,
) -> ParserResult:
    doc = document_by_id(document_id)
    if doc is None:
        return ParserResult(
            ok=False,
            parser_id="unknown",
            config_version="",
            document_id=document_id,
            document_label=Path(path).name,
            failure=ParserFailure(
                parser_id="unknown",
                config_version="",
                document_label=Path(path).name,
                user_message=f"Unknown document type: {document_id}",
                developer_message="Add document id to config/lazy_mode/document_manifest.json",
                config_path=str(_MANIFEST_PATH),
            ),
        )

    if doc.get("ready") is False:
        config_rel = str(doc.get("config_path", ""))
        return ParserResult(
            ok=False,
            parser_id=str(doc.get("parser_id", "")),
            config_version=str(doc.get("parser_config_version", "")),
            document_id=document_id,
            document_label=str(doc.get("label", "")),
            failure=ParserFailure(
                parser_id=str(doc.get("parser_id", "")),
                config_version=str(doc.get("parser_config_version", "")),
                document_label=str(doc.get("label", "")),
                user_message=(
                    f"{doc.get('label')} import is not ready yet. "
                    "Use Form 1040 PDF for now, or enter values in Guided mode."
                ),
                developer_message=f"Implement parser adapter for {doc.get('parser_id')}.",
                config_path=parser_config_path(config_rel) if config_rel else "",
            ),
        )

    parser_id = str(doc.get("parser_id", ""))
    person_tag = _resolve_person(doc, person)

    if parser_id == "tax_return":
        return _parse_tax_return_upload(snapshot, doc=doc, path=path, document_id=document_id)

    if parser_id == "w2":
        return _parse_w2_upload(doc=doc, path=path, document_id=document_id, person=person_tag)

    if parser_id == "pay_stub":
        return _parse_pay_stub_upload(doc=doc, path=path, document_id=document_id, person=person_tag)

    if parser_id in {
        "ssa_1099",
        "brokerage_statement",
        "401k_statement",
        "1099_r",
        "1099_int",
        "1099_div",
        "form_1098",
        "mortgage_statement",
        "property_tax_bill",
    }:
        return _parse_statement_upload(
            doc=doc, path=path, document_id=document_id, person=person_tag
        )

    return ParserResult(
        ok=False,
        parser_id=parser_id,
        config_version=str(doc.get("parser_config_version", "")),
        document_id=document_id,
        document_label=str(doc.get("label", "")),
        failure=_failure_from_notes(
            doc=doc,
            path=path,
            notes=f"No parser adapter registered for {parser_id}.",
        ),
    )


def _resolve_person(doc: dict[str, Any], person: str | None) -> str | None:
    scope = str(doc.get("person_scope") or "household")
    if scope == "household":
        return None
    if person in {"self", "wife"}:
        return person
    if scope == "wife":
        return "wife"
    return "self"


def _parse_statement_upload(
    *,
    doc: dict[str, Any],
    path: str,
    document_id: str,
    person: str | None = None,
) -> ParserResult:
    from providers.document_extractors.statement_pdf import parse_statement_pdf

    parser_id = str(doc.get("parser_id", document_id))
    version = str(doc.get("parser_config_version", ""))
    person_tag = person if person in {"self", "wife"} else "self"
    suggestions = parse_statement_pdf(path, document_id=document_id, person=person_tag)

    if suggestions and not suggestions[0].ok:
        excerpt = ""
        try:
            from providers.document_extractors.pdf_text import extract_pdf_text

            excerpt = extract_pdf_text(path)[:500]
        except Exception:
            excerpt = ""
        # Soft-fail: allow local AI to still try (empty fields + not fatal)
        return ParserResult(
            ok=True,
            parser_id=parser_id,
            config_version=version,
            document_id=document_id,
            document_label=str(doc.get("label", Path(path).name)),
            fields=[],
            failure=None,
        )

    fields = [row for row in suggestions if row.ok and row.field_key]
    return ParserResult(
        ok=True,
        parser_id=parser_id,
        config_version=version,
        document_id=document_id,
        document_label=Path(path).name,
        fields=fields,
    )


def _parse_pay_stub_upload(
    *,
    doc: dict[str, Any],
    path: str,
    document_id: str,
    person: str | None = None,
) -> ParserResult:
    from providers.document_extractors.pay_stub_pdf import parse_pay_stub_pdf

    parser_id = str(doc.get("parser_id", "pay_stub"))
    version = str(doc.get("parser_config_version", ""))
    person_tag = person if person in {"self", "wife"} else "self"
    if str(doc.get("person_scope") or "") == "wife" and person is None:
        person_tag = "wife"
    suggestions = parse_pay_stub_pdf(path, person=person_tag)

    # Soft-fail: empty fields let local AI still try (same pattern as statement PDFs)
    if suggestions and not suggestions[0].ok:
        return ParserResult(
            ok=True,
            parser_id=parser_id,
            config_version=version,
            document_id=document_id,
            document_label=str(doc.get("label", Path(path).name)),
            fields=[],
            failure=None,
        )

    fields = [row for row in suggestions if row.ok and row.field_key]
    return ParserResult(
        ok=True,
        parser_id=parser_id,
        config_version=version,
        document_id=document_id,
        document_label=Path(path).name,
        fields=fields,
    )


def _parse_w2_upload(
    *,
    doc: dict[str, Any],
    path: str,
    document_id: str,
    person: str | None = None,
) -> ParserResult:
    from providers.document_extractors.w2_pdf import parse_w2_pdf

    parser_id = str(doc.get("parser_id", "w2"))
    version = str(doc.get("parser_config_version", ""))
    person_tag = person if person in {"self", "wife"} else "self"
    suggestions = parse_w2_pdf(path, person=person_tag)

    if suggestions and not suggestions[0].ok:
        excerpt = ""
        try:
            from providers.document_extractors.pdf_text import extract_pdf_text

            excerpt = extract_pdf_text(path)[:500]
        except Exception:
            excerpt = ""
        return ParserResult(
            ok=False,
            parser_id=parser_id,
            config_version=version,
            document_id=document_id,
            document_label=str(doc.get("label", "")),
            failure=_failure_from_notes(
                doc=doc,
                path=path,
                notes=str(suggestions[0].notes or "Could not parse W-2."),
                excerpt=excerpt,
            ),
        )

    fields = [row for row in suggestions if row.ok and row.field_key]
    if not fields:
        return ParserResult(
            ok=False,
            parser_id=parser_id,
            config_version=version,
            document_id=document_id,
            document_label=str(doc.get("label", "")),
            failure=_failure_from_notes(
                doc=doc,
                path=path,
                notes="No mappable fields found in this W-2.",
            ),
        )

    return ParserResult(
        ok=True,
        parser_id=parser_id,
        config_version=version,
        document_id=document_id,
        document_label=Path(path).name,
        fields=fields,
    )


def _parse_tax_return_upload(
    snapshot: PlanningSnapshot,
    *,
    doc: dict[str, Any],
    path: str,
    document_id: str,
) -> ParserResult:
    result = ingest_document(snapshot, path)
    parser_id = str(doc.get("parser_id", "tax_return"))
    version = str(doc.get("parser_config_version", ""))

    if not result.get("ok"):
        excerpt = ""
        if Path(path).suffix.lower() == ".pdf":
            try:
                from providers.document_extractors.pdf_text import extract_pdf_text

                excerpt = extract_pdf_text(path)[:500]
            except Exception:
                excerpt = ""
        return ParserResult(
            ok=False,
            parser_id=parser_id,
            config_version=version,
            document_id=document_id,
            document_label=str(doc.get("label", "")),
            failure=_failure_from_notes(
                doc=doc,
                path=path,
                notes=str(result.get("message", "Could not parse tax return.")),
                excerpt=excerpt,
            ),
        )

    rows = result.get("rows") or []
    fields = _rows_to_suggestions(rows, document_type=str(result.get("document_type", "pdf")))
    return ParserResult(
        ok=True,
        parser_id=parser_id,
        config_version=version,
        document_id=document_id,
        document_label=str(result.get("document_label", Path(path).name)),
        fields=fields,
    )


def _rows_to_suggestions(rows: list[dict[str, Any]], *, document_type: str) -> list[ExtractedFieldSuggestion]:
    suggestions: list[ExtractedFieldSuggestion] = []
    for row in rows:
        if not row.get("ok") or not row.get("field_key"):
            continue
        suggestions.append(
            ExtractedFieldSuggestion(
                field_key=str(row["field_key"]),
                person=row.get("person"),
                label=str(row.get("label", row["field_key"])),
                raw_value=str(row.get("raw_value", "")),
                display_value=str(row.get("suggested", row.get("raw_value", ""))),
                confidence=str(row.get("confidence", "medium")),
                source_quote=str(row.get("source_quote", row.get("notes", ""))),
                document_type=document_type,
            )
        )
    return suggestions


_MANIFEST_PATH = PROJECT_ROOT / "config" / "lazy_mode" / "document_manifest.json"