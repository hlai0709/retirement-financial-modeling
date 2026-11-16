# Screen 1 Mockup — Guided · Household setup (DRAFT)

**Status:** ✅ Locked — **Liquid Glass** (iOS 26 inspired, dark). See [`UX_DESIGN_LANGUAGE.md`](UX_DESIGN_LANGUAGE.md)

### Interaction rules (locked)
- **Back** — grayed on step 1; active from step 2 onward
- **Skip** — active whenever step is skippable
- **Continue** — always active (advances wizard)
- **Run plan** — grayed on all steps except review; white primary when active
- Disabled = same pill shape, muted gray fill/text, no shadow, `not-allowed` cursor
- **Step progress** — footer strip only (`Step 1 of 14` · dots · `Start`); no top-right badge
- **Title** — left-aligned with card; title case (`Household Setup`); Help on the right  
**Delivery:** HTML in browser (chat images did not render)  
**Not implemented in Python yet**

## Open the mockup

```bash
open "/Users/henrylai/myprojects/Retirement_Financial_Modeling/design/mockups/screen-01-guided-setup.html"
```

**Canonical mockup:** [`design/mockups/screen-01-variant-c-liquid-glass.html`](../design/mockups/screen-01-variant-c-liquid-glass.html)

---

## Layout sketch

```
┌─ Header: Retirement Planner  [Guided|Expert]     build label ─┐
├─────────────────────────────────────────────────────────────────┤
│  START · STEP 1 OF 14                                           │
│  Household setup                         ← 26pt semibold        │
│                                                                 │
│  ┌─ card (max ~520px wide) ──────────────────────────────────┐  │
│  │  How are you modeling your household?    ← 15pt            │  │
│  │  ( ● Together (joint)   ○ Separately )                    │  │
│  │                                                            │  │
│  │  Where do you live?                                        │  │
│  │  [ CA ▾ ]  ← narrow ~96px                                  │  │
│  │  Tax tables in use: 2024 federal + CA state                │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  ← Back (disabled)  Skip for now              [ Continue → ]    │
├─────────────────────────────────────────────────────────────────┤
│  Building your retirement picture              Run plan ▶ (off) │
│  Start — Household setup                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Design tokens (match `gui/design_system/tokens.py`)

| Token | Value |
|-------|-------|
| bg | `#14161a` |
| surface | `#1e2128` |
| surface-raised | `#282c34` |
| text-primary | `#f4f5f7` |
| text-secondary | `#9aa3b2` |
| text-muted | `#6b7280` |
| accent | `#5ec8e8` |

**Typography:** step title 26pt · questions 15pt · caption 11pt

---

## Feedback checklist

- [ ] Title vs question size contrast
- [ ] Card width / empty space on right
- [ ] Button alignment (Back/Skip left, Continue right)
- [ ] Accent color brightness
- [ ] Overall premium feel

---

## HTML source (save as `design/mockups/screen-01-guided-setup.html`)

See agent-created file when plan mode is off, or request "create the HTML mockup file".