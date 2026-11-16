#!/usr/bin/env python3
"""Generate individual liquid-glass screen HTML files from guided-screens-data.js."""

from __future__ import annotations

import json
import re
from pathlib import Path

DIR = Path(__file__).parent
DATA_JS = DIR / "guided-screens-data.js"
TOTAL = 16

EXTRA_CSS = """
    .select-wrap::after {
      content: "";
      position: absolute; right: 16px; top: 50%;
      transform: translateY(-50%);
      border-left: 4px solid transparent;
      border-right: 4px solid transparent;
      border-top: 5px solid rgba(255,255,255,0.5);
      pointer-events: none;
    }
    .glass-input.year { width: 108px; }
    .caption.tight { margin-top: 10px; }
    .caption.loose { margin-top: 28px; }
    .dots i { width: 14px; }
    .ai-card {
      position: relative;
      text-align: left;
      padding: 24px 28px 28px 84px;
    }
    .ai-card p { font-size: 14px; color: var(--secondary); margin-bottom: 20px; line-height: 1.55; }
    .ai-actions {
      display: flex;
      justify-content: center;
      margin: 4px calc(-28px) 0 calc(-56px);
    }
    .ai-icon {
      position: absolute;
      top: 22px;
      left: 22px;
      width: 48px; height: 48px;
      margin: 0;
      border-radius: 14px;
      background: linear-gradient(135deg, rgba(90,200,250,0.35), rgba(175,82,222,0.25));
      border: 1px solid rgba(255,255,255,0.2);
      display: flex; align-items: center; justify-content: center;
      font-size: 22px;
    }
    .btn-skip-prominent {
      background: rgba(255,255,255,0.16) !important;
      border-color: rgba(255,255,255,0.28) !important;
    }
"""


def load_screens() -> list[dict]:
    text = DATA_JS.read_text(encoding="utf-8")
    match = re.search(r"const GUIDED_SCREENS = (\[[\s\S]*\]);", text)
    if not match:
        raise SystemExit("Could not parse GUIDED_SCREENS from JS")
    raw = match.group(1)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    raw = re.sub(r"^\s*(\w+)\s*:", r'"\1":', raw, flags=re.MULTILINE)
    raw = re.sub(r"([{\[,])\s*(\w+)\s*:", r'\1 "\2":', raw)
    return json.loads(raw)


def dots(n: int) -> str:
    parts = []
    for i in range(TOTAL):
        cls = ' class="on"' if i < n else ""
        parts.append(f"<i{cls}></i>")
    return "".join(parts)


def field_html(f: dict, idx: int) -> str:
    if f.get("type") == "more":
        return f'<p class="more-fields">{f["text"]}</p>'
    n = idx + 1
    label = f'<p class="q-label"><span>{n}.</span>{f["label"]}</p>'
    t = f["type"]
    control = ""
    if t == "radio":
        opts = "".join(
            f'<div class="radio-pill{" on" if i == f.get("on", 0) else ""}">{o}</div>'
            for i, o in enumerate(f["options"])
        )
        control = f'<div class="radio-row">{opts}</div>'
    elif t == "select":
        opts = "".join(
            f'<option{" selected" if o == f.get("value") else ""}>{o}</option>'
            for o in f["options"]
        )
        control = f'<div class="select-wrap"><select class="state-select">{opts}</select></div>'
    elif t == "narrow":
        control = f'<input class="glass-input narrow" type="text" value="{f["value"]}" readonly />'
    elif t == "year":
        control = f'<input class="glass-input narrow year" type="text" value="{f["value"]}" readonly />'
    elif t == "medium":
        control = f'<input class="glass-input medium" type="text" value="{f["value"]}" readonly />'
    elif t == "dollar":
        control = (
            f'<div class="input-dollar"><span class="prefix">$</span>'
            f'<input type="text" value="{f["value"]}" readonly /></div>'
        )
    cap = ""
    if f.get("caption"):
        tight = " tight" if f.get("captionTight") else " loose"
        cap = f'<p class="caption{tight}">{f["caption"]}</p>'
    return f'<div class="glass-inner">{label}{control}{cap}</div>'


def content_html(screen: dict) -> str:
    layout = screen.get("layout")
    if layout == "connect":
        return f"""<div class="content-glass liquid liquid-strong wide">
          <div class="glass-inner connect-card">
            <p>{screen["connectText"]}</p>
            <button type="button" class="connect-btn">Connect with Plaid</button>
            <button type="button" class="connect-btn">Import from file</button>
          </div>
        </div>"""
    if layout == "ai":
        return f"""<div class="content-glass liquid liquid-strong wide">
          <div class="glass-inner ai-card">
            <div class="ai-icon">✦</div>
            <p>{screen["aiText"]}</p>
            <div class="ai-actions"><button type="button" class="connect-btn">Run AI estimates</button></div>
          </div>
        </div>"""
    if layout == "review":
        cards = "".join(
            f'<div class="summary-card"><h3>{c["h"]}</h3><p>{c["p"]}</p></div>'
            for c in screen["summary"]
        )
        sim = "".join(
            f'<div class="radio-pill{" on" if i == screen.get("simOn", 0) else ""}">{o}</div>'
            for i, o in enumerate(screen["simOptions"])
        )
        return f"""<div class="content-glass liquid liquid-strong wide">
          <div class="glass-inner">
            <p class="q-label"><span>1.</span>Your plan at a glance</p>
            <div class="summary-grid">{cards}</div>
          </div>
          <div class="glass-inner">
            <p class="q-label"><span>2.</span>How should we simulate your plan?</p>
            <div class="radio-row">{sim}</div>
          </div>
        </div>"""
    wide = " wide" if screen.get("wide") else ""
    fields = "".join(field_html(f, i) for i, f in enumerate(screen.get("fields", [])))
    return f'<div class="content-glass liquid liquid-strong{wide}">{fields}</div>'


def footer_html(screen: dict) -> str:
    f = screen.get("footer") or {}
    back_cls = " disabled" if f.get("backDisabled") else ""
    back_dis = " disabled" if f.get("backDisabled") else ""
    skip_cls = " btn-skip-prominent" if f.get("skipProminent") else ""
    skip_btn = "" if f.get("showRun") else f'<button type="button" class="btn btn-secondary{skip_cls}">Skip for now</button>'
    right = (
        '<button type="button" class="btn btn-run">Run plan</button>'
        if f.get("showRun")
        else '<button type="button" class="btn btn-primary">Continue →</button>'
    )
    return f"""<div class="chrome-dock liquid">
      <div class="step-footer">
        <div class="footer-left">
          <button type="button" class="btn btn-secondary{back_cls}"{back_dis}>← Back</button>
          {skip_btn}
        </div>
        {right}
      </div>
      <div class="progress-strip">
        <span>Step {screen["num"]} of {TOTAL}</span>
        <div class="dots">{dots(screen["num"])}</div>
        <span>{screen["journey"]}</span>
      </div>
    </div>"""


def help_html(screen: dict) -> str:
    items = "".join(
        f'<div class="help-item"><h3>{h["h"]}</h3><p>{h["p"]}</p></div>'
        for h in screen.get("help", [])
    )
    return f"""<div class="help-backdrop" id="backdrop"></div>
    <aside class="help-panel liquid liquid-strong" id="panel">
      <div class="help-head"><span>{screen["helpTitle"]}</span><button type="button" class="btn-x" id="help-close">✕</button></div>
      <div class="help-body">{items}</div>
    </aside>"""


def nav_links(screens: list[dict], idx: int) -> str:
    prev_l = ""
    next_l = ""
    if idx > 0:
        prev_s = screens[idx - 1]
        prev_l = f'<a href="{prev_s["file"]}">← Screen {prev_s["num"]}</a>'
    if idx < len(screens) - 1:
        next_s = screens[idx + 1]
        next_l = f'<a href="{next_s["file"]}">Screen {next_s["num"]} →</a>'
    return f"""Screen {screens[idx]["num"]} — {screens[idx]["title"]} ·
    {prev_l}
    {next_l}
    <a href="guided-all-screens.html">All screens</a>
    <a href="index.html">Index</a>"""


def render_screen(screen: dict, screens: list[dict], idx: int) -> str:
    opt = ' <span style="font-size:18px;color:var(--muted);font-weight:400">(optional)</span>' if screen.get("optional") else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Screen {screen["num"]} — {screen["title"]} · Liquid Glass</title>
  <link rel="stylesheet" href="liquid-glass.css" />
  <style>{EXTRA_CSS}</style>
</head>
<body class="screen-body">
  <div class="banner">{nav_links(screens, idx)}</div>

  <div class="window">
    <header class="header liquid">
      <div class="header-left">
        <span class="app-name">Retirement Planner</span>
        <div class="pill-toggle">
          <button type="button" class="on">Guided</button>
          <button type="button">Expert</button>
        </div>
      </div>
      <span class="build">UI Jun 23 · liquid glass</span>
    </header>

    <main class="main">
      <div class="step-row">
        <h1 class="step-title">{screen["title"]}{opt}</h1>
        <button type="button" class="btn-help" id="help-open">Help</button>
      </div>
      {content_html(screen)}
    </main>

    {footer_html(screen)}
    {help_html(screen)}
  </div>

  <script>
    const p = document.getElementById('panel'), b = document.getElementById('backdrop');
    document.getElementById('help-open').onclick = () => {{ p.classList.add('open'); b.classList.add('open'); }};
    document.getElementById('help-close').onclick = () => {{ p.classList.remove('open'); b.classList.remove('open'); }};
    b.onclick = () => {{ p.classList.remove('open'); b.classList.remove('open'); }};
  </script>
</body>
</html>
"""


def main() -> None:
    screens = load_screens()
    for idx, screen in enumerate(screens):
        out = DIR / screen["file"]
        out.write_text(render_screen(screen, screens, idx), encoding="utf-8")
        print(f"Wrote {out.name}")
    print(f"Done — {len(screens)} screens.")


if __name__ == "__main__":
    main()