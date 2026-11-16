# Lazy Mode — Execution Plan

**Status:** Phase 1 complete · Phase 2 (W-2) started  
**Last updated:** 2026-07-05  
**Goal:** A third presentation mode where the software acts like an accountant: tell the user which documents to provide, ingest them (Plaid + uploads), auto-fill planning fields, and surface parser failures with actionable upgrade paths when formats change.

---

## 1. Executive summary

| Mode | User experience |
|------|-----------------|
| **Guided** | Answer questions step-by-step (TurboTax-style wizard). |
| **Expert** | Edit all assumptions in section panels. |
| **Lazy** (new) | Provide documents + connect Plaid; software extracts values and fills fields. User reviews a checklist, applies suggestions, then runs the plan. |

Lazy Mode does **not** remove Guided/Expert — it is the fastest on-ramp. Unresolved fields fall back to Guided with pre-filled values where possible.

---

## 2. Field inventory review

The model has **99 catalog fields** (55 shared, 44 person-scoped → up to **143 person-specific slots** in joint households).

### 2.1 By data source (engine metadata)

| Source | Count | Keys | Lazy acquisition |
|--------|------:|------|------------------|
| **Plaid** | 2 | `initial_assets`, `base_expenses` | Connect accounts → Refresh balances / expenses |
| **Market** | 3 | `inflation_rate`, `investment_return_mean`, `tsla_current_price` | Refresh Market (FRED/yfinance) — no user document |
| **Manual** | 94 | (see appendix) | Documents, defaults, or LLM estimates |

### 2.2 Completeness “core” fields (minimum to run a credible plan)

These 16 slots drive the completeness ring today (joint household):

| Group | Fields |
|-------|--------|
| Setup | `state_of_residence` |
| Timeline | `current_age`, `birth_year`, `retirement_age`, `life_expectancy`, `marriage_year` (joint) |
| Assets | `initial_assets` (self + wife if joint) |
| Expenses | `base_expenses` (self + wife if joint) |
| Income | `annual_earned_income` (self + wife if joint) |
| SS / pension | `ss_start_age`, `ss_amount` (self + wife if joint) |
| Tax | `pre_retire_tax_rate`, `post_retire_tax_rate` |
| Healthcare | `health_insurance_pre_medicare` (self + wife if joint) |
| Market | `inflation_rate`, `investment_return_mean` |

**Lazy Mode MVP** must cover **100% of core fields** via document + Plaid + market refresh, with explicit “still needed” for anything that cannot be parsed.

### 2.3 Full field catalog by UI group

| UI group | Field slots (joint) | Lazy priority |
|----------|------------------:|---------------|
| Taxes & benefits | 53 | High — 1040, W-2, 1099s, SSA statement |
| Healthcare (self/wife) | 32 | Medium — pay stubs, 1040, Medicare notices; many defaulted |
| Spending (self/wife) | 18 | Medium — Plaid expenses, 1040 Schedule A |
| Timeline | 8 | High — 1040, W-2, SSA, driver license / manual |
| Assets (self/wife) | 11 | High — Plaid, brokerage/401k statements |
| Income (self/wife) | 11 | High — W-2, 1040, pay stubs |
| Market | 3 | Auto — Refresh Market |
| Setup | 2 | 1040 header / manual confirm |
| Scenarios (expert) | 5 | Optional — skip in Lazy MVP |

Machine-readable inventory: `config/lazy_mode/field_inventory.json` (generated from `model_fields.py`).

---

## 3. Document → field mapping (accountant checklist)

What a real CPA would ask for, mapped to software ingestion.

### 3.1 Tier A — Required for core plan (Lazy MVP)

| Document ID | What to ask the user | Formats | Acquisition | Parser | Primary fields |
|-------------|---------------------|---------|-------------|--------|----------------|
| `plaid_accounts` | Bank & investment logins (read-only) | API | **Plaid Link** | `plaid_balances` | `initial_assets` |
| `plaid_spending` | Credit card / checking (same Link) | API | **Plaid** refresh expenses | `plaid_expenses` | `base_expenses` |
| `form_1040` | Most recent federal tax return (all pages if itemized) | PDF, JSON | Upload | `tax_return_pdf` / `tax_return_json` | Wages, interest, dividends, IRA, pension, SS, cap gains, rental, itemized, filing status, state, derived tax rates |
| `w2` | W-2 from each employer (you + spouse) | PDF, image* | Upload (per person) | `w2_v1` | `annual_earned_income`, FICA hints |
| `market_data` | (none — automatic) | API | **Refresh Market** | `market_fred` | `inflation_rate`, `investment_return_mean` |

\* Image OCR deferred; show “export PDF from employer portal” guidance.

### 3.2 Tier B — Strongly recommended (full Lazy, post-MVP)

| Document ID | Accountant ask | Fields |
|-------------|----------------|--------|
| `1099_int` | 1099-INT (each payer) | Taxable interest → cross-check 1040 |
| `1099_div` | 1099-DIV | Ordinary dividends |
| `1099_r` | 1099-R | IRA/pension distributions |
| `ssa_1099` | SSA-1099 / my Social Security statement | `ss_amount`, `ss_pia_monthly`, `ss_aime` |
| `brokerage_statement` | Latest brokerage statement (Fidelity, Schwab, …) | `initial_taxable_assets`, allocation |
| `401k_statement` | 401(k) / IRA statement | `initial_traditional_ira`, `initial_roth_ira` |
| `pay_stub` | Recent pay stub | `annual_earned_income`, `annual_contributions`, employer health premium |
| `property_tax` / Schedule E | Property tax bill or Schedule E | `rental_income_start`, `mortgage_annual`, `itemized_salt_annual` |

### 3.3 Tier C — Defaults / LLM / Guided fallback

Fields with weak document signal (retirement age, life expectancy, Go-Go spending phases, Roth conversion strategy, ACA benchmark premiums, scenario knobs):

- Apply **sensible defaults** from `model_fields.py`
- Offer **LLM estimate** batch (existing AI step) for remaining gaps
- Route user to **Guided** for final confirmation

Canonical mapping file: `config/lazy_mode/document_manifest.json`  
Field cross-reference: `config/lazy_mode/field_document_map.json`

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Lazy Mode UI (live-app.js)                                 │
│  Checklist · Upload · Plaid shortcuts · Progress · Apply    │
└───────────────────────────┬─────────────────────────────────┘
                            │ WebAppApi
┌───────────────────────────▼─────────────────────────────────┐
│  PlanningSession.get_lazy_screen / run_lazy_ingest / apply  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  lazy_mode/orchestrator.py                                  │
│  · Resolve required docs for household_mode                 │
│  · Route files → parser_registry                            │
│  · Merge suggestions · track coverage                       │
│  · Emit ParserFailure payloads for UI                       │
└───────┬─────────────────────────────┬───────────────────────┘
        │                             │
┌───────▼──────────┐         ┌────────▼────────┐
│ parser_registry  │         │ Plaid / Market  │
│ (versioned)      │         │ (existing)      │
└───────┬──────────┘         └─────────────────┘
        │
┌───────▼──────────────────────────────────────┐
│ config/lazy_mode/parsers/<id>_<version>.json │  ← user-editable mapping rules
│ providers/document_extractors/*              │  ← Python adapters
└──────────────────────────────────────────────┘
```

### 4.1 Parser plugin contract (format-change resilient)

Each parser declares:

```python
@dataclass(frozen=True)
class ParserSpec:
    parser_id: str           # e.g. "w2"
    config_version: str      # e.g. "2024_v1"
    config_path: str         # JSON rules path for user updates
    supported_extensions: tuple[str, ...]

@dataclass
class ParserResult:
    ok: bool
    fields: list[ExtractedFieldSuggestion]
    parser_id: str
    config_version: str
    failure: ParserFailure | None = None

@dataclass
class ParserFailure:
    parser_id: str
    config_version: str
    document_label: str
    user_message: str
    developer_message: str
    config_path: str         # shown in popup: "Update parser rules here"
    excerpt: str             # first 500 chars of extracted text
```

**On parse failure:** UI shows modal with:

1. Plain-language “we couldn’t read this document”
2. Path to `config/lazy_mode/parsers/<parser>_<version>.json`
3. Link to docs section “Updating parsers when IRS/employer forms change”
4. Option to re-upload or switch to Guided for that field group

**Versioning:** New IRS W-2 box layout → add `w2_2026_v1.json` + adapter; keep `w2_2024_v1` for older PDFs. Registry picks parser by `tax_year` hint or `can_parse()` confidence.

### 4.2 Auto-fill policy

| Confidence | Behavior |
|------------|----------|
| `high` | Auto-apply on “Fill my plan” if field empty |
| `medium` | Pre-check in review table; user confirms |
| `low` | Show in “needs review”; never silent apply |

Existing document import review modal is reused; Lazy Mode adds batch review + coverage summary.

---

## 5. UI specification (Lazy Mode)

### 5.1 Header

Add third pill: **Guided | Expert | Lazy**

### 5.2 Lazy home screen (single scroll)

1. **Accountant intro** — “Here’s what I need to build your plan…”
2. **Progress** — core documents X/Y, fields filled Z%, link to completeness
3. **Document checklist** (grouped Tier A → B)
   - Status: `missing` | `provided` | `parsed` | `failed` | `n/a` (Plaid connected)
   - Action button per row:
     - `Connect Plaid` / `Refresh balances` / `Refresh expenses`
     - `Upload…` (file picker)
     - `Refresh market`
4. **Fill my plan** — run orchestrator, open batch review
5. **What’s still missing** — list fields with “Add document X” or “Answer in Guided →”

### 5.3 Parser failure modal

Triggered when any `ParserFailure` returned:

- Title: “Couldn’t read {document label}”
- Body: user_message + excerpt (collapsible)
- Actions: **Try another file** | **Edit parser config** (opens path in Finder + show path) | **Enter manually in Guided**

---

## 6. Phased execution plan

### Phase 1 — Foundation ✅ complete

| # | Deliverable | Files |
|---|-------------|-------|
| 1.1 | Execution plan (this doc) | `docs/LAZY_MODE_EXECUTION_PLAN.md` |
| 1.2 | Document manifest + field map JSON | `config/lazy_mode/*.json` |
| 1.3 | Parser registry + failure types | `app/core/lazy_mode/parser_registry.py` |
| 1.4 | Lazy orchestrator (skeleton) | `app/core/lazy_mode/orchestrator.py` |
| 1.5 | Lazy screen payload | `app/core/presentation/lazy_screens.py` |
| 1.6 | `presentation_mode=lazy` in preferences + session | `preferences.py`, `planning_session.py` |
| 1.7 | Web API + Lazy UI shell | `gui_web/api.py`, `live-app.js` |
| 1.8 | Wire existing 1040 PDF/JSON + Plaid into checklist | orchestrator |
| 1.9 | Parser failure modal (JS) | `live-app.js` |
| 1.10 | Regression tests for manifest + registry | `regression_test.py` |

**Exit criteria:** User can switch to Lazy, see accountant checklist, upload 1040 PDF, connect Plaid, click “Fill my plan”, review suggestions, see parser failure dialog on bad file.

### Phase 2 — W-2 + 1099 parsers (in progress)

| # | Deliverable | Status |
|---|-------------|--------|
| 2.1 | `w2_2024_v1` JSON rules + `w2_pdf.py` extractor | ✅ |
| 2.2 | `1099_int_v1`, `1099_div_v1`, `1099_r_v1` |
| 2.3 | Per-person document uploads (self vs wife) |
| 2.4 | Golden PDF fixtures in `validation/fixtures/lazy/` |

### Phase 3 — Statements & SSA

| # | Deliverable |
|---|-------------|
| 3.1 | Brokerage / 401k statement parsers (table OCR / LLM) |
| 3.2 | SSA statement parser → `ss_*` fields |
| 3.3 | Pay stub → income + contributions + employer health |

### Phase 4 — Auto-fill + gap routing

| # | Deliverable |
|---|-------------|
| 4.1 | One-click apply with confidence rules |
| 4.2 | “Remaining gaps” → Guided jump links per field group |
| 4.3 | Lazy session persistence (documents provided, parse log) |
| 4.4 | Import history in plan snapshot |

### Phase 5 — Hardening

| # | Deliverable |
|---|-------------|
| 5.1 | Parser admin doc: “When IRS changes forms” |
| 5.2 | CLI: `python scripts/lazy_ingest.py --dir ~/tax_docs` |
| 5.3 | Optional LLM vision for scanned W-2/1099 |
| 5.4 | Tk shell parity (if still supported) |

---

## 7. Testing strategy

| Layer | Tests |
|-------|-------|
| Manifest | Every core field has ≥1 document or `default` source |
| Parsers | Unit tests per fixture PDF/text; version bump tests |
| Orchestrator | Mock parsers; merge conflicts; failure propagation |
| UI | Regression tokens: `lazy`, `data-lazy-*`, `showParserFailureModal` |
| E2E | Manual: real 1040 PDF + Plaid + Fill my plan |

---

## 8. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| PDF layout drift (TurboTax vs H&R Block) | Versioned parser configs + LLM fallback + failure modal |
| Scanned documents (no text layer) | Detect low text; prompt for LLM Settings or digital PDF |
| Plaid Trial item limits | Lazy checklist shows connected count; link to Manage accounts |
| Over-automation wrong values | Confidence gating + review modal before apply |
| Joint household spouse docs | `person_scope` on each document row |

---

## 9. Appendix — document manifest IDs (quick reference)

`plaid_accounts`, `plaid_spending`, `form_1040`, `w2`, `1099_int`, `1099_div`, `1099_r`, `ssa_1099`, `brokerage_statement`, `401k_statement`, `pay_stub`, `property_tax`, `market_data`, `manual_gaps`

---

## 10. PR stack (Graphite-friendly)

1. `lazy-mode-config` — manifest JSON + field map  
2. `lazy-mode-core` — registry, orchestrator, failure types  
3. `lazy-mode-ui` — presentation mode + live-app shell  
4. `lazy-mode-1040-plaid` — wire existing extractors  
5. `lazy-mode-w2` — W-2 parser (Phase 2)  
6. `lazy-mode-1099` — 1099 family (Phase 2)  

---

*Next step after Phase 1 exit criteria: implement W-2 parser (Phase 2.1).*