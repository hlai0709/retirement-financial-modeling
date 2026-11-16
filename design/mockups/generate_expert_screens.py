#!/usr/bin/env python3
"""Generate individual expert-mode liquid-glass HTML files."""

from __future__ import annotations

import json
import re
from pathlib import Path

DIR = Path(__file__).parent
DATA_JS = DIR / "expert-screens-data.js"
EXTRA_CSS = """
    .select-wrap::after {
      content: "";
      position: absolute; right: 14px; top: 50%;
      transform: translateY(-50%);
      border-left: 4px solid transparent;
      border-right: 4px solid transparent;
      border-top: 5px solid rgba(255,255,255,0.5);
      pointer-events: none;
    }
    .glass-input.year { width: 88px; }
"""


def load_screens() -> list[dict]:
    text = DATA_JS.read_text(encoding="utf-8")
    match = re.search(r"const EXPERT_SCREENS = (\[[\s\S]*\]);", text)
    if not match:
        raise SystemExit("Could not parse EXPERT_SCREENS")
    raw = match.group(1)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    raw = re.sub(r"^\s*(\w+)\s*:", r'"\1":', raw, flags=re.MULTILINE)
    raw = re.sub(r"([{\[,])\s*(\w+)\s*:", r'\1 "\2":', raw)
    return json.loads(raw)


def field_control(f: dict) -> str:
    t = f["type"]
    if t == "radio":
        return (
            '<div class="radio-row">'
            + "".join(
                f'<div class="radio-pill{" on" if i == f.get("on", 0) else ""}">{o}</div>'
                for i, o in enumerate(f["options"])
            )
            + "</div>"
        )
    if t == "select":
        opts = "".join(
            f'<option{" selected" if o == f.get("value") else ""}>{o}</option>'
            for o in f["options"]
        )
        return f'<div class="select-wrap"><select class="state-select">{opts}</select></div>'
    if t == "narrow":
        return f'<input class="glass-input narrow" type="text" value="{f["value"]}" readonly />'
    if t == "year":
        return f'<input class="glass-input narrow year" type="text" value="{f["value"]}" readonly />'
    if t == "medium":
        return f'<input class="glass-input medium" type="text" value="{f["value"]}" readonly />'
    if t == "dollar":
        return (
            f'<div class="input-dollar"><span class="prefix">$</span>'
            f'<input type="text" value="{f["value"]}" readonly /></div>'
        )
    return ""


def groups_html(screen: dict) -> str:
    parts = []
    for g in screen.get("groups", []):
        fields = "".join(
            f'<div class="field-row"><span class="field-label">{f["label"]}</span>{field_control(f)}</div>'
            for f in g["fields"]
        )
        cap = f'<p class="caption">{g["caption"]}</p>' if g.get("caption") else ""
        open_attr = " open" if g.get("open") else ""
        parts.append(
            f'<details class="section-group liquid"{open_attr}>'
            f'<summary>{g["title"]}</summary><div class="group-fields">{fields}{cap}</div></details>'
        )
    if screen.get("moreNote"):
        parts.append(f'<p class="more-fields">{screen["moreNote"]}</p>')
    return "".join(parts)


def content_html(screen: dict) -> str:
    layout = screen.get("layout")
    if layout == "llm":
        checks = "".join(
            f'<label class="check-row"><input type="checkbox{" checked" if i in screen.get("checksOn", []) else ""} readonly />{c}</label>'
            for i, c in enumerate(screen["checks"])
        )
        return f"""<div class="content-glass liquid liquid-strong" style="max-width:none">
          <div class="glass-inner llm-panel">
            <p class="caption" style="margin-top:0;margin-bottom:14px">{screen["llmStatus"]}</p>
            <p class="field-label" style="margin-bottom:8px">Additional context (optional)</p>
            <textarea class="glass-input" readonly>Focus on conservative estimates for healthcare and Social Security.</textarea>
            <div class="expert-toolbar" style="margin-top:16px;margin-bottom:10px">
              <span class="field-label" style="font-weight:600;color:var(--text)">Select fields to research</span>
              <div class="radio-row">
                <div class="radio-pill">All</div><div class="radio-pill">None</div><div class="radio-pill on">Empty only</div>
              </div>
            </div>
            <div class="llm-checklist">{checks}</div>
            <div style="margin-top:16px;display:flex;justify-content:center">
              <button type="button" class="connect-btn">Run batch research</button>
            </div>
          </div>
        </div>"""
    if layout == "live_data":
        cards = "".join(
            f'<div class="data-card"><h3>{c["title"]}</h3><p>{c["desc"]}</p>'
            + "".join(f'<button type="button" class="connect-btn">{a}</button>' for a in c["actions"])
            + "</div>"
            for c in screen["cards"]
        )
        return f'<div class="cards-grid">{cards}</div>'
    if layout == "simulate":
        sim = "".join(
            f'<div class="radio-pill{" on" if i == screen.get("simModeOn", 0) else ""}">{o}</div>'
            for i, o in enumerate(["Deterministic", "Monte Carlo", "Both"])
        )
        prec = "".join(
            f'<div class="radio-pill{" on" if i == screen.get("precisionOn", 0) else ""}">{o}</div>'
            for i, o in enumerate(["Standard", "CPA Precision"])
        )
        grid = "".join(
            f'<span class="field-label">{f["label"]}</span>{field_control(f)}' for f in screen["fields"]
        )
        actions = "".join(f'<button type="button" class="connect-btn">{a}</button>' for a in screen["actions"])
        return f"""<div class="content-glass liquid liquid-strong" style="max-width:none">
          <div class="glass-inner">
            <p class="field-label" style="margin-bottom:10px">Simulation mode</p>
            <div class="radio-row" style="margin-bottom:18px">{sim}</div>
            <p class="field-label" style="margin-bottom:10px">Planning precision</p>
            <div class="radio-row" style="margin-bottom:18px">{prec}</div>
            <div class="sim-grid">{grid}</div>
            <div style="margin-top:18px">{actions}</div>
          </div>
        </div>"""
    if layout == "settings":
        items = []
        for it in screen["items"]:
            if it["type"] == "button":
                items.append(f'<button type="button" class="connect-btn" style="width:100%;text-align:left">{it["label"]}</button>')
            else:
                items.append(f'<p class="caption" style="margin:0;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.06)">{it["label"]}</p>')
        return f'<div class="settings-list">{"".join(items)}</div>'

    toolbar = ""
    if screen.get("showPersonFilter") or screen.get("showSearch"):
        persons = "".join(
            f'<div class="radio-pill{" on" if i == screen.get("personOn", 0) else ""}">{p}</div>'
            for i, p in enumerate(["Shared", "Self", "Wife"])
        )
        search = (
            '<input class="glass-input search" type="text" placeholder="Search fields…" readonly />'
            if screen.get("showSearch")
            else ""
        )
        toolbar = f'<div class="expert-toolbar"><div class="radio-row">{persons}</div>{search}</div>'
    return f"{toolbar}<div class=\"section-groups\">{groups_html(screen)}</div>"


def rail_html(active_id: str) -> str:
    labels = [(s["id"], s["title"]) for s in load_screens()]
    links = "".join(
        f'<button type="button" class="rail-link{" on" if sid == active_id else ""}">{label}</button>'
        for sid, label in labels
    )
    return f'<span class="rail-label">Sections</span>{links}'


def nav_links(screens: list[dict], idx: int) -> str:
    prev_l = next_l = ""
    if idx > 0:
        prev_l = f'<a href="{screens[idx-1]["file"]}">← {screens[idx-1]["title"]}</a>'
    if idx < len(screens) - 1:
        next_l = f'<a href="{screens[idx+1]["file"]}">{screens[idx+1]["title"]} →</a>'
    return f"""Expert · {screens[idx]["title"]} · {prev_l} {next_l}
    <a href="expert-all-screens.html">All sections</a>
    <a href="index.html">Index</a>"""


def render_page(screen: dict, screens: list[dict], idx: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Expert — {screen["title"]} · Liquid Glass</title>
  <link rel="stylesheet" href="liquid-glass.css" />
  <style>{EXTRA_CSS}</style>
</head>
<body class="screen-body">
  <div class="banner">{nav_links(screens, idx)}</div>

  <div class="window expert-window">
    <header class="header liquid">
      <div class="header-left">
        <span class="app-name">Retirement Planner</span>
        <div class="pill-toggle">
          <button type="button">Guided</button>
          <button type="button" class="on">Expert</button>
        </div>
      </div>
      <span class="build">UI Jun 23 · liquid glass</span>
    </header>

    <div class="expert-body">
      <nav class="section-rail liquid" aria-label="Sections">
        {rail_html(screen["id"])}
      </nav>
      <main class="expert-main">
        <div class="expert-main-head">
          <div>
            <h1 class="expert-title">{screen["headline"]}</h1>
            <p class="expert-subtitle">{screen["subtitle"]}</p>
          </div>
          <button type="button" class="link-guided">Jump to Guided →</button>
        </div>
        {content_html(screen)}
      </main>
    </div>

    <div class="status-dock liquid">
      <div class="status-copy">
        <h2>Expert editor</h2>
        <p>{screen["title"]}</p>
      </div>
      <button type="button" class="btn btn-run">Run plan</button>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    screens = load_screens()
    for idx, screen in enumerate(screens):
        path = DIR / screen["file"]
        path.write_text(render_page(screen, screens, idx), encoding="utf-8")
        print(f"Wrote {path.name}")
    print(f"Done — {len(screens)} expert sections.")


if __name__ == "__main__":
    main()