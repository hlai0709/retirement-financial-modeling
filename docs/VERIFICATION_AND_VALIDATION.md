# Verification and Validation

**Purpose:** How to confirm the retirement model matches real-world tax rules and your own return before trusting projections.

---

## 1. Validation layers

| Layer | What it checks | How to run |
|-------|----------------|------------|
| **Regression suite** | Engine invariants, golden tax scenarios, CPA mode, Monte Carlo | `python scripts/regression_test.py` |
| **Golden-file taxes** | 50+ IRS-style bracket/SS/Medicare scenarios | `python validation/golden_runner.py` |
| **Tax return replay** | Your Form 1040 lines vs model for one tax year | GUI **Validate Tax Return** or CLI below |
| **Live policy tables** | Brackets/deductions match published IRS year | `python tools/fetch_policy_tables.py --status` |

Run regression only when you explicitly want a full sweep (takes several minutes with Monte Carlo perf check).

---

## 2. Official IRS tax tables (always current)

Bundled official tables live in `config/policy/updates/official/YYYY.json` (2024–2026 today). The active year is stored in `config/policy/updates/policy_active.json`.

```bash
# List bundled years (* = active)
python tools/fetch_policy_tables.py --list

# Show active policy
python tools/fetch_policy_tables.py --status

# Auto-pull from IRS newsroom (parses brackets + standard deduction)
python tools/fetch_policy_tables.py --fetch 2026
python tools/fetch_policy_tables.py --pull-latest    # fetch default_year + activate

# Preview without writing files
python tools/fetch_policy_tables.py --fetch 2026 --dry-run

# Switch to an already-downloaded year
python tools/fetch_policy_tables.py --activate 2025

# Activate manifest default if none set (no network)
python tools/fetch_policy_tables.py --ensure-latest
```

The GUI shows the active label at the top of the **Simulation** panel. Activating a year updates federal brackets, standard deductions, SS thresholds, NIIT, AMT, and Medicare baselines for new runs.

**Auto-pull behavior:** downloads the IRS newsroom inflation-adjustments page for the tax year, parses federal ordinary brackets, standard deductions, and AMT exemptions, then scales HOH/LTCG/SS/Medicare/FPL from the prior bundled year. Add `newsroom_url` to `manifest.json` when IRS publishes a new year with a non-standard URL slug.

**Manual fallback:** copy the prior `official/YYYY.json`, fill values from Rev. Proc. PDF, update `manifest.json`, then `--activate`.

---

## 3. Validate against your tax return

### Step 1 — Copy the template

```bash
cp validation/tax_return_template.json ~/my_2024_return.json
```

### Step 2 — Map Form 1040 lines

| Form 1040 line | JSON field |
|----------------|------------|
| 1z Wages | `income.wages` |
| 2b Taxable interest | `income.taxable_interest` |
| 3b Ordinary dividends | `income.ordinary_dividends` |
| 4b IRA distributions | `income.ira_distributions` |
| 5b Pensions | `income.pension` |
| 6b Social Security | `income.social_security_gross` |
| 7 Capital gain | `income.capital_gain` |
| 11 AGI | `form_1040.agi` |
| 15 Taxable income | `form_1040.taxable_income` |
| 16 Income tax | `form_1040.income_tax` |
| 24 Total tax | `form_1040.total_tax` |
| State return (optional) | `form_1040.state_income_tax` |

Set `tax_year`, `filing_status`, and `state_of_residence` to match the return. For CPA itemized returns, fill `itemized` and set `planning_precision` to `"cpa"`.

### Step 3 — Run validation

**GUI:** **Validate Tax Return** → select your JSON file.

**CLI:**

```bash
python -c "
from validation.tax_return_validator import validate_tax_return_file
from pathlib import Path
r = validate_tax_return_file(Path('~/my_2024_return.json').expanduser())
print(r.summary_text())
"
```

The tool auto-activates the matching IRS policy year when the JSON file is on disk. Adjust `tolerance` if minor rounding differences are expected.

### Example fixture

`validation/examples/tax_return_2024_mfj_wages.json` — 2024 MFJ, $100k wages only (used in regression).

---

## 4. Monte Carlo validation notes

- **Stock/bond blend:** Set **MC Stock Allocation %** (default 70). Bootstrap blends `^GSPC` and `AGG` by that weight.
- **CPA + Monte Carlo:** CPA precision is slower per path; use ~1,000 paths unless you have time for 5,000.
- **Correlated sampling:** Requires `FRED_API_KEY` in `.env` for CPI pairing with returns.

---

## 5. When numbers disagree

1. Confirm active tax year matches the return (`--status`).
2. Check filing status and state code.
3. For itemized returns (working or retired years), switch to **CPA Precision** and fill Schedule A fields.
4. Modeled states include **MA** and **NJ**; unknown states in standard mode warn and estimate using California.
5. Widen `tolerance.federal_tax` slightly for rounding / worksheet differences.
6. State tax: set `state_tax_override_pct` if your state profile is approximate.

---

## 6. Refresh golden files after policy change

```bash
python tools/fetch_policy_tables.py --activate 2025
python validation/build_golden_file.py   # if regenerating expected values
python scripts/regression_test.py
```

---

*See also: `docs/IMPLEMENTATION_PLAN.md` § Phase 9.*