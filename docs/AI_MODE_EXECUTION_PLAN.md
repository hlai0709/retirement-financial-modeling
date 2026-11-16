# AI Mode — Execution Plan

**Status:** Implemented end-to-end (live-test polish next)  
**Last updated:** 2026-07-25  
**Goal:** A third presentation mode that uses **local AI (Ollama / qwen3:14b)** plus deterministic parsers and Plaid to fill **user-specific** planning fields from documents, then prompts the user only for gaps—while keeping the existing **AI Estimates** feature for non–user-specific assumptions.

---

## 1. Product vision

| Mode | User experience |
|------|-----------------|
| **Guided** | Answer questions step-by-step (TurboTax-style). |
| **Expert** | Edit all assumptions in panels. |
| **AI** (this mode) | Upload documents (and connect Plaid where useful). Software extracts user facts, shows a review, then only asks for what’s still missing. Optional **AI Estimates** for market/healthcare defaults. |

**Burden reduction:** The user should not re-type numbers that already exist on a 1040, W-2, SSA statement, or bank PDF.

**Privacy:** Document text for extraction stays on-device via **local Ollama** (`127.0.0.1`). Cloud LLM estimates (if configured) remain a separate, optional step for *generic* assumptions—not for parsing personal PDFs.

**Relation to Lazy Mode:** Lazy Mode was an earlier document-checklist prototype (later disabled in preferences). AI Mode **replaces and extends** it: same document-first idea, plus local AI extraction, clearer guided-field mapping, and an explicit gaps + estimates finish.

---

## 2. Field inventory (what the planner needs)

From `model_fields.py` (catalog ~99 keys; joint households double person-scoped slots).

### 2.1 Completeness “core” (must cover for a credible plan)

| Group | Fields | User-specific? |
|-------|--------|----------------|
| Setup | `state_of_residence` | Yes |
| Timeline | `current_age`, `birth_year`, `retirement_age`, `life_expectancy`, `marriage_year` | Yes (retirement/life often judgment) |
| Assets | `initial_assets` (+ taxable/IRA/Roth breakdown) | Yes |
| Expenses | `base_expenses` | Yes |
| Income | `annual_earned_income`, contributions | Yes |
| SS / pension | `ss_start_age`, `ss_amount` / PIA, `pension_*` | Yes |
| Tax | `pre_retire_tax_rate`, `post_retire_tax_rate`, itemized | Mix (rates derivable) |
| Healthcare | `health_insurance_pre_medicare`, Medicare knobs | Mix |
| Market | `inflation_rate`, `investment_return_mean` | **No** — market / estimates |

### 2.2 User-specific vs estimate-friendly

| Class | Examples | AI Mode source |
|-------|----------|----------------|
| **User facts** | Income, assets, age/DOB, SS benefit, mortgage, itemized, state | Documents + Plaid + manual gaps |
| **Judgment / goals** | Retirement age, life expectancy, Roth strategy, expense phases | Manual or guided questions |
| **Non-user assumptions** | Inflation, return mean, ACA benchmarks, LTC inflation | **AI Estimates** / market refresh |

---

## 3. Document → field map (research + product)

Sources: IRS form instructions, SSA/my Social Security, typical CPA intake, brokerage statements, and this app’s field catalog.

### 3.1 Tier A — Highest value for AI Mode MVP

| Document | Where to get it | Primary planner fields |
|----------|-----------------|------------------------|
| **Form 1040** (full PDF, all schedules if itemized) | TurboTax/H&R Block export, CPA, IRS transcript | Wages/AGI/taxable income → earned income & tax rates; Schedule A → SALT/mortgage/charity/medical; Sch E → rental; interest/dividends/cap gains; filing status; state of residence (header / state return) |
| **Form W-2** (each employer, each spouse) | Employer portal, mail | `annual_earned_income`, FICA wages; sometimes state wages |
| **Plaid (bank + investment)** | In-app Connect | `initial_assets`, `base_expenses` (transaction annualization) |
| **Brokerage / 401(k) / IRA statement** | Fidelity, Schwab, Vanguard, NetBenefits | `initial_taxable_assets`, `initial_traditional_ira`, `initial_roth_ira` |
| **Market refresh** | In-app (FRED/yfinance) | `inflation_rate`, `investment_return_mean` |

### 3.2 Tier B — Strongly recommended

| Document | Fields |
|----------|--------|
| **SSA-1099 / my Social Security** | `ss_amount`, `ss_pia_monthly`, `ss_aime`, claiming age hints |
| **1099-INT / DIV / R / B** | Cross-check 1040 interest/dividends/IRA/pension/cap gains |
| **Pay stub (recent, YTD)** | `annual_earned_income` annualization, `annual_contributions`, `contribution_roth_pct`, employer health premium |
| **Property tax / mortgage statement** | `itemized_salt_annual`, `mortgage_annual`, payoff year |
| **Health insurance / COBRA / ACA bill** | `health_insurance_pre_medicare`, employer health monthly |

### 3.3 Tier C — User judgment (not on tax docs)

Retirement age, life expectancy, Go-Go spending phases, Roth conversion strategy, doomsday scenarios, TSLA targets — **manual or Guided**; not forced through document AI.

### 3.4 Guided step → documents (journey alignment)

| Guided area | Ask user for |
|-------------|--------------|
| Setup | 1040 header (state, filing status) |
| Timeline | DL / SSA / manual (DOB/age); marriage year manual |
| Income | W-2, 1040 line 1, pay stub |
| Assets | Plaid + brokerage/401k/IRA PDFs |
| Spending | Plaid expenses; optional Schedule A / budgets |
| Healthcare | Premium bills; defaults + estimates for future |
| Taxes & benefits | 1040 + SSA + 1099-R / pension docs |
| Market | Refresh market + AI Estimates |

Machine-readable: `config/ai_mode/field_document_sources.json` and `config/lazy_mode/document_manifest.json` (shared document IDs).

---

## 4. AI Mode UX (target)

### 4.1 Header

**Guided | Expert | AI**

### 4.2 Flow (section wizard)

1. **Welcome** — What AI Mode does; local AI privacy note.  
2. **Connect (optional)** — Plaid balances & expenses.  
3. **Upload queue** — One section per document type (1040, W-2 self, W-2 spouse, statements…).  
   - “Where to get this” tips  
   - Upload PDF → **local extract** (deterministic parsers first, then Ollama JSON extraction)  
   - Review table → Apply  
4. **Market** — Refresh market (non-personal).  
5. **What’s still missing** — Core fields still empty → short manual form or “Open Guided for this section”.  
6. **AI Estimates** — Existing batch estimator for remaining *assumption* fields (cloud or configured LLM—not personal PDF parsing).  
7. **Review & run** — Completeness ring + Run plan.

### 4.3 Extraction pipeline (per upload)

```
PDF → text (pypdf/pymupdf)
    → deterministic extractor (1040/W-2/pay stub if available)
    → local Ollama structured extract (field_key, person, value, confidence, quote)
    → merge + confidence policy
    → review_rows → user Apply → snapshot
```

**Confidence:** high → default checked; medium → checked if empty; low → unchecked.

---

## 5. Architecture

| Layer | Responsibility |
|-------|----------------|
| `config/ai_mode/*` | Field↔document map, extract schemas |
| `app/core/ai_mode/` or extended `lazy_mode/` | Orchestration, gaps, local extract |
| `providers/document_extractors/*` | Deterministic parsers |
| `providers/local_doc_ai.py` | Ollama loopback JSON extraction |
| `gui_web/api.py` | Upload, extract, apply, screen |
| `live-app.js` | AI mode shell (evolved Lazy UI) |

---

## 6. Phased delivery

### Phase 0 — Plan + map (this doc) ✅

### Phase 1 — Mode enabled + rebrand ✅

- `presentation_mode = "ai"` (legacy `"lazy"` → `"ai"`)  
- Header pill **AI**  
- Section wizard + document checklist  

### Phase 2 — Local AI extract on upload ✅

- Deterministic parsers first, then local Ollama merge (`providers/local_doc_ai.py`)  
- Review modal before apply  

### Phase 3 — Gaps UX ✅

- Core gap list with Guided jump buttons (`app/core/ai_mode/gaps.py`)  

### Phase 4 — Estimates integration ✅

- AI Mode **Estimates** step with status + jump to Guided LLM assist  

### Phase 5 — More documents ✅ (best-effort)

- SSA, 1099-R, brokerage, 401(k) text extractors + local AI fallback  
- OCR for scans still deferred  

---

## 7. Risks

| Risk | Mitigation |
|------|------------|
| Wrong extraction | Review before apply; never silent overwrite high-value fields when set |
| Slow Ollama | One AI pass per upload; Fast parsers first; optional skip AI |
| Scanned PDFs | Detect empty text layer; prompt for digital PDF |
| Privacy confusion | UI copy: personal docs → local Ollama only |

---

## 8. Success criteria

1. User can complete a core plan with **1040 + W-2 + Plaid + market**, reviewing extracts, not typing wages/assets by hand.  
2. Remaining questions are **few** and clearly listed.  
3. Estimates still available for inflation/returns/healthcare defaults.  
4. No personal PDF content sent to cloud APIs for extraction.

---

*Next implementation step: enable AI mode in preferences/session/UI and local extract hook on document upload.*
