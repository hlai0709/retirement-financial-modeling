# Retirement Financial Modeling — Implementation Plan

**Version:** 1.0  
**Last updated:** 2026-06-19  
**Status:** Phase 1 complete — ready for user verification  
**Purpose:** Comprehensive reference for new AI/coding sessions. Read this file first.

---

## Table of Contents

1. [Project Vision](#1-project-vision)
2. [Current Baseline (What Exists Today)](#2-current-baseline-what-exists-today)
3. [User Profile and Institutions](#3-user-profile-and-institutions)
4. [Architecture Overview](#4-architecture-overview)
5. [Security Model (Non-Negotiable)](#5-security-model-non-negotiable)
6. [Plaid Integration](#6-plaid-integration)
7. [Apple Security Features](#7-apple-security-features)
8. [Data Sources and Field Mapping](#8-data-sources-and-field-mapping)
9. [Module Structure (Target)](#9-module-structure-target)
10. [Phase 1 — Keychain + Plaid + Live Assets](#10-phase-1--keychain--plaid--live-assets)
11. [Phase 2 — Live Market and Macro Data](#11-phase-2--live-market-and-macro-data)
12. [Phase 3 — Expenses and Billing](#12-phase-3--expenses-and-billing)
13. [Phase 4 — Monte Carlo Simulation](#13-phase-4--monte-carlo-simulation)
14. [Phase 5 — Beta Polish + AI Adviser](#14-phase-5--beta-polish--ai-adviser)
15. [Phase 6 — Native macOS App](#15-phase-6--native-macos-app)
16. [GUI Specifications](#16-gui-specifications)
17. [Testing Checklist](#17-testing-checklist)
18. [Dependencies](#18-dependencies)
19. [Workflow Rules for AI Sessions](#19-workflow-rules-for-ai-sessions)
20. [Open Decisions / TBD](#20-open-decisions--tbd)
21. [Session Handoff Template](#21-session-handoff-template)
22. [QNAP Private Git Remote (Deferred)](#22-qnap-private-git-remote-deferred)

---

## 1. Project Vision

Build an **extremely accurate, security-first retirement projection tool** that:

- Pulls **live** account balances, holdings, and transactions via **Plaid**
- Uses **real market and inflation data** (not hardcoded assumptions)
- Detects **recurring bills** from transaction history
- Runs **Monte Carlo** simulations for probability-of-success
- Includes an integrated **AI investment adviser** extension
- Processes **everything locally** on the user's Mac
- Ships first as a **Python beta**, then as a **native macOS SwiftUI app**

**User goal:** Retire with peace of mind — know the probability of not running out of money.

**Process rule:** User approves each phase before any code is saved.

---

## 2. Current Baseline (What Exists Today)

### Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — launches Tkinter GUI |
| `model/projection.py` | Deterministic year-by-year projection engine |
| `gui.py` | Scrollable input window + separate results window |
| `requirements.txt` | `pandas`, `matplotlib` only |

### Working features (do not break)

- Joint / separate household modes
- Year-by-year projection through life expectancy (bug-fixed year range)
- Input validation in `model/projection.py`
- Scrollable main GUI (Run Simulation reachable without maximizing)
- Results in separate `ResultsWindow` (table + chart)
- Chart y-axis with readable dollar labels (`$4.7M`, start/end callout)
- CSV export from results window

### Hardcoded assumptions to replace

| Constant | Location | Current value |
|----------|----------|---------------|
| `TSLA_BASELINE_PRICE` | `model/projection.py` | `426.0` |
| `investment_return_mean` | default param | `7%` |
| `inflation_rate` | default param | `3%` |
| All `initial_assets`, expenses, etc. | GUI defaults | Manual entry |

### What was reverted (do not re-add without plan)

A partial live-data draft was reverted. Beta implementation should follow **this document**, not old draft code.

---

## 3. User Profile and Institutions

### People

- **self** and **wife** — accounts mapped individually (split ownership)
- Each Plaid account assigned to: `self` | `wife` | `exclude`

### Financial institutions (initial)

Configure your real institutions in a **local-only** file: `data/institutions.local.md` (gitignored).

| Institution (example) | Type | Plaid support (expected) |
|-----------------------|------|--------------------------|
| Bank A | Bank | Yes (OAuth) |
| Brokerage B | Brokerage | Yes |
| Brokerage C | Brokerage | Yes |
| Bank D | Bank | Yes |

### Extensibility

- Adding a new institution = run Plaid Link again + map new accounts
- No code changes required per institution
- Config-driven account mapping in `data/accounts.json`

### Manual inputs (v1 — no API)

- Pension amount / start age
- Social Security amount / start age
- Rental income
- Tax rates
- Life expectancy, retirement ages

---

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Retirement_Financial_Modeling             │
│                         (local Mac only)                     │
├─────────────────────────────────────────────────────────────┤
│  gui.py          │  main.py                                 │
│  ResultsWindow   │                                          │
│  AdviserWindow   │                                          │
├──────────────────┴──────────────────────────────────────────┤
│  services/data_service.py          ← orchestrates all providers       │
├─────────────────────────────────────────────────────────────┤
│  model/projection.py (deterministic) │  simulation/monte_carlo.py (stochastic)   │
│  adviser/advisor_engine.py                                   │
├─────────────────────────────────────────────────────────────┤
│  security/                                                   │
│    keychain.py  → macOS Keychain (Plaid tokens)              │
│    vault.py     → encrypted local cache                      │
├─────────────────────────────────────────────────────────────┤
│  providers/                                                  │
│    plaid_provider.py   → balances, holdings, transactions    │
│    market_data.py      → yfinance prices & returns          │
│    fred_provider.py    → CPI / inflation                    │
│    expense_analyzer.py → recurring bill detection           │
│    csv_provider.py     → fallback manual import             │
└─────────────────────────────────────────────────────────────┘
         │ HTTPS read-only          │ HTTPS read-only
         ▼                          ▼
    ┌─────────┐              ┌──────────┐
    │  Plaid  │              │ yfinance │
    │   API   │              │   FRED   │
    └─────────┘              └──────────┘
```

### Data flow (refresh cycle)

1. User clicks **Refresh Live Data**
2. `services/data_service.py` reads Plaid tokens from **Keychain**
3. Fetches balances, holdings, transactions from Plaid
4. Fetches market prices and CPI from yfinance / FRED
5. Writes encrypted snapshot to local vault cache
6. Shows **review dialog** — user confirms before updating form fields
7. User clicks **Run Simulation** → deterministic and/or Monte Carlo

---

## 5. Security Model (Non-Negotiable)

### Principles

1. **Local-only processing** — no custom cloud backend
2. **No bank passwords in our app** — ever
3. **Plaid tokens in Keychain only** — never plaintext JSON or `.env`
4. **Encrypted cache** for financial snapshots on disk
5. **Review-before-apply** — live data never silently overwrites inputs
6. **Disconnect** — one-click token removal + optional Plaid item revoke
7. **No telemetry** — no analytics, no external AI for financial data (beta)
8. **Gitignore all secrets** — `.env`, vault, cache, `data/accounts.json`
9. **Private git remote** — primary backup on local QNAP NAS (see [§22](#22-qnap-private-git-remote-deferred)); GitHub optional and must stay private

### Data directory

```
~/Library/Application Support/RetirementPlanner/
  vault.enc              # encrypted cache (balances, transactions, market)
  accounts.json          # account_id → owner mapping (no secrets)
  refresh_log.json       # timestamps and sources (no credentials)
```

Permissions: directory mode `700`, files mode `600`.

### What goes where

| Secret / data | Storage |
|---------------|---------|
| `PLAID_CLIENT_ID`, `PLAID_SECRET` | `.env` (API keys only, not tokens) |
| Plaid `access_token` per Item | **macOS Keychain** via `keyring` |
| Vault encryption key | **macOS Keychain** |
| Cached balances / transactions | `vault.enc` (encrypted) |
| Bank username/password | **Never stored** — entered in Plaid Link only |

### Threat model notes

- Plaid is a trusted third party (SOC 2, used by major fintech apps)
- We do **not** store bank credentials — better than DIY password vault
- If Mac is compromised, Keychain provides additional OS-level protection
- User can revoke Plaid access from [dashboard.plaid.com](https://dashboard.plaid.com) at any time

---

## 6. Plaid Integration

### Why Plaid

- Industry standard in the US for personal finance aggregation
- Used by Venmo, Robinhood, AmEx, Chime, and thousands of others
- Read-only access — cannot move money through our integration
- Supports Chase, Robinhood, Schwab, Ally (verify in sandbox first)

### Account setup (user steps)

1. Create free developer account at [dashboard.plaid.com](https://dashboard.plaid.com)
2. Copy `PLAID_CLIENT_ID` and `PLAID_SECRET` to local `.env`
3. Start with `PLAID_ENV=sandbox` for development
4. Move to Trial/Production for real accounts (free Trial: up to 10 Items)

### Plaid products to enable

| Product | Purpose |
|---------|---------|
| `assets` | Account balances |
| `investments` | Holdings, securities, 401k/brokerage values |
| `transactions` | Spending history for bill detection |

### Authentication flow (Plaid Link)

```
User clicks "Connect Accounts"
  → App calls link_token_create
  → Browser opens Plaid Link
  → User picks institution (e.g. Chase)
  → User enters credentials IN PLAID'S UI (not our app)
  → Plaid returns public_token
  → App exchanges for access_token
  → access_token saved to macOS Keychain immediately
  → Account list cached for mapping
```

**Re-authentication:** Required only when token expires or user revokes access — not every refresh.

### Plaid Item model

- One **Item** = one institution connection (e.g. one bank login)
- User connects institutions as needed (see `data/institutions.local.md`)
- Each Item can have multiple accounts (checking, savings, brokerage, IRA)
- Trial plan supports up to 10 Items (room to grow)

### Key API calls

| Endpoint | When | Data |
|----------|------|------|
| `link_token_create` | Connect Accounts | Start Link flow |
| `item_public_token_exchange` | After Link | Get access_token |
| `accounts_balance_get` | Refresh | Cash account balances |
| `investments_holdings_get` | Refresh | Brokerage positions and values |
| `transactions_sync` | Refresh (Phase 3) | Transaction history |
| `item_remove` | Disconnect | Revoke access |

### Double-counting prevention

| Account subtype | Use balance from |
|-----------------|------------------|
| `depository` (checking/savings) | `accounts_balance_get` |
| `investment` (brokerage, IRA) | `investments_holdings_get` (sum of holdings) |
| `credit` | Balance for expense analysis only — not assets |

Never sum `accounts_balance_get` AND `investments_holdings_get` for the same investment account.

### Fallback: CSV import

If Plaid cannot reach an institution:

- User exports CSV from bank/broker
- Format: `account_name,balance` (+ optional `owner` column)
- Import via GUI — stored locally, no API needed

---

## 7. Apple Security Features

### Beta (Python) — use now

| Feature | Implementation | Purpose |
|---------|----------------|---------|
| **Keychain Services** | Python `keyring` library | Store Plaid tokens and vault key |
| **Application Support dir** | `~/Library/Application Support/RetirementPlanner/` | Standard macOS app data location |
| **File permissions** | `os.chmod` 700/600 | Restrict access to user only |
| **File encryption** | `cryptography` (Fernet/AES) | Encrypt cached financial data |

### Native app (Phase 6) — use later

| Feature | Purpose |
|---------|---------|
| **Keychain Services** (native Swift) | Secrets |
| **CryptoKit** | Encryption at rest |
| **App Sandbox** | Hardened attack surface |
| **Hardened Runtime** | Code signing integrity |
| **Foundation Models** | On-device AI adviser |
| **Swift Charts** | Native fan charts |

### What Apple does NOT provide

- **No Apple API** to pull Chase / Schwab / Robinhood / Ally data
- **FinanceKit** — only Apple Card / Savings / Cash (user does not use these) — skip for now

---

## 8. Data Sources and Field Mapping

### Model input fields → data source

| Model field | Source (automated) | Fallback |
|-------------|-------------------|----------|
| `initial_assets` | Plaid balances + holdings | CSV import / manual |
| `annual_contributions` | Plaid investment deposits (12mo) | Manual |
| `base_expenses` | Plaid transactions → expense analyzer | Manual |
| `investment_return_mean` | Portfolio-weighted historical returns (yfinance) | S&P 500 10yr CAGR |
| `inflation_rate` | FRED CPI YoY change | Manual / cached |
| `tsla_current_price` | yfinance live TSLA price | Manual |
| `pension_amount` | — | Manual |
| `ss_amount` | — | Manual |
| `rental_income_start` | — | Manual |
| `pre/post_retire_tax_rate` | — | Manual |

### Metadata on every field

```json
{
  "value": 800000.0,
  "source": "plaid",
  "source_detail": "Example bank + brokerage holdings",
  "as_of": "2026-06-20T14:30:00",
  "confidence": "live"
}
```

Display in GUI as: `Live (Plaid, Jun 20)` or `Manual` or `Estimated (12mo avg)`.

---

## 9. Module Structure (Target)

```
Retirement_Financial_Modeling/
├── main.py
├── model/projection.py                    # deterministic engine (existing, extend)
├── simulation/monte_carlo.py              # NEW — Phase 4
├── services/data_service.py             # NEW — orchestrator
├── settings.py                 # NEW — paths, config
├── gui.py                      # existing, extend
├── setup_plaid.py              # NEW — Plaid Link launcher
├── list_accounts.py            # NEW — account listing helper
├── security/
│   ├── __init__.py
│   ├── keychain.py             # Keychain wrapper (keyring)
│   └── vault.py                # encrypted file cache
├── providers/
│   ├── __init__.py
│   ├── plaid_provider.py
│   ├── market_data.py
│   ├── fred_provider.py
│   ├── expense_analyzer.py
│   └── csv_provider.py
├── adviser/
│   ├── __init__.py
│   └── advisor_engine.py       # rule-based adviser
├── config/
│   └── accounts.example.json
├── data/                       # gitignored runtime data
├── docs/
│   └── IMPLEMENTATION_PLAN.md  # this file
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## 10. Phase 1 — Keychain + Plaid + Live Assets

**Goal:** Connect configured institutions via Plaid; refresh real balances into `initial_assets`.

### 10.1 Tasks

| # | Task | File(s) |
|---|------|---------|
| 1.1 | Add `security/keychain.py` — store/retrieve/delete Plaid tokens | `security/keychain.py` | ✅ |
| 1.2 | Add `security/vault.py` — encrypt/decrypt JSON cache | `security/vault.py` | ✅ |
| 1.3 | Add `settings.py` — paths, env loading, config | `settings.py` | ✅ |
| 1.4 | Add `providers/plaid_provider.py` — Link, exchange, balances, holdings | `providers/plaid_provider.py` | ✅ |
| 1.5 | Add `services/data_service.py` — orchestrate refresh, aggregate by owner | `services/data_service.py` | ✅ |
| 1.6 | Add `setup_plaid.py` — local HTTP server + Plaid Link in browser | `setup_plaid.py` | ✅ |
| 1.7 | Add `list_accounts.py` — print accounts for mapping | `list_accounts.py` | ✅ |
| 1.8 | Add `config/accounts.example.json` — mapping template | `config/accounts.example.json` | ✅ |
| 1.9 | Extend `gui.py` — Live Data panel (Connect / Refresh / Disconnect) | `gui.py` | ✅ |
| 1.10 | Add review dialog before applying to form fields | `gui.py` | ✅ |
| 1.11 | Update `.gitignore`, `.env.example`, `requirements.txt` | config files | ✅ |

### 10.2 `accounts.json` schema

```json
{
  "institutions": [
    {
      "item_id": "plaid_item_xxx",
      "institution_name": "Bank A",
      "connected_at": "2026-06-20"
    }
  ],
  "account_mappings": [
    {
      "account_id": "plaid_account_xxx",
      "institution": "Bank A",
      "name": "Checking Account",
      "owner": "self",
      "include_in_assets": true
    },
    {
      "account_id": "plaid_account_yyy",
      "institution": "Brokerage B",
      "name": "Brokerage Account",
      "owner": "self",
      "include_in_assets": true
    },
    {
      "account_id": "plaid_account_zzz",
      "institution": "Bank C",
      "name": "Savings Account",
      "owner": "wife",
      "include_in_assets": true
    }
  ]
}
```

### 10.3 Acceptance criteria

- [ ] Sandbox: connect fake institution, balances appear in review dialog
- [ ] Production/Trial: connect each configured institution — balances match institution app
- [ ] Brokerage holdings values match institution app
- [ ] All configured institutions connect successfully
- [ ] Plaid token in Keychain — NOT in any JSON/plaintext file
- [ ] `accounts.json` maps accounts to self/wife correctly
- [ ] Review dialog shows per-account breakdown before apply
- [ ] Disconnect removes Keychain token
- [ ] Adding 5th institution works without code changes
- [ ] Existing simulation still runs with manual values (no regression)

### 10.4 User verification steps

```bash
cd Retirement_Financial_Modeling
cp .env.example .env          # add Plaid keys
source .venv/bin/activate
pip install -r requirements.txt
python setup_plaid.py link    # connect each institution
python list_accounts.py       # verify accounts and balances
python main.py                # Refresh Live Data → review → Run Simulation
```

---

## 11. Phase 2 — Live Market and Macro Data

**Goal:** Replace hardcoded 7% return, 3% inflation, $426 TSLA baseline.

### 11.1 Tasks

| # | Task | File(s) |
|---|------|---------|
| 2.1 | `providers/market_data.py` — yfinance prices and historical returns | new |
| 2.2 | `providers/fred_provider.py` — CPI series `CPIAUCSL` | new |
| 2.3 | Portfolio-weighted return from Plaid holdings + yfinance | `services/data_service.py` |
| 2.4 | Extend `model/projection.py` — accept live `tsla_current_price`, source metadata | `model/projection.py` |
| 2.5 | GUI: show source labels on market fields | `gui.py` |
| 2.6 | Cache market data in vault with stale-data warnings (>24h) | `security/vault.py` |

### 11.2 FRED API

- Free API key from [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)
- Series: `CPIAUCSL` (Consumer Price Index)
- Compute YoY % change for `inflation_rate`

### 11.3 Acceptance criteria

- [ ] TSLA price matches Yahoo Finance on refresh day
- [ ] Inflation rate matches latest CPI YoY
- [ ] Portfolio return reflects actual holdings weights
- [ ] Stale cache shows warning if APIs unavailable
- [ ] `Formula_Notes` includes data source and date

---

## 12. Phase 3 — Expenses and Billing

**Goal:** Derive `base_expenses` from real transaction history.

### 12.1 Tasks

| # | Task | File(s) |
|---|------|---------|
| 3.1 | Plaid `transactions_sync` — pull 12–24 months | `plaid_provider.py` |
| 3.2 | `providers/expense_analyzer.py` — recurring bill detection | new |
| 3.3 | Categorize: mortgage, utilities, insurance, subscriptions, discretionary | new |
| 3.4 | Exclude transfers, investment moves, income deposits | new |
| 3.5 | GUI: itemized expense review dialog | `gui.py` |
| 3.6 | Joint mode: household expense total (no double-count) | `services/data_service.py` |

### 12.2 Recurring bill detection logic

```
For each merchant name:
  - Find transactions with similar amounts (±10%) at regular intervals (28–31 days)
  - If ≥3 occurrences → classify as recurring bill
  - Sum all recurring bills → monthly fixed expenses
  - Average non-recurring spending → monthly variable expenses
  - Annualize: (fixed + variable) × 12 → suggested base_expenses
```

### 12.3 Acceptance criteria

- [ ] Identifies major recurring bills by name
- [ ] Itemized list shown before applying
- [ ] User can exclude miscategorized items
- [ ] Suggested total is within reasonable range of actual spending
- [ ] Works with Chase checking/credit transactions

---

## 13. Phase 4 — Monte Carlo Simulation

**Goal:** Answer "What is my probability of not running out of money?"

### 13.1 Tasks

| # | Task | File(s) |
|---|------|---------|
| 4.1 | `simulation/monte_carlo.py` — N-path simulation engine | new |
| 4.2 | Calibrate return distribution from historical data (Phase 2) | `simulation/monte_carlo.py` |
| 4.3 | Calibrate inflation distribution from FRED history | `simulation/monte_carlo.py` |
| 4.4 | Reuse `_project_for_person` logic per path | `model/projection.py` / `simulation/monte_carlo.py` |
| 4.5 | Output: success rate, percentile paths (10/50/90) | `simulation/monte_carlo.py` |
| 4.6 | GUI: fan chart in results window | `gui.py` |
| 4.7 | Mode selector: Deterministic / Monte Carlo / Both | `gui.py` |

### 13.2 Simulation parameters

| Parameter | Default | User-adjustable |
|-----------|---------|-----------------|
| Number of paths | 5,000 | Yes |
| Return model | Historical bootstrap (10yr) | Yes |
| Inflation model | CPI bootstrap (10yr) | Yes |
| Random seed | None (true random) | Optional fixed seed for reproducibility |

### 13.3 Outputs

| Metric | Description |
|--------|-------------|
| Success rate | % paths with assets > 0 at life expectancy |
| Median ending balance | 50th percentile |
| Worst realistic case | 10th percentile |
| Best realistic case | 90th percentile |
| Fan chart | Shaded band between 10th and 90th percentile per year |

### 13.4 Acceptance criteria

- [ ] 5,000 paths complete in <30 seconds on M-series Mac
- [ ] Success rate is stable across runs (±1% with different seeds)
- [ ] Fan chart renders in results window
- [ ] Deterministic path matches existing `model/projection.py` output when volatility = 0
- [ ] Results exportable to CSV

---

## 14. Phase 5 — Beta Polish + AI Adviser

### 14.1 Beta polish tasks

| # | Task |
|---|------|
| 5.1 | Account mapping GUI (dropdown per account: self/wife/exclude) |
| 5.2 | Data source labels on every input field |
| 5.3 | Exportable assumptions report (PDF or CSV) |
| 5.4 | Contribution estimate from investment deposit transactions |
| 5.5 | CSV import fallback in GUI |
| 5.6 | Clear-cache and disconnect-all controls |

### 14.2 AI Investment Adviser (`adviser/advisor_engine.py`)

**Integrated extension** — separate tab/window, shares data with projection.

#### Inputs

- Monte Carlo success rate and percentile outcomes
- Live portfolio holdings (from Plaid)
- Retirement goals (target age, expenses, asset target)
- User risk tolerance (conservative / moderate / aggressive)

#### Outputs (rule-based — Phase 5)

| Analysis | Example output |
|----------|----------------|
| Goal gap | "Success rate 78%. Need $400k more by retirement." |
| Allocation review | "TSLA is 42% of portfolio. Typical for your timeline: 15–25%." |
| Rebalance suggestion | "Trim $180k TSLA → diversify into VTI/VXUS." |
| Contribution coaching | "+$5k/yr contributions → success rate 78% → 91%." |
| Action checklist | Prioritized steps with estimated impact |

#### AI layer (Phase 6 native app only)

- Apple **Foundation Models** — on-device natural language explanations
- Structured JSON output validated before display
- **No external AI API** for financial data in final app

#### Disclaimer (required in UI)

> This tool provides educational analysis only. It is not licensed financial, tax, or investment advice. Consult a qualified professional before making financial decisions.

### 14.3 Acceptance criteria

- [ ] Adviser tab shows goal gap and top 3 recommendations
- [ ] Recommendations derived from real numbers (no hallucination)
- [ ] "Apply what-if" pre-fills simulation parameters (user confirms)
- [ ] Disclaimer visible on adviser screen

---

## 15. Phase 6 — Native macOS App

**Prerequisite:** Beta Phases 1–5 validated by user.

### 15.1 Scope

- New Xcode project: `RetirementPlanner.app`
- SwiftUI interface replicating all beta functionality
- Port projection + Monte Carlo logic to Swift (preferred over embedded Python)
- Native adviser with Foundation Models

### 15.2 Apple APIs

| API | Use |
|-----|-----|
| Keychain Services | Plaid tokens, vault key |
| CryptoKit | Encrypt cached data |
| Swift Charts | Fan charts, asset trajectories |
| Foundation Models | On-device AI adviser |
| App Sandbox + Hardened Runtime | Distribution security |

### 15.3 Migration from beta

- Export `accounts.json` mapping from Python beta
- Plaid tokens: user re-links via Plaid Link in native app (or Keychain migration utility)
- Shared JSON schema for assumptions and parameters

### 15.4 Distribution options (decide later)

- Direct download (signed .dmg)
- Mac App Store (requires Sandbox review)

---

## 16. GUI Specifications

### Main window (input)

- Scrollable (existing)
- Sections: Mode | Shared Parameters | Self | Wife | Live Data | Run Simulation
- Live Data panel (Phase 1):
  - Status label (last refresh time, sources)
  - Buttons: Connect Accounts | Refresh Live Data | Disconnect All
  - Checkbox: Update investment return from market benchmark

### Results window (existing pattern)

- Separate `tk.Toplevel` window
- Tabs: Joint / Self / Wife (or Monte Carlo tab added in Phase 4)
- Per tab: scrollable table + chart + CSV export

### New: Review dialog (Phase 1)

- Modal before applying live data
- Table: Institution | Account | Balance | Owner | Include?
- Buttons: Apply All | Cancel

### New: Adviser window (Phase 5)

- Separate window or results tab
- Sections: Goal Status | Recommendations | Action Checklist
- Disclaimer footer

---

## 17. Testing Checklist

### Per-phase regression

After every phase, verify:

- [ ] `python main.py` launches without error
- [ ] Manual inputs still work without Plaid configured
- [ ] Run Simulation produces results window
- [ ] Chart renders with dollar labels
- [ ] CSV export works

### Phase 1 integration tests

```bash
python -c "from security.keychain import store_token, get_token; ..."
python -c "from providers.plaid_provider import PlaidProvider; ..."
python model/projection.py   # existing smoke test
```

### Institution verification matrix

| Institution | Connect | Balances | Holdings | Transactions |
|-------------|---------|----------|----------|--------------|
| Bank A | | | | |
| Brokerage B | | | | |
| Brokerage C | | | | |
| Bank D | | | | |

Fill in during Phase 1 testing.

---

## 18. Dependencies

### Beta `requirements.txt` (target)

```
pandas>=2.0
matplotlib>=3.7
numpy>=1.24
yfinance>=0.2
plaid-python>=24.0
python-dotenv>=1.0
cryptography>=41.0
keyring>=24.0
requests>=2.28
```

### Environment variables (`.env`)

```
PLAID_CLIENT_ID=your_client_id
PLAID_SECRET=your_sandbox_secret
PLAID_ENV=sandbox
FRED_API_KEY=your_fred_key
```

**Never in `.env`:** Plaid access tokens (Keychain only).

---

## 19. Workflow Rules for AI Sessions

When continuing this project in a new session:

1. **Read this file first** — `docs/IMPLEMENTATION_PLAN.md`
2. **Read current code** — `model/projection.py`, `gui.py`, `main.py`
3. **Check which phase is active** — see Session Handoff Template below
4. **Propose changes** — describe what you will modify before saving
5. **Wait for user approval** — user must say "go ahead" or equivalent
6. **Implement one phase at a time** — do not skip ahead
7. **Run tests** — verify acceptance criteria before marking phase complete
8. **Update this document** — check off completed items, note deviations

---

## 20. Open Decisions / TBD

| # | Decision | Status |
|---|----------|--------|
| 1 | macOS version target for native app | TBD |
| 2 | Apple Developer Program enrollment | TBD (needed Phase 6) |
| 3 | FRED API key obtained | TBD |
| 4 | Plaid developer account created | TBD |
| 5 | Risk tolerance setting for adviser | TBD — add to GUI in Phase 5 |
| 6 | Monte Carlo default path count | Default 5,000 — confirm with user |
| 7 | SS/pension manual entry format | Keep current GUI fields for v1 |
| 8 | Mac App Store vs direct download | Decide in Phase 6 |

---

## 21. Session Handoff Template

Copy this block into new sessions and fill in current state:

```
## Retirement Financial Modeling — Session State

**Plan doc:** Retirement_Financial_Modeling/docs/IMPLEMENTATION_PLAN.md
**Current phase:** [1 / 2 / 3 / 4 / 5 / 6 / not started]
**Last completed task:** [e.g. "Phase 1 acceptance criteria all checked"]
**In progress:** [e.g. "Implementing plaid_provider.py"]
**Blocked on:** [e.g. "User needs to create Plaid developer account"]

**Institutions connected:** [list from institutions.local.md]
**Accounts mapped:** [yes/no]

**User rules:**
- Approve all edits before saving
- Security-first, local-only
- Plaid for live data, Keychain for tokens
```

---

## Phase Summary (quick reference)

| Phase | Deliverable | Key files |
|-------|-------------|-----------|
| **1** | Live assets via Plaid + Keychain | `security/`, `providers/plaid_provider.py`, `gui.py` |
| **2** | Live market + inflation | `providers/market_data.py`, `providers/fred_provider.py` |
| **3** | Real expenses from transactions | `providers/expense_analyzer.py` |
| **4** | Monte Carlo + fan charts | `simulation/monte_carlo.py`, results GUI |
| **5** | Adviser + polish | `adviser/advisor_engine.py`, mapping GUI |
| **6** | Native macOS SwiftUI app | New Xcode project |

**Start here:** Phase 1, task 1.1 — after user says "implement Phase 1".

---

## 22. QNAP Private Git Remote (Deferred)

**Status:** Not started — user will set this up when ready to push to a private remote.  
**Scope:** Entire monorepo (`myprojects` — includes `Retirement_Financial_Modeling/` and `Fantasy_BBall/`).  
**Goal:** Host a private Git remote on local QNAP NAS; optional secondary backup to GitHub (private only).

### Why QNAP instead of (or in addition to) GitHub

- Keeps code and history on your own hardware — no public exposure
- Works on your home network without internet
- Good primary backup for projects with financial data and API tokens (even when gitignored locally)

### Prerequisites (fill in when ready)

| Item | Your value (fill at setup time) |
|------|----------------------------------|
| QNAP hostname or IP | e.g. `192.168.1.50` or `qnap.local` |
| SSH username | e.g. `admin` or your QNAP user |
| SSH port | Default `22` (or custom in QNAP settings) |
| Share path for repos | e.g. `/share/homes/youruser/git/` or `/share/git/` |
| Mac repo path | `/Users/henrylai/myprojects` |

### Before first push to NAS

Complete these even if GitHub is no longer used:

- [ ] Make `hlai0709/myprojects` **private** on GitHub, or remove the `origin` remote after NAS is working
- [ ] Revoke/rotate any credentials that were ever in public git history (Yahoo OAuth, Anthropic API key)
- [ ] Confirm `secrets.json`, `.env`, `oauth2.json`, and `data/` runtime files are **not** tracked (see §5 and `.gitignore`)

### Step 1 — Enable SSH on QNAP

1. QNAP web UI → **Control Panel** → **Network & File Services** → **Telnet / SSH**
2. Enable **SSH** (allow SFTP if offered — optional)
3. Note the port (default 22)
4. Ensure your Mac can reach the NAS: `ping qnap.local` or `ping <NAS_IP>`

**Test from Mac:**

```bash
ssh youruser@qnap.local
# or: ssh -p 22 youruser@192.168.1.50
```

### Step 2 — Create repo directory on QNAP

SSH into the QNAP and create a bare repository (bare = no working tree; correct for a remote):

```bash
# On QNAP (via SSH)
mkdir -p /share/homes/youruser/git
cd /share/homes/youruser/git
mkdir myprojects.git
cd myprojects.git
git init --bare
```

**Path notes for QNAP:**

- User home is often `/share/homes/<username>/`
- Shared folders appear as `/share/<ShareName>/`
- Prefer a dedicated `git` share or a `git/` folder under your home — avoid putting repos in actively synced cloud folders

**Permissions:** Restrict the `git` folder to your QNAP user only (File Station → folder properties → permissions).

### Step 3 — (Recommended) SSH key auth from Mac

Avoid typing your NAS password on every push:

```bash
# On Mac — generate key if you don't have one
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -C "mac-to-qnap"

# Copy public key to QNAP
ssh-copy-id youruser@qnap.local

# Verify passwordless login
ssh youruser@qnap.local "echo ok"
```

If `ssh-copy-id` is unavailable, paste `~/.ssh/id_ed25519.pub` into QNAP **Control Panel → Privilege → Users → your user → Edit → SSH public key** (wording may vary by QTS version).

### Step 4 — Add NAS as a Git remote (Mac)

```bash
cd /Users/henrylai/myprojects

# SSH remote (preferred — works without SMB mount)
git remote add nas ssh://youruser@qnap.local/share/homes/youruser/git/myprojects.git

# If non-default SSH port:
# git remote add nas ssh://youruser@qnap.local:2222/share/homes/youruser/git/myprojects.git
```

**Alternative — SMB mount (simpler but less reliable for Git):**

```bash
# Mount NAS share in Finder first, e.g. /Volumes/NAS
git remote add nas /Volumes/NAS/git/myprojects.git
```

Prefer SSH for daily use; SMB mounts can drop mid-push.

### Step 5 — First push

```bash
cd /Users/henrylai/myprojects
git push -u nas main
```

Verify on QNAP:

```bash
ssh youruser@qnap.local "ls -la /share/homes/youruser/git/myprojects.git/refs/heads/"
```

### Step 6 — Ongoing workflow

```bash
# After local commits — backup to NAS
git push nas main

# Optional: also push to GitHub (keep repo PRIVATE)
git push origin main
```

**Suggested remote layout:**

| Remote | URL | Role |
|--------|-----|------|
| `nas` | `ssh://youruser@qnap.local/.../myprojects.git` | Primary private backup |
| `origin` | `https://github.com/hlai0709/myprojects.git` | Optional; must stay private or be removed |

To remove GitHub entirely after NAS is verified:

```bash
git remote remove origin
git remote -v   # should show only nas
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `Permission denied (publickey)` | Re-run `ssh-copy-id`; check QNAP user has SSH access |
| `fatal: not a git repository` | Ensure remote path ends in `.git` and you ran `git init --bare` |
| Push hangs or fails over SMB | Switch to SSH remote |
| `detected dubious ownership` | On Mac: `git config --global --add safe.directory /Users/henrylai/myprojects` |
| Wrong branch name | Use `git push -u nas main` (or your branch name) |

### Optional — Gitea on QNAP (future)

For a web UI (browse code, multiple repos), install **Gitea** via QNAP Container Station (Docker). Defer until bare-repo SSH workflow is working. Gitea adds:

- Browser-based repo view
- Multiple projects without manual bare-repo setup
- HTTPS/SSH remotes from any device on the LAN

### Acceptance criteria (QNAP setup complete)

- [ ] `git remote -v` shows `nas` pointing at QNAP bare repo
- [ ] `git push nas main` succeeds without errors
- [ ] QNAP bare repo contains `refs/heads/main`
- [ ] SSH key login works (no password prompt)
- [ ] GitHub is private or `origin` removed
- [ ] No secrets or personal data files are tracked (re-run "Before first push" checklist above)

### When ready — session prompt for AI

Copy into a new session:

```
Set up QNAP git remote for myprojects.

NAS: [hostname/IP]
SSH user: [username]
SSH port: [22 or custom]
Share path: [/share/homes/.../git/]

Follow IMPLEMENTATION_PLAN.md §22.
```

---

## 23. Phase 9 — Verification and Validation (2026-06)

**Status:** In progress  
**Goal:** A/A+ accuracy confidence via replayable tax checks and live IRS tables.

### Deliverables

| Item | Location | Status |
|------|----------|--------|
| Official IRS policy JSON (2024–2026) | `config/policy/updates/official/` | Done |
| Active policy loader | `config/policy/live.py`, `policy_for_projection()` | Done |
| Policy CLI | `tools/fetch_policy_tables.py` | Done |
| Tax return validator | `validation/tax_return_validator.py` | Done |
| GUI validate button | `gui.py` → **Validate Tax Return** | Done |
| Verification guide | `docs/VERIFICATION_AND_VALIDATION.md` | Done |
| MC stock/bond blend | `simulation/monte_carlo.py` + GUI allocation % | Done |
| CPA round 2 (working-year itemized, MA/NJ, state warnings) | `model/payroll.py`, `model/state_taxes.py` | Done |

### Workflow for new sessions

1. Read `docs/VERIFICATION_AND_VALIDATION.md`
2. `python tools/fetch_policy_tables.py --ensure-latest`
3. User supplies tax return JSON → **Validate Tax Return**
4. Run `python scripts/regression_test.py` only when user asks

### Acceptance criteria

- [ ] User tax return JSON validates within tolerance for at least one recent year
- [ ] Active policy year visible in GUI and assumptions export
- [ ] Golden scenarios pass after policy activation
- [ ] MC blended bootstrap runs with 70% stock default

---

*End of implementation plan.*