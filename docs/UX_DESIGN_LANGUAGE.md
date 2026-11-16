# Design Language — Liquid Glass (locked)

**Status:** Approved 2026-06-23  
**Reference mockup:** [`design/mockups/screen-01-variant-c-liquid-glass.html`](../design/mockups/screen-01-variant-c-liquid-glass.html)  
**Inspiration:** Apple Liquid Glass (WWDC 2025 — iOS 26, macOS Tahoe 26)

---

## Principles

| Principle | Application |
|-----------|-------------|
| **Content first** | Questions and data are the hero; chrome floats above and recedes |
| **Refracted layers** | Header, card, footer dock are separate translucent glass panels |
| **Surroundings inform color** | Subtle ambient canvas behind glass (muted in prod — navy/teal, not playful) |
| **Specular vitality** | Top edge highlight + inset glow on glass controls |
| **Concentric radius** | ~22px on floating chrome; ~22px capsule controls |
| **Grouped functional layer** | Nav buttons + progress live in one footer glass dock |

---

## Typography (Apple system)

- **Font:** SF Pro Text / Display (`-apple-system`)
- **Step title:** 36px semibold, title case, +16px left nudge
- **Questions:** 18px medium, numbered with accent `1.` `2.`
- **Caption:** 12px muted (e.g. tax tables line), 28px below control

---

## Color (dark Liquid Glass)

| Token | Value |
|-------|-------|
| Canvas | Deep navy mesh + soft purple/teal/orange blooms (subtle) |
| Glass fill | `rgba(255,255,255,0.07–0.11)` |
| Glass blur | `blur(48–56px) saturate(1.75–1.9)` |
| Glass border | `rgba(255,255,255,0.16–0.18)` |
| Specular | `inset 0 1px 0 rgba(255,255,255,0.28)` + top gradient hairline |
| Text primary | `rgba(255,255,255,0.95)` |
| Text secondary | `rgba(255,255,255,0.62)` |
| Text muted | `rgba(255,255,255,0.42)` |
| Accent | `#5ac8fa` (question numbers, progress dot) |
| Primary button | Near-white pill `#f5f5f7` / dark text |
| Disabled button | Muted gray fill, no shadow |

---

## Layout (Screen 1 pattern — all guided steps)

1. **Floating glass header** — app name, Guided/Expert toggle, build label
2. **Main** — step title + Help (right)
3. **Content glass card** — numbered questions, pill radios, glass select
4. **Floating glass dock** — Back / Skip / Continue + progress strip inside same panel
5. **Help** — slide-out glass panel, all fields on step
6. **No Run plan** until review (last) step

---

## Controls

- **Radio / select:** Glass capsules, selected = brighter glass + stronger specular
- **Buttons:** Same pill shape; primary = white; secondary = glass; disabled = grayed
- **Help:** One button per page, not per field

---

## Workflow

1. Mock each screen in HTML (`design/mockups/`) using this language
2. User approves mockup
3. Implement in Tkinter (`gui/design_system/`) — approximate blur/material where possible
4. Regression: `python scripts/regression_test.py --only "GUI,Phase 5"`

---

## Next screens to mock

1. ~~Household Setup~~ — [`screen-01-variant-c-liquid-glass.html`](../design/mockups/screen-01-variant-c-liquid-glass.html)
2. ~~Timeline~~ — [`screen-02-timeline-liquid-glass.html`](../design/mockups/screen-02-timeline-liquid-glass.html)
3. ~~Your Income &amp; Savings~~ — [`screen-03-income-liquid-glass.html`](../design/mockups/screen-03-income-liquid-glass.html)
4. Review (+ Run plan)
4. Review (+ Run plan)
5. Results window