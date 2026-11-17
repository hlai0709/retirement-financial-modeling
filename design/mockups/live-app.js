/**
 * Liquid Glass web shell — renders PlanningSession screens from Python.
 */
(function () {
  let current = 0;
  let totalSteps = 16;
  let lazyCurrent = 0;
  let lazyTotalSteps = 10;
  let currentScreen = null;
  let appState = {};
  let expertSection = "assumptions";
  let lastResults = null;
  let llmSelection = new Set();
  let expertSearchQuery = "";

  const appRoot = () => document.getElementById("app");

  function showBoot(message, { error = false } = {}) {
    const el = appRoot();
    if (!el) return;
    const div = document.createElement("div");
    div.className = error ? "boot-error" : "boot-loading";
    div.textContent = message;
    el.innerHTML = "";
    el.appendChild(div);
  }

  const api = () => {
    const bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge.get_state !== "function") return null;
    return bridge;
  };

  if (appRoot() && !appRoot().querySelector(".window")) {
    showBoot("Loading your plan…");
  }
  window.addEventListener("error", (e) => {
    console.error("live-app error", e.error || e.message);
    showBoot(`Startup error: ${e.message || "Unknown JavaScript error"}`, { error: true });
  });
  window.addEventListener("unhandledrejection", (e) => {
    console.error("live-app unhandled rejection", e.reason);
    const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason || "Unknown error");
    showBoot(`Startup error: ${msg}`, { error: true });
  });

  async function refreshState() {
    if (!api()) return;
    try {
      appState = await api().get_state();
      current = appState.step_index ?? current;
      totalSteps = appState.total_steps ?? totalSteps;
      lazyCurrent = appState.lazy_step_index ?? lazyCurrent;
      lazyTotalSteps = appState.lazy_total_steps ?? lazyTotalSteps;
      expertSection = appState.expert_section || expertSection;
      updateCompletenessChrome();
    } catch (e) {
      console.warn("get_state failed", e);
    }
  }

  function completenessRingMetrics(percent) {
    const p = Math.max(0, Math.min(100, Number(percent) || 0));
    const r = 15.5;
    const arc = p;
    const gap = Math.max(0, 100 - p);
    return { p, r, arc, gap };
  }

  function updateCompletenessChrome() {
    const { p, arc, gap } = completenessRingMetrics(appState.completeness?.percent);
    document.querySelectorAll("[data-completeness-open]").forEach((btn) => {
      const pct = btn.querySelector(".completeness-pct");
      if (pct) pct.textContent = `${p}%`;
      const fg = btn.querySelector(".ring-fg");
      if (fg) {
        fg.setAttribute("stroke-dasharray", `${arc} ${gap}`);
        fg.setAttribute("stroke-dashoffset", "0");
      }
    });
  }

  async function loadScreen(index) {
    if (!api()) return null;
    try {
      return await api().get_guided_screen(index);
    } catch (e) {
      console.warn("get_guided_screen failed", e);
      return null;
    }
  }

  async function loadExpertScreen(section) {
    if (!api()) return null;
    try {
      return await api().get_expert_screen(section);
    } catch (e) {
      console.warn("get_expert_screen failed", e);
      return null;
    }
  }

  function waitForApi() {
    return new Promise((resolve) => {
      if (api()) return resolve(true);
      let settled = false;
      let poll = null;
      const done = (ok) => {
        if (settled) return;
        settled = true;
        if (poll) clearInterval(poll);
        resolve(!!ok);
      };
      window.addEventListener("pywebviewready", () => done(api()), { once: true });
      poll = setInterval(() => {
        if (api()) done(true);
      }, 50);
      setTimeout(() => done(api()), 12000);
    });
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function dotsHtml(n) {
    return Array.from({ length: totalSteps }, (_, i) =>
      `<i class="${i < n ? "on" : ""}"></i>`
    ).join("");
  }

  function simulationModeLabel(mode) {
    const m = String(mode || "").toLowerCase();
    if (m === "both") return "Deterministic + Monte Carlo";
    if (m === "monte_carlo") return "Monte Carlo";
    if (m === "deterministic") return "Deterministic";
    return m.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function dataSourcesHint() {
    const ds = appState.data_sources;
    if (!ds?.summary) return "";
    const title = (ds.labels || []).length ? (ds.labels || []).join(", ") : ds.summary;
    return `<span class="data-sources-hint" title="${esc(title)}">${esc(ds.summary)}</span>`;
  }

  function savedLabel(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      return `Saved ${d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
    } catch {
      return "";
    }
  }

  async function savePlanNow() {
    if (!api()) return;
    await persistDraft();
    const res = await api().save_plan();
    await showAlert(res.message || "Plan saved.");
    await refreshState();
    await renderCurrent();
  }

  async function startNewPlan() {
    if (!api()) return;
    const ok = await showConfirm(
      "Load a blank plan template (zeros) for this session? Your last saved plan stays on disk — use Restart to reload it.",
      "Start new plan"
    );
    if (!ok) return;
    const res = await api().start_new_plan();
    lastResults = null;
    expertSearchQuery = "";
    current = res.step_index ?? 0;
    await refreshState();
    await renderCurrent();
    await showAlert(res.message || "Started a new plan.");
  }

  async function restartApp() {
    if (!api()) {
      location.reload();
      return;
    }
    const ok = await showConfirm(
      "Restart the app now? Your plan is saved first. Use this after code or Lazy config changes.",
      "Restart app"
    );
    if (!ok) return;
    try {
      await api().restart_app();
    } catch {
      /* window may close before the promise settles */
    }
  }

  async function showPdfRedactorModal() {
    if (!api()) {
      await showAlert("PDF redactor needs the app backend (run with python main.py --web).");
      return;
    }
    let caps = {};
    try {
      caps = await api().pdf_redactor_capabilities();
    } catch (e) {
      caps = { ready: false, notes: [String(e)] };
    }
    const aiOk = caps.ai_available;
    const ready = caps.ready;
    const notes = (caps.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    const body = `<div style="text-align:left;line-height:1.5">
      <p style="color:var(--secondary);margin:0 0 10px">
        Built for <strong>bank statements, W-2s, and 1040s</strong>.
        <strong>Redaction does not use the internet</strong> — your PDF is never uploaded.
        AI mode talks only to <strong>Ollama on this Mac (127.0.0.1)</strong> with
        <strong>${esc(caps.model || "qwen3:14b")}</strong>. If the Mac feels slow, that is local CPU/RAM load, not Wi‑Fi.
      </p>
      <p style="color:var(--muted);font-size:12px;margin:0 0 12px">
        <strong>Removes:</strong> names, addresses (you + employer), phones, SSN/ITIN/EIN (incl. partial), account &amp; routing IDs.<br/>
        <strong>Keeps:</strong> wages, taxes, balances, and other dollar amounts so the file is still useful for planning.<br/>
        <strong>Tip:</strong> use <strong>Fast</strong> for a lighter run; use <strong>AI</strong> only when you need better name/address detection.
      </p>
      ${!ready ? `<p style="color:#f99;margin:0 0 12px">Not ready: ${esc((caps.notes || []).join(" "))}</p>` : ""}
      ${notes && ready && !aiOk ? `<ul style="color:var(--muted);font-size:12px;margin:0 0 12px;padding-left:18px">${notes}</ul>` : ""}
      <p class="field-label" style="margin:12px 0 8px">Mode</p>
      <label style="display:flex;align-items:flex-start;gap:8px;margin:8px 0;cursor:pointer;color:var(--secondary)">
        <input type="radio" name="redact-mode" data-redact-mode value="fast" checked style="margin-top:3px" />
        <span><strong>Fast</strong> — recommended for large 1040s. Local rules only (no AI). Usually much quicker.</span>
      </label>
      <label style="display:flex;align-items:flex-start;gap:8px;margin:8px 0;cursor:pointer;color:var(--secondary);${!aiOk ? "opacity:0.55" : ""}">
        <input type="radio" name="redact-mode" data-redact-mode value="ai" style="margin-top:3px" ${aiOk ? "" : "disabled"} />
        <span><strong>AI</strong> — one local Ollama pass on identity pages (not every page), then rules.
          Better for messy names; still slower than Fast.
          ${!aiOk ? " — unavailable until Ollama is running." : ""}</span>
      </label>
      <p class="field-label" style="margin:12px 0 6px">Optional extra rules (AI mode)</p>
      <input type="text" class="glass-input" data-redact-rules placeholder="e.g. Also redact my CPA firm name" style="width:100%" ${ready ? "" : "disabled"} />
      <p style="font-size:12px;color:var(--muted);margin:12px 0 0">
        Progress shows page status and time remaining. Always review before sharing.
      </p>
    </div>`;
    showModal("Redact financial PDF", body, [
      { label: "Cancel" },
      {
        label: "Choose PDF…",
        primary: true,
        onClick: async () => {
          if (!ready) {
            await showAlert("Install pymupdf first: pip install pymupdf");
            return;
          }
          // Read mode BEFORE any await (and while modal fields still exist).
          // Pass use_ai as 0/1 — pywebview often drops JS boolean false, which
          // previously made Python default to fast mode even when AI was selected.
          const modeEl = document.querySelector('input[name="redact-mode"]:checked');
          const mode = modeEl?.value || "fast";
          const useAi = mode === "ai" ? 1 : 0;
          const rules = document.querySelector("[data-redact-rules]")?.value || "";
          try {
            const res = await runPdfRedactionWithProgress(useAi, rules);
            if (res.cancelled) return;
            await showAlert(
              res.message || (res.ok ? "Done." : "Failed."),
              res.ok ? "Redaction complete" : "Redaction failed"
            );
          } catch (e) {
            await showAlert(String(e.message || e), "Redaction failed");
          } finally {
            hideProgress();
          }
        },
      },
    ]);
  }

  function stepIndexForId(stepId) {
    if (!stepId) return null;
    for (const block of appState.journey_rail || []) {
      for (const step of block.steps || []) {
        if (step.step_id === stepId) return step.index;
      }
    }
    return null;
  }

  function completenessRingHtml(percent) {
    const { p, r, arc, gap } = completenessRingMetrics(percent);
    return `<button type="button" class="completeness-btn" data-completeness-open title="What's missing?">
      <svg viewBox="0 0 36 36" width="28" height="28" aria-hidden="true">
        <circle class="ring-bg" cx="18" cy="18" r="${r}" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="3"/>
        <circle class="ring-fg" cx="18" cy="18" r="${r}" fill="none" stroke="#5ac8fa" stroke-width="3" stroke-linecap="round"
          pathLength="100" stroke-dasharray="${arc} ${gap}" stroke-dashoffset="0" transform="rotate(-90 18 18)"/>
      </svg>
      <span class="completeness-pct">${p}%</span>
    </button>`;
  }

  function journeyRailHtml(rail) {
    if (!rail || !rail.length) return "";
    return rail
      .map(
        (block) => `<div class="journey-block">
        <span class="journey-heading">${esc(block.journey)}</span>
        ${(block.steps || [])
          .map((step) => {
            const cls = [
              "journey-step",
              step.current ? "current" : "",
              step.completed ? "done" : "",
              step.skipped ? "skipped" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return `<button type="button" class="${cls}" data-journey-step="${step.index}" title="${esc(step.label)}">${esc(step.label)}</button>`;
          })
          .join("")}
      </div>`
      )
      .join("");
  }

  function completenessGroupsHtml(groups, { clickable = false } = {}) {
    return (groups || [])
      .map((group) => {
        const pct = group.percent ?? 0;
        const filled = group.filled ?? 0;
        const total = group.total ?? 0;
        const open = group.missing_count ?? Math.max(0, total - filled);
        const done = pct >= 100 || open === 0;
        const jump = group.jump_step_index;
        const inner = `<div class="completeness-group-head">
            <span class="completeness-group-label">${esc(group.label)}</span>
            <span class="completeness-group-pct">${pct}%</span>
          </div>
          <div class="completeness-group-bar"><span style="width:${pct}%"></span></div>
          <span class="completeness-group-meta">${
            done
              ? `${filled} of ${total} key field${total === 1 ? "" : "s"} complete`
              : `${open} field${open === 1 ? "" : "s"} still needed · ${filled} of ${total} done`
          }</span>`;
        if (!done && clickable && jump != null) {
          return `<button type="button" class="completeness-group incomplete" data-missing-step="${jump}" title="Go to ${esc(group.label)}">
            ${inner}<span class="completeness-group-action">Go to section →</span>
          </button>`;
        }
        return `<div class="completeness-group${done ? " done" : ""}">${inner}</div>`;
      })
      .join("");
  }

  function missingRowsHtml(missing) {
    return (missing || [])
      .map(
        (row) => `<li class="missing-row">
        <button type="button" class="missing-jump" data-missing-step="${row.step_index}">${esc(row.label)}</button>
        <span class="missing-group">${esc(row.group)}</span>
      </li>`
      )
      .join("");
  }

  function invalidRowsHtml(invalid) {
    return (invalid || [])
      .map(
        (row) => `<li class="missing-row invalid-row">
        <button type="button" class="missing-jump" data-missing-step="${row.step_index}">${esc(row.label)}</button>
        <span class="invalid-message">${esc(row.message)}</span>
        <span class="missing-group">${esc(row.group)}</span>
      </li>`
      )
      .join("");
  }

  function bindMissingJumpHandlers() {
    document.querySelectorAll("[data-missing-step]").forEach((btn) => {
      btn.onclick = async () => {
        closeModal();
        await goStep(Number(btn.dataset.missingStep));
      };
    });
  }

  function showCompletenessModal(comp, { title = "Plan completeness", intro = "" } = {}) {
    const missing = comp.missing || [];
    const invalid = comp.invalid || [];
    const groups = completenessGroupsHtml(comp.groups, { clickable: missing.length > 0 || invalid.length > 0 });
    const invalidBlock = invalid.length
      ? `<p class="missing-subhead">Invalid values</p>
        <ul class="missing-list">${invalidRowsHtml(invalid)}</ul>`
      : "";
    const missingBlock = missing.length
      ? `<p class="missing-subhead">Open fields</p>
        <ul class="missing-list">${missingRowsHtml(missing)}</ul>`
      : "";
    const body = missing.length || invalid.length
      ? `${intro ? `<p class="missing-intro">${intro}</p>` : ""}
        <div class="completeness-groups">${groups}</div>
        ${invalidBlock}
        ${missingBlock}`
      : `<p class="missing-intro">${intro || "Your high-impact planning sections look complete."}</p>
        <div class="completeness-groups">${groups}</div>`;
    showModal(title, body, [{ label: "Close" }]);
    bindMissingJumpHandlers();
  }

  async function showMissingModal() {
    if (api()) await persistDraft();
    await refreshState();
    const comp = appState.completeness || {};
    const missing = comp.missing || [];
    if (!missing.length) {
      showCompletenessModal(comp);
      return;
    }
    const pct = comp.percent ?? 0;
    showCompletenessModal(comp, {
      intro: `${pct}% complete — ${missing.length} key field${missing.length === 1 ? "" : "s"} still need your input. Zeros and blanks count once you've entered them or finished that step. Tap a section or field to jump there.`,
    });
  }

  function showRunPlanBlockedModal(comp) {
    const missing = comp?.missing_only || comp?.missing || [];
    const invalid = comp?.invalid || [];
    const pct = comp?.percent ?? 0;
    const parts = [];
    if (invalid.length) {
      parts.push(`fix ${invalid.length} invalid value${invalid.length === 1 ? "" : "s"}`);
    }
    if (missing.length) {
      parts.push(`fill in ${missing.length} required field${missing.length === 1 ? "" : "s"}`);
    }
    const detail = parts.length ? parts.join(" and ") : "complete your plan";
    showCompletenessModal(comp || {}, {
      title: "Can't run plan yet",
      intro: `${pct}% complete — ${detail} before running. Tap a section or field below to go there.`,
    });
  }

  function guidedStepIndexById(stepId) {
    if (!stepId || !api()) return null;
    // Prefer journey rail if it carries guided step indices
    for (const block of appState.journey_rail || []) {
      for (const step of block.steps || []) {
        if (step.step_id === stepId || step.section_id === stepId) return step.index;
      }
    }
    return null;
  }

  async function jumpToFieldError(res) {
    const stepId = res.step_id || "";
    const fieldKey = res.field_key || "";
    closeModal();
    // Prefer Guided for known setup/tax fields
    if (stepId && api()) {
      try {
        await setMode("guided");
        const jump = await api().jump_to_guided_step(stepId);
        if (jump && jump.ok) {
          await refreshState();
          await renderCurrent();
          // Focus / highlight field after paint
          setTimeout(() => {
            const root = document.getElementById("app");
            const el =
              root?.querySelector(`[data-key="${fieldKey}"]`) ||
              root?.querySelector(`input[data-key="${fieldKey}"], select[data-key="${fieldKey}"]`);
            if (el) {
              el.scrollIntoView({ behavior: "smooth", block: "center" });
              el.focus?.();
              el.classList?.add("field-error-flash");
              setTimeout(() => el.classList?.remove("field-error-flash"), 2500);
            }
          }, 80);
          return;
        }
      } catch (e) {
        console.warn("jump to field failed", e);
      }
    }
    await showAlert(
      (res.message || "Invalid value.") +
        (fieldKey ? `\n\nField: ${res.field_label || fieldKey}` : "") +
        (stepId ? `\nOpen Guided → step “${stepId}”.` : "")
    );
  }

  async function showRunErrorModal(res) {
    const msg = res.message || "Run plan failed.";
    const fieldKey = res.field_key || "";
    const label = res.field_label || fieldKey.replace(/_/g, " ");
    if (fieldKey || res.step_id) {
      const body = `<p style="line-height:1.5;color:var(--secondary)">${esc(msg)}</p>
        <p style="margin-top:12px;font-size:13px;color:var(--muted)">Related field: <strong>${esc(label || fieldKey)}</strong></p>
        <p class="caption" style="margin-top:8px">Leave State Move Year blank if you are not relocating. Use a future year (or this year) if you are.</p>`;
      showModal(res.title || "Can't run plan", body, [
        { label: "Close" },
        {
          label: "Go to field →",
          primary: true,
          onClick: async () => {
            await jumpToFieldError(res);
          },
        },
      ]);
      return;
    }
    await showAlert(msg, res.title || "Notice");
  }

  /* ── Modals ── */
  function ensureChrome() {
    if (document.getElementById("modal-root")) return;
    document.body.insertAdjacentHTML(
      "beforeend",
      `<div id="modal-root" class="modal-backdrop" data-modal-backdrop>
        <div class="modal-panel liquid liquid-strong" data-modal-panel>
          <div class="modal-head"><h2 class="modal-title" data-modal-title>Dialog</h2><button type="button" class="btn-x" data-modal-close>✕</button></div>
          <div class="modal-body" data-modal-body></div>
          <div class="modal-foot" data-modal-foot></div>
        </div>
      </div>
      <div class="progress-overlay" data-progress-overlay>
        <div class="progress-card liquid liquid-strong">
          <div class="spinner" data-progress-spinner></div>
          <p data-progress-label>Working…</p>
          <div class="progress-bar-track" data-progress-track hidden>
            <div class="progress-bar-fill" data-progress-bar style="width:0%"></div>
          </div>
          <p class="progress-meta" data-progress-meta hidden></p>
        </div>
      </div>
      <div class="results-overlay liquid" data-results-overlay>
        <div class="results-head">
          <div class="results-head-copy">
            <h2>Your plan results</h2>
            <p class="results-subtitle" data-results-subtitle></p>
          </div>
          <button type="button" class="btn-x" data-results-close>✕</button>
        </div>
        <div class="results-body" data-results-body></div>
      </div>`
    );
    document.querySelector("[data-modal-close]").onclick = closeModal;
    document.querySelector("[data-modal-backdrop]").onclick = (e) => {
      if (e.target.matches("[data-modal-backdrop]")) closeModal();
    };
  }

  function bindResultsChrome() {
    const closeBtn = document.querySelector("[data-results-close]");
    if (closeBtn) closeBtn.onclick = closeResults;
    if (!window._resultsEscBound) {
      window._resultsEscBound = true;
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          closeResults();
          closeModal();
        }
      });
    }
  }

  function showProgress(label, percent) {
    ensureChrome();
    const labelEl = document.querySelector("[data-progress-label]");
    if (labelEl) labelEl.textContent = label || "Working…";
    updateProgressBar(percent, "");
    document.querySelector("[data-progress-overlay]")?.classList.add("open");
  }

  function updateProgressBar(percent, meta) {
    const track = document.querySelector("[data-progress-track]");
    const bar = document.querySelector("[data-progress-bar]");
    const metaEl = document.querySelector("[data-progress-meta]");
    const spinner = document.querySelector("[data-progress-spinner]");
    if (percent == null || Number.isNaN(Number(percent))) {
      if (track) track.hidden = true;
      if (metaEl) metaEl.hidden = true;
      if (spinner) spinner.style.display = "";
      return;
    }
    const p = Math.max(0, Math.min(100, Math.round(Number(percent))));
    if (track) track.hidden = false;
    if (bar) bar.style.width = `${p}%`;
    if (spinner) spinner.style.display = p >= 100 ? "none" : "";
    if (metaEl) {
      metaEl.hidden = false;
      metaEl.textContent = meta || `${p}%`;
    }
  }

  function setProgress(label, percent, meta) {
    ensureChrome();
    const labelEl = document.querySelector("[data-progress-label]");
    if (labelEl && label) labelEl.textContent = label;
    updateProgressBar(percent, meta);
    document.querySelector("[data-progress-overlay]")?.classList.add("open");
  }

  function hideProgress() {
    const el = document.querySelector("[data-progress-overlay]");
    if (el) el.classList.remove("open");
    updateProgressBar(null, "");
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function formatEtaSeconds(seconds) {
    if (seconds == null || Number.isNaN(Number(seconds))) return "";
    const s = Math.max(0, Math.round(Number(seconds)));
    if (s <= 0) return "almost done";
    if (s < 5) return "a few seconds left";
    if (s < 60) return `~${s}s left`;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    if (m < 60) return sec ? `~${m}m ${String(sec).padStart(2, "0")}s left` : `~${m}m left`;
    const h = Math.floor(m / 60);
    return `~${h}h ${m % 60}m left`;
  }

  /** Client-side ETA fallback if the backend has not estimated yet. */
  function estimateEtaFromElapsed(percent, startedAtMs) {
    if (!startedAtMs || percent == null) return null;
    const pct = Number(percent);
    if (!(pct > 2 && pct < 100)) return pct >= 100 ? 0 : null;
    const elapsed = (Date.now() - startedAtMs) / 1000;
    if (elapsed < 0.5) return null;
    return Math.max(0, elapsed / (pct / 100) - elapsed);
  }

  async function runPdfRedactionWithProgress(useAi, rules) {
    // Integers only — see pdf_redact_start docstring (JS false is unreliable).
    const useAiFlag = useAi ? 1 : 0;
    const start = await api().pdf_redact_start(useAiFlag, rules || "");
    if (start.cancelled) return start;
    if (!start.ok) return start;

    // Trust server-confirmed mode (not the client checkbox alone).
    const runningAi = Boolean(start.used_ai || start.want_ai || start.mode === "ai");
    const startedAtMs = Date.now();
    setProgress(
      runningAi
        ? "Redacting (local AI · no internet)…"
        : "Redacting (local rules · no internet)…",
      1,
      start.input_name
        ? `${start.input_name} · ${runningAi ? "local qwen · CPU busy" : "Fast"} · estimating…`
        : "Estimating…"
    );

    let status = { state: "running", percent: 1 };
    const pollMs = runningAi ? 400 : 200;
    while (status.state === "running") {
      await sleep(pollMs);
      status = await api().pdf_redact_status();
      const pct = status.percent ?? 0;
      let etaLabel = status.eta_label || "";
      if (!etaLabel) {
        const etaSec =
          status.eta_seconds != null
            ? status.eta_seconds
            : estimateEtaFromElapsed(pct, startedAtMs);
        etaLabel = formatEtaSeconds(etaSec);
      }
      const parts = [];
      if (status.pages > 0) parts.push(`Page ${status.page || 0}/${status.pages}`);
      parts.push(`${Math.round(pct)}%`);
      if (etaLabel) parts.push(etaLabel);
      setProgress(status.message || "Working…", pct, parts.join(" · "));
    }

    if (status.state === "error" || status.ok === false) {
      return {
        ok: false,
        message: status.result_message || status.message || "Redaction failed.",
      };
    }

    setProgress("Choose where to save…", 100, "Complete");
    const saved = await api().pdf_redact_save();
    return saved;
  }

  function showModal(title, bodyHtml, buttons) {
    ensureChrome();
    const titleEl = document.querySelector("[data-modal-title]");
    titleEl.textContent = title;
    titleEl.style.color = "rgba(255, 255, 255, 0.95)";
    document.querySelector("[data-modal-body]").innerHTML = bodyHtml;
    const foot = document.querySelector("[data-modal-foot]");
    foot.innerHTML = "";
    (buttons || []).forEach((btn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = btn.primary ? "btn btn-primary" : "btn btn-secondary";
      b.textContent = btn.label;
      // Capture form values before closeModal (body stays in DOM, but be safe).
      b.onclick = () => {
        if (btn.onClick) {
          const result = btn.onClick();
          closeModal();
          return result;
        }
        closeModal();
      };
      foot.appendChild(b);
    });
    document.getElementById("modal-root").classList.add("open");
  }

  function closeModal() {
    const el = document.getElementById("modal-root");
    if (el) el.classList.remove("open");
  }

  function showAlert(message, title) {
    return new Promise((resolve) => {
      showModal(title || "Notice", `<p style="line-height:1.5;color:var(--secondary)">${esc(message)}</p>`, [
        { label: "OK", primary: true, onClick: resolve },
      ]);
    });
  }

  function showConfirm(message, title) {
    return new Promise((resolve) => {
      showModal(title || "Confirm", `<p style="line-height:1.5;color:var(--secondary)">${esc(message)}</p>`, [
        { label: "Cancel", onClick: () => resolve(false) },
        { label: "Continue", primary: true, onClick: () => resolve(true) },
      ]);
    });
  }

  function closeResults() {
    const el = document.querySelector("[data-results-overlay]");
    if (el) el.classList.remove("open");
  }

  function fmtMoney(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `$${Math.round(n).toLocaleString()}`;
  }

  function fmtPct(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${(n * 100).toFixed(1)}%`;
  }

  function resultsTableHtml(rows, columns) {
    if (!rows || !rows.length) return "";
    const head = columns.map((c) => `<th>${esc(c.label)}</th>`).join("");
    const body = rows.map((row) =>
      `<tr>${columns.map((c) => `<td>${esc(c.fmt(row))}</td>`).join("")}</tr>`
    ).join("");
    return `<table class="review-table results-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function downloadCsv(filename, rows) {
    if (!rows || !rows.length) return;
    const cols = Object.keys(rows[0]);
    const escCell = (value) => {
      if (value == null) return "";
      const text = String(value);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [cols.join(",")];
    rows.forEach((row) => lines.push(cols.map((col) => escCell(row[col])).join(",")));
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function fanChartSvg(mcPoints, detPoints, { width = 560, height = 150 } = {}) {
    if (!mcPoints || !mcPoints.length) return "";
    const values = [];
    mcPoints.forEach((p) => values.push(Number(p.p10) || 0, Number(p.p50) || 0, Number(p.p90) || 0));
    if (detPoints) detPoints.forEach((p) => values.push(Number(p.balance) || 0));
    const max = Math.max(...values, 1);
    const n = mcPoints.length;
    const xAt = (i) => (n <= 1 ? 0 : (i / (n - 1)) * width);
    const yAt = (v) => height - ((Number(v) || 0) / max) * (height - 8) - 4;

    const upper = mcPoints.map((p, i) => `${xAt(i)},${yAt(p.p90)}`).join(" ");
    const lower = [...mcPoints].reverse().map((p, i) => {
      const idx = n - 1 - i;
      return `${xAt(idx)},${yAt(p.p10)}`;
    }).join(" ");
    const median = mcPoints.map((p, i) => `${xAt(i)},${yAt(p.p50)}`).join(" ");
    const detLine = detPoints && detPoints.length
      ? detPoints.map((p, i) => `${xAt(Math.min(i, n - 1))},${yAt(p.balance)}`).join(" ")
      : "";

    return `<svg class="fan-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Monte Carlo percentile fan chart">
      <polygon class="fan-band" points="${upper} ${lower}" />
      <polyline class="fan-median" points="${median}" />
      ${detLine ? `<polyline class="fan-det" points="${detLine}" />` : ""}
    </svg>
    <div class="fan-legend">
      <span><i class="fan-swatch band"></i>10th–90th</span>
      <span><i class="fan-swatch median"></i>Median</span>
      ${detLine ? `<span><i class="fan-swatch det"></i>Deterministic</span>` : ""}
    </div>`;
  }

  function resultsActionsHtml(tabKey, det, mc) {
    const buttons = [];
    if (mc?.export_rows?.length) {
      buttons.push(`<button type="button" class="btn btn-secondary" data-export-mc="${esc(tabKey)}">Export Monte Carlo CSV</button>`);
    }
    if (det?.export_rows?.length) {
      buttons.push(`<button type="button" class="btn btn-secondary" data-export-det="${esc(tabKey)}">Export projection CSV</button>`);
    }
    if (!buttons.length) return "";
    return `<div class="results-actions">${buttons.join("")}</div>`;
  }

  function alignDetOverlay(mcPoints, detPoints) {
    if (!detPoints?.length || !mcPoints?.length) return null;
    return mcPoints.map((mc, i) => {
      const byYear = detPoints.find((d) => d.year != null && d.year === mc.year);
      return byYear || detPoints[Math.min(i, detPoints.length - 1)];
    });
  }

  function bindResultsExports(container, tab, tabKey) {
    container.querySelectorAll("[data-export-mc]").forEach((btn) => {
      btn.onclick = () => {
        if (tab.monte_carlo?.export_rows) downloadCsv(`${tabKey}_monte_carlo.csv`, tab.monte_carlo.export_rows);
      };
    });
    container.querySelectorAll("[data-export-det]").forEach((btn) => {
      btn.onclick = () => {
        if (tab.deterministic?.export_rows) downloadCsv(`${tabKey}_deterministic.csv`, tab.deterministic.export_rows);
      };
    });
  }

  function resultsHeadlineHtml(results) {
    const headline = results.headline;
    if (!headline) return "";
    const tone = ["ok", "warn", "neutral"].includes(headline.tone) ? headline.tone : "neutral";
    const lines = (headline.lines || []).map((line) => `<p>${esc(line)}</p>`).join("");
    return `<div class="results-headline tone-${tone}"><h2>${esc(headline.title || "")}</h2>${lines}</div>`;
  }

  function showResults(results) {
    ensureChrome();
    bindResultsChrome();
    lastResults = results;
    const body = document.querySelector("[data-results-body]");
    const subtitle = document.querySelector("[data-results-subtitle]");
    if (subtitle) {
      const parts = [simulationModeLabel(results.mode)];
      if (results.risk_tolerance) parts.push(`${results.risk_tolerance} risk`);
      subtitle.textContent = parts.filter(Boolean).join(" · ");
    }
    const tabs = [];
    if (results.adviser) {
      tabs.push({ key: "adviser", label: "Adviser", adviser: results.adviser });
    }
    (results.tabs || []).forEach((tab) => tabs.push(tab));
    let active = 0;

    function renderTab() {
      const tab = tabs[active];
      if (!tab) {
        body.innerHTML = "<p>No results data.</p>";
        return;
      }
      let html = resultsHeadlineHtml(results);
      if (results.alerts && results.alerts.length && tab.key !== "adviser") {
        html += `<div class="results-alert liquid">${results.alerts.map((a) => `<p>${esc(a)}</p>`).join("")}</div>`;
      }
      if (tabs.length > 1) {
        html += `<div class="results-tabs">${tabs.map((t, i) =>
          `<button type="button" class="results-tab${i === active ? " on" : ""}" data-tab="${i}">${esc(t.label)}</button>`
        ).join("")}</div>`;
      }

      if (tab.adviser) {
        const adv = tab.adviser;
        if (!adv.ok) {
          html += `<p class="results-alert">Adviser analysis failed: ${esc(adv.error || "Unknown error")}</p>`;
        } else {
          html += `<div class="glass-inner adviser-block"><h3 class="results-section-title">Goal status</h3><p>${esc(adv.goal_status || "")}</p>`;
          if (adv.holdings_summary) {
            html += `<p class="caption" style="margin-top:8px">${esc(adv.holdings_summary)}</p>`;
          }
          if (adv.success_rate != null) {
            html += `<p class="caption" style="margin-top:8px">Success rate ${fmtPct(adv.success_rate)} · target ${fmtPct(adv.target_success_rate)}</p>`;
          }
          html += `</div>`;
          const recs = adv.recommendations || [];
          if (recs.length) {
            html += `<h3 class="results-section-title">Recommendations</h3>`;
            recs.forEach((rec, i) => {
              html += `<div class="adviser-rec liquid"><h4>${i + 1}. ${esc(rec.title)}</h4><p>${esc(rec.detail)}</p>`;
              if (rec.what_if && rec.what_if.length) {
                html += `<button type="button" class="btn btn-secondary" data-what-if="${i}">Apply what-if</button>`;
              }
              html += `</div>`;
            });
          } else {
            html += `<p class="caption">No specific recommendations — plan metrics look healthy.</p>`;
          }
          if (adv.checklist && adv.checklist.length) {
            html += `<h3 class="results-section-title">Action checklist</h3><ul class="adviser-checklist">${adv.checklist.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
          }
          if (adv.disclaimer) {
            html += `<p class="results-alert" style="margin-top:14px">${esc(adv.disclaimer)}</p>`;
          }
        }
      }

      if (tab.monte_carlo) {
        const mc = tab.monte_carlo;
        html += `<div class="metric-grid">
          <div class="metric-card"><h4>Success rate</h4><p>${fmtPct(mc.success_rate)}</p></div>
          <div class="metric-card"><h4>Median ending</h4><p>${fmtMoney(mc.median_ending)}</p></div>
          <div class="metric-card"><h4>10th percentile</h4><p>${fmtMoney(mc.p10_ending)}</p></div>
          <div class="metric-card"><h4>90th percentile</h4><p>${fmtMoney(mc.p90_ending)}</p></div>
        </div>`;
        if (mc.return_model) {
          html += `<p class="caption" style="margin-bottom:12px">${esc(mc.return_model)} · ${esc(mc.inflation_model || "")}${mc.sampling_model ? ` · ${esc(mc.sampling_model)}` : ""}</p>`;
        }
        if (mc.chart && mc.chart.length) {
          const overlay = results.mode === "both"
            ? alignDetOverlay(mc.chart, tab.deterministic?.chart)
            : null;
          html += `<h3 class="results-section-title">Monte Carlo fan chart</h3><div class="fan-chart-wrap">${fanChartSvg(mc.chart, overlay)}</div>`;
        }
        if (mc.summary_lines && mc.summary_lines.length) {
          html += `<div class="glass-inner results-summary"><pre>${esc(mc.summary_lines.join("\n"))}</pre></div>`;
        }
        if (mc.table && mc.table.length) {
          html += `<h3 class="results-section-title">Monte Carlo paths</h3>`;
          html += resultsTableHtml(mc.table, [
            { label: "Year", fmt: (r) => r.year ?? "—" },
            { label: "Age", fmt: (r) => r.age ?? "—" },
            { label: "P10", fmt: (r) => fmtMoney(r.p10) },
            { label: "P50", fmt: (r) => fmtMoney(r.p50) },
            { label: "P90", fmt: (r) => fmtMoney(r.p90) },
          ]);
        }
      }

      if (tab.deterministic) {
        const det = tab.deterministic;
        html += `<div class="metric-grid">
          <div class="metric-card"><h4>Starting balance</h4><p>${fmtMoney(det.starting_balance)}</p></div>
          <div class="metric-card"><h4>Peak balance</h4><p>${fmtMoney(det.peak_balance)}</p></div>
          <div class="metric-card"><h4>Ending balance</h4><p>${fmtMoney(det.ending_balance)}</p></div>
        </div>`;
        if (det.chart && det.chart.length && !tab.monte_carlo) {
          const max = Math.max(...det.chart.map((p) => Number(p.balance) || 0), 1);
          html += `<h3 class="results-section-title">Assets over time</h3>`;
          html += `<div class="chart-bars">${det.chart.map((p) =>
            `<div class="chart-bar" style="height:${Math.max(4, ((Number(p.balance) || 0) / max) * 100)}%" title="Age ${p.age}: ${fmtMoney(p.balance)}"></div>`
          ).join("")}</div>`;
        }
        if (det.table && det.table.length) {
          html += `<h3 class="results-section-title">Projection sample</h3>`;
          html += resultsTableHtml(det.table, [
            { label: "Year", fmt: (r) => r.year ?? "—" },
            { label: "Age", fmt: (r) => r.age ?? "—" },
            { label: "Assets", fmt: (r) => fmtMoney(r.assets) },
            { label: "Expenses", fmt: (r) => fmtMoney(r.expenses) },
            { label: "SS", fmt: (r) => fmtMoney(r.ss) },
          ]);
        }
      }

      html += resultsActionsHtml(tab.key || tab.label, tab.deterministic, tab.monte_carlo);
      body.innerHTML = html || "<p>No chart data for this tab.</p>";
      body.querySelectorAll("[data-tab]").forEach((btn) => {
        btn.onclick = () => { active = parseInt(btn.dataset.tab, 10); renderTab(); };
      });
      bindResultsExports(body, tab, tab.key || tab.label || "projection");
      if (tab.adviser?.ok) {
        const recs = tab.adviser.recommendations || [];
        body.querySelectorAll("[data-what-if]").forEach((btn) => {
          btn.onclick = async () => {
            const rec = recs[parseInt(btn.dataset.whatIf, 10)];
            if (!rec?.what_if?.length || !api()) return;
            const res = await api().apply_what_if(rec.what_if);
            await showAlert(res.message || (res.ok ? "Applied." : "Failed."));
            if (res.ok) await refreshState();
          };
        });
      }
    }

    renderTab();
    document.querySelector("[data-results-overlay]").classList.add("open");
  }

  /* ── Field rendering ── */
  function optionText(f, option, index) {
    const labels = f.optionLabels;
    if (labels && labels[index] != null && labels[index] !== "") return labels[index];
    return option;
  }

  function radioControl(f) {
    const key = esc(f.key || "");
    const scope = f.scope ? ` data-scope="${esc(f.scope)}"` : "";
    const person = f.person ? ` data-person="${esc(f.person)}"` : "";
    const selected = f.on != null ? f.on : -1;
    const pills = (f.options || []).map((option, index) =>
      `<div class="radio-pill${index === selected ? " on" : ""}" data-value="${esc(option)}">${esc(optionText(f, option, index))}</div>`
    ).join("");
    return `<div class="radio-row" data-key="${key}"${scope}${person}>${pills}</div>`;
  }

  function selectControl(f) {
    const key = esc(f.key || "");
    const scope = f.scope ? ` data-scope="${esc(f.scope)}"` : "";
    const person = f.person ? ` data-person="${esc(f.person)}"` : "";
    const policy = f.key === "state_of_residence" ? " data-policy-caption" : "";
    const options = (f.options || []).map((option, index) =>
      `<option value="${esc(option)}"${String(option) === String(f.value) ? " selected" : ""}>${esc(optionText(f, option, index))}</option>`
    ).join("");
    return `<div class="select-wrap"><select class="state-select" data-key="${key}"${scope}${person}${policy}>${options}</select></div>`;
  }

  function fieldHtml(f, idx) {
    const n = idx + 1;
    if (f.type === "more") return `<p class="more-fields">${esc(f.text)}</p>`;
    const qLabel = `<p class="q-label"><span>${n}.</span>${esc(f.label)}</p>`;
    const key = esc(f.key || "");
    const scope = f.scope ? ` data-scope="${esc(f.scope)}"` : "";
    const person = f.person ? ` data-person="${esc(f.person)}"` : "";
    const example = f.example ? " example" : "";
    let control = "";
    switch (f.type) {
      case "radio":
        control = radioControl(f);
        break;
      case "select":
        control = selectControl(f);
        break;
      case "narrow":
      case "year":
        control = `<input class="glass-input ${f.type === "year" ? "narrow year" : "narrow"}${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} />`;
        break;
      case "medium":
        control = f.suffix
          ? `<div class="input-suffix"><input class="glass-input medium${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} /><span class="suffix">${esc(f.suffix)}</span></div>`
          : `<input class="glass-input medium${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} />`;
        break;
      case "dollar":
        control = `<div class="input-dollar${example}"><span class="prefix">$</span><input class="${example.trim()}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} /></div>`;
        break;
      default:
        control = `<input class="glass-input medium${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} />`;
    }
    let caption = f.caption || "";
    if (f.key === "state_of_residence" && appState.policy_label) {
      caption = `Tax tables in use — ${appState.policy_label}`;
    }
    const cap = caption ? `<p class="caption${f.captionTight ? " tight" : " loose"}">${esc(caption)}</p>` : "";
    const errKey = `${f.person || "shared"}:${f.key || ""}`;
    const err = `<p class="field-error" data-field-error="${esc(errKey)}" aria-live="polite"></p>`;
    const showWhen = f.showWhen
      ? ` data-show-when="${esc(f.showWhen.key)}" data-show-value="${esc(f.showWhen.value)}"`
      : "";
    return `<div class="glass-inner field-block" data-field-block="${esc(errKey)}"${showWhen}>${qLabel}${control}${err}${cap}</div>`;
  }

  function fieldSearchText(f) {
    return String(f.searchText || `${f.key || ""} ${f.shortLabel || ""} ${f.label || ""} ${f.caption || ""}`)
      .trim()
      .toLowerCase();
  }

  function expertFieldControl(f) {
    const key = esc(f.key || "");
    const scope = f.scope ? ` data-scope="${esc(f.scope)}"` : "";
    const person = f.person ? ` data-person="${esc(f.person)}"` : "";
    const example = f.example ? " example" : "";
    switch (f.type) {
      case "radio":
        return radioControl(f);
      case "select":
        return selectControl(f);
      case "narrow":
      case "year":
        return `<input class="glass-input ${f.type === "year" ? "narrow year" : "narrow"}${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} />`;
      case "medium":
        return f.suffix
          ? `<div class="input-suffix"><input class="glass-input medium${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} /><span class="suffix">${esc(f.suffix)}</span></div>`
          : `<input class="glass-input medium${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} />`;
      case "dollar":
        return `<div class="input-dollar${example}"><span class="prefix">$</span><input class="${example.trim()}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} /></div>`;
      default:
        return `<input class="glass-input medium${example}" type="text" value="${esc(f.value)}" data-key="${key}"${scope}${person} />`;
    }
  }

  function expertFieldHtml(f) {
    return `<div class="field-row field-block" data-field-search="${esc(fieldSearchText(f))}">
      <span class="field-label">${esc(f.shortLabel || f.label)}</span>
      ${expertFieldControl(f)}
    </div>`;
  }

  function filterExpertGroups(groups, query) {
    const q = String(query || "").trim().toLowerCase();
    if (!q) return groups || [];
    return (groups || [])
      .map((g) => {
        const fields = (g.fields || []).filter((f) => fieldSearchText(f).includes(q));
        return fields.length ? { ...g, fields, matchCount: fields.length } : null;
      })
      .filter(Boolean);
  }

  function bindExpertSearch(root) {
    const input = root.querySelector("[data-expert-search]");
    if (!input) return;
    input.value = expertSearchQuery;
    input.oninput = () => {
      expertSearchQuery = input.value;
      applyExpertSearch(root);
    };
    applyExpertSearch(root);
  }

  function applyExpertSearch(root) {
    const q = expertSearchQuery.trim().toLowerCase();
    const groups = root.querySelectorAll("[data-expert-group]");
    let visibleGroups = 0;
    groups.forEach((group) => {
      const blocks = group.querySelectorAll("[data-field-search]");
      let visibleFields = 0;
      blocks.forEach((block) => {
        const match = !q || (block.dataset.fieldSearch || "").includes(q);
        block.classList.toggle("hidden", !match);
        if (match) visibleFields += 1;
      });
      const show = visibleFields > 0;
      group.classList.toggle("hidden", !show);
      if (q && show) group.open = true;
      if (show) visibleGroups += 1;
    });
    const empty = root.querySelector("[data-search-empty]");
    if (empty) empty.classList.toggle("hidden", !q || visibleGroups > 0);
  }

  function llmChecklistHtml(llm) {
    return (llm.fields || []).map((f) => {
      const checked = f.empty ? "checked" : "";
      const emptyAttr = f.empty ? " data-empty='1'" : "";
      return `<label class="check-row"><input type="checkbox" data-llm-id="${esc(f.id)}" data-field-key="${esc(f.field_key)}" data-person="${esc(f.person || "")}"${emptyAttr} ${checked} />${esc(f.label)}</label>`;
    }).join("");
  }

  function llmSelectToolbarHtml(activeMode) {
    const mode = activeMode || "empty";
    return `<div class="expert-toolbar llm-select-toolbar">
      <div class="llm-select-head">
        <span class="field-label llm-select-label">Which fields?</span>
        <span class="llm-select-hint">Unfilled = blank or skipped in your plan</span>
      </div>
      <div class="radio-row" data-llm-select-mode>
        <div class="radio-pill${mode === "all" ? " on" : ""}" data-mode="all">All</div>
        <div class="radio-pill${mode === "none" ? " on" : ""}" data-mode="none">None</div>
        <div class="radio-pill${mode === "empty" ? " on" : ""}" data-mode="empty" title="Check only fields you have not filled in yet">Unfilled</div>
      </div>
    </div>`;
  }

  function llmSelectionHostHtml(llm) {
    if (!llm) return "";
    return `<div class="llm-selection-host" hidden aria-hidden="true">
      ${llmSelectToolbarHtml("empty")}
      <div class="llm-checklist">${llmChecklistHtml(llm)}</div>
    </div>`;
  }

  function llmPanelInputContentHtml(llm, { guided = false } = {}) {
    const context = `<p class="caption llm-status-line">${esc(llm.status)}</p>
      <label class="field-label llm-context-label">Additional context (optional)</label>
      <textarea class="glass-input llm-context-input" data-llm-prompt rows="3">${esc(llm.extra_prompt || "")}</textarea>`;
    if (guided) {
      const total = (llm.fields || []).length;
      const selected = (llm.fields || []).filter((f) => f.empty).length;
      return `${context}
        <div class="llm-field-picker-row">
          <span class="llm-field-summary" data-llm-field-summary>${selected} of ${total} fields selected for AI</span>
          <button type="button" class="btn-help" data-llm-fields-open>Choose fields…</button>
        </div>`;
    }
    return `${context}${llmSelectToolbarHtml("empty")}`;
  }

  function llmPanelInputHtml(llm) {
    if (!llm) return "";
    return `<div class="glass-inner question-panel-llm-input">${llmPanelInputContentHtml(llm, { guided: true })}</div>`;
  }

  function llmPanelActionsHtml(llm) {
    if (!llm) return "";
    return `<div class="glass-inner question-panel-llm-actions">
      <div class="llm-action-row">
        <button type="button" class="connect-btn" data-llm-run>Run batch research</button>
        <button type="button" class="btn btn-secondary" data-llm-settings>LLM Settings</button>
      </div>
      <p class="caption loose" data-llm-status></p>
    </div>`;
  }

  function llmPanelHtml(llm) {
    if (!llm) return "";
    return `<div class="ai-panel-expanded glass-inner llm-panel">
      ${llmPanelInputContentHtml(llm)}
      <div class="llm-checklist">${llmChecklistHtml(llm)}</div>
      <div class="llm-action-row">
        <button type="button" class="connect-btn" data-llm-run>Run batch research</button>
        <button type="button" class="btn btn-secondary" data-llm-settings>LLM Settings</button>
      </div>
      <p class="caption loose" data-llm-status></p>
    </div>`;
  }

  function panelWideClass(screen) {
    if (screen.wide || screen.layout === "connect" || screen.layout === "import" || screen.layout === "ai" || screen.layout === "review") {
      return " wide";
    }
    return "";
  }

  function plaidInstitutionsListHtml(institutions, { emptyHint = "No banks linked yet." } = {}) {
    const list = institutions || [];
    if (!list.length) {
      return `<div class="plaid-inst-list empty"><p class="caption">${esc(emptyHint)}</p></div>`;
    }
    return `<div class="plaid-inst-list">
      <p class="plaid-inst-heading">Connected institutions (${list.length})</p>
      ${list
        .map((inst) => {
          const accts = (inst.account_names || []).slice(0, 6);
          const more =
            (inst.account_names || []).length > 6
              ? ` +${(inst.account_names || []).length - 6} more`
              : "";
          const acctLine = accts.length
            ? accts.join(" · ") + more
            : inst.account_count
              ? `${inst.account_count} account(s)`
              : "Linked via Plaid";
          return `<div class="plaid-inst-row">
            <div class="plaid-inst-copy">
              <strong>${esc(inst.name || "Institution")}</strong>
              <span class="plaid-inst-meta">${esc(acctLine)}</span>
            </div>
          </div>`;
        })
        .join("")}
    </div>`;
  }

  function connectBodyHtml(screen) {
    const ld = screen.live_data || {};
    const status = ld.status || appState.live_status || "";
    const institutions =
      ld.institutions || appState.plaid_connections?.institutions || [];
    return `<div class="glass-inner connect-card">
      <p class="live-status">${esc(status)}</p>
      <p>${esc(screen.connectText || ld.connect_text || "")}</p>
      ${plaidInstitutionsListHtml(institutions)}
      <div class="connect-actions">
        <div class="connect-action-group">
          <p class="connect-group-label">Institutions</p>
          <div class="connect-btn-row">
            <button type="button" class="connect-btn" data-connect-plaid>Connect with Plaid</button>
            <button type="button" class="connect-btn" data-manage-accounts>Manage accounts</button>
          </div>
        </div>
        <div class="connect-action-group">
          <p class="connect-group-label">Balances</p>
          <div class="connect-btn-row">
            <button type="button" class="connect-btn" data-refresh-balances>Refresh balances</button>
            <button type="button" class="connect-btn" data-import-balances>Import from CSV</button>
          </div>
        </div>
        <div class="connect-action-group">
          <p class="connect-group-label">Spending</p>
          <div class="connect-btn-row">
            <button type="button" class="connect-btn" data-refresh-expenses>Refresh expenses</button>
            <button type="button" class="connect-btn" data-spending-builder="self">Spending builder…</button>
          </div>
        </div>
      </div>
      <p class="caption loose" data-action-status style="margin-top:16px;min-height:1.2em"></p>
    </div>`;
  }

  function importBodyHtml(screen) {
    const imp = screen.import || {};
    const types = (imp.types || []).map((t) =>
      `<div class="import-type-card${t.ready ? "" : " stub"}">
        <h3>${esc(t.label)}${t.ready ? "" : " <span class='import-soon'>Soon</span>"}</h3>
        <p>${esc(t.detail || "")}</p>
        ${t.ready ? `<button type="button" class="connect-btn" data-import-document data-doc-type="${esc(t.id)}">Choose file…</button>` : ""}
      </div>`
    ).join("");
    return `<div class="glass-inner connect-card">
      <p class="live-status">${esc(imp.status || "")}</p>
      <p>Import documents to pre-fill planning fields. You'll review every suggestion before it updates your plan.</p>
      <div class="import-types">${types}</div>
      ${imp.template_hint ? `<p class="caption loose">${esc(imp.template_hint)}</p>` : ""}
      <p class="caption loose" data-action-status style="margin-top:12px;min-height:1.2em"></p>
    </div>`;
  }

  function summaryCardsHtml(rows) {
    return (rows || []).map((c) => {
      const jump = c.step_id ? ` data-review-jump="${esc(c.step_id)}"` : "";
      return `<button type="button" class="summary-card clickable"${jump}><h3>${esc(c.h)}</h3><p>${esc(c.p)}</p><span class="card-jump">Edit →</span></button>`;
    }).join("");
  }

  function reviewBodyHtml(screen) {
    const cards = summaryCardsHtml(screen.summary);
    const examples = screen.examples || [];
    const exampleBlock = examples.length
      ? `<div class="glass-inner">
          <p class="q-label"><span>2.</span>Still using example numbers</p>
          <p class="caption loose">These are sample figures. Replace them before you trust the result.</p>
          <div class="summary-grid">${summaryCardsHtml(examples)}</div>
        </div>`
      : "";
    const simNumber = examples.length ? 3 : 2;
    const sim = (screen.simOptions || []).map((o, i) =>
      `<div class="radio-pill${i === (screen.simOn ?? 0) ? " on" : ""}" data-sim="${esc(o)}">${esc(o)}</div>`
    ).join("");
    const viewBtn = appState.has_results
      ? `<button type="button" class="connect-btn" data-view-results style="margin-top:12px">View last results</button>`
      : "";
    return `<div class="glass-inner">
      <p class="q-label"><span>1.</span>Your plan at a glance</p>
      <div class="summary-grid">${cards}</div>
    </div>
    ${exampleBlock}
    <div class="glass-inner">
      <p class="q-label"><span>${simNumber}.</span>How should we simulate your plan?</p>
      <div class="radio-row" data-field="sim">${sim}</div>
      ${viewBtn}
    </div>`;
  }

  function guidedPanelHeadHtml(screen, optTag) {
    const docs = screen.documents_panel;
    const docsBtn = docs
      ? `<button type="button" class="btn-help" data-docs-open title="Documents that fill this step">Documents</button>`
      : "";
    return `<div class="glass-inner question-panel-head">
      <div class="step-row">
        <h1 class="step-title">${esc(screen.title)}${optTag}</h1>
        <div class="step-head-actions">
          ${docsBtn}
          <button type="button" class="btn-help" data-help-open>Help</button>
        </div>
      </div>
      ${stepIntroHtml(screen)}
    </div>`;
  }

  function spendingBuilderButtonHtml(person) {
    const who = person === "wife" ? "spouse" : "your";
    return `<div class="spending-builder-callout">
      <p class="caption">Build ${who} retirement spending from categories (rent, mortgage, food, etc.). We’ll pre-fill from Plaid and your plan when possible.</p>
      <button type="button" class="connect-btn" data-spending-builder="${esc(person || "self")}">Open spending builder…</button>
    </div>`;
  }

  function guidedPanelBodyHtml(screen) {
    if (screen.layout === "connect") return connectBodyHtml(screen);
    if (screen.layout === "import") return importBodyHtml(screen);
    if (screen.layout === "review") return reviewBodyHtml(screen);
    const fields = (screen.fields || []).map((f, i) => fieldHtml(f, i)).join("");
    const sid = screen.stepId || screen.step_id || "";
    let builder = "";
    if (sid === "spending_self") builder = spendingBuilderButtonHtml("self");
    if (sid === "spending_wife") builder = spendingBuilderButtonHtml("wife");
    return `${builder}${fields}`;
  }

  function guidedPanelHtml(screen, optTag) {
    if (screen.layout === "ai") {
      return `<div class="content-glass liquid liquid-strong question-panel question-panel-ai${panelWideClass(screen)}">
        ${guidedPanelHeadHtml(screen, optTag)}
        ${llmPanelInputHtml(screen.llm)}
        ${llmSelectionHostHtml(screen.llm)}
        ${llmPanelActionsHtml(screen.llm)}
      </div>`;
    }
    return `<div class="content-glass liquid liquid-strong question-panel${panelWideClass(screen)}">
      ${guidedPanelHeadHtml(screen, optTag)}
      <div class="question-panel-scroll">${guidedPanelBodyHtml(screen)}</div>
    </div>`;
  }

  function stepIntroHtml(screen) {
    let html = "";
    const desc = screen.layout === "ai" ? screen.aiText : screen.intro;
    if (desc) {
      html += `<div class="step-intro"><p>${esc(desc)}</p></div>`;
    }
    if (screen.microTotal > 1) {
      html += `<div class="micro-head">
        <p class="micro-part">Part ${screen.microStep} of ${screen.microTotal}</p>
        <h2 class="micro-title">${esc(screen.microTitle || "")}</h2>
      </div>`;
    }
    return html;
  }

  function microBackDisabled(screen) {
    const f = screen.footer || {};
    const onFirstMicro = !screen.microTotal || screen.microTotal <= 1 || (screen.microStep || 1) <= 1;
    return (current <= 0 || f.backDisabled) && onFirstMicro;
  }

  function expertAssumptionsToolbarHtml(screen) {
    const scopes = screen.scope_options || ["shared"];
    const scopePills = scopes.map((s, i) =>
      `<div class="radio-pill${i === (screen.scope_on ?? 0) ? " on" : ""}" data-expert-scope="${esc(s)}">${esc(s)}</div>`
    ).join("");
    return `<div class="expert-toolbar expert-panel-toolbar">
      <span class="field-label">Scope</span>
      <div class="radio-row" data-expert-scope-row>${scopePills}</div>
      <input class="glass-input search" type="search" data-expert-search placeholder="Search fields…" autocomplete="off" />
    </div>`;
  }

  function expertSimToolbarHtml(screen) {
    const modes = ["Deterministic", "Monte Carlo", "Both"];
    const modeOn = screen.sim_mode_on ?? 2;
    const precOn = screen.precision_on ?? 0;
    return `<div class="expert-panel-toolbar expert-sim-toolbar">
      <div class="expert-sim-row">
        <span class="field-label">Simulation mode</span>
        <div class="radio-row" data-expert-sim-mode>${modes.map((m, i) =>
          `<div class="radio-pill${i === modeOn ? " on" : ""}" data-value="${esc(m)}">${esc(m)}</div>`
        ).join("")}</div>
      </div>
      <div class="expert-sim-row">
        <span class="field-label">Planning precision</span>
        <div class="radio-row" data-expert-precision>
          <div class="radio-pill${precOn === 0 ? " on" : ""}" data-value="standard">Standard</div>
          <div class="radio-pill${precOn === 1 ? " on" : ""}" data-value="cpa">CPA Precision</div>
        </div>
      </div>
    </div>`;
  }

  function expertPanelToolbarHtml(screen) {
    if (screen.layout === "assumptions") return expertAssumptionsToolbarHtml(screen);
    if (screen.layout === "simulate") return expertSimToolbarHtml(screen);
    return "";
  }

  function expertPanelHeadHtml(screen) {
    const toolbar = expertPanelToolbarHtml(screen);
    return `<div class="glass-inner question-panel-head expert-panel-head">
      <div class="step-row">
        <h1 class="step-title">${esc(screen.title)}</h1>
        <button type="button" class="link-guided" data-jump-guided>Jump to Guided →</button>
      </div>
      ${screen.subtitle ? `<div class="step-intro"><p>${esc(screen.subtitle)}</p></div>` : ""}
      ${toolbar}
    </div>`;
  }

  function expertPanelScrollHtml(screen) {
    if (screen.layout === "setup") {
      return (screen.fields || []).map((f, i) => fieldHtml(f, i)).join("");
    }
    if (screen.layout === "live_data") {
      const ld = screen.live_data || {};
      const cards = (ld.cards || []).map((c) =>
        `<div class="data-card"><h3>${esc(c.title)}</h3><p>${esc(c.description)}</p>${(c.actions || []).map((a) =>
          `<button type="button" class="connect-btn" data-live-action="${esc(a)}">${esc(a)}</button>`
        ).join("")}</div>`
      ).join("");
      return `<div class="glass-inner">
        <p class="live-status">${esc(ld.status || "")}</p>
        <div class="cards-grid">${cards}</div>
      </div>`;
    }
    if (screen.layout === "simulate") {
      return `<div class="glass-inner">${(screen.fields || []).map((f, i) => fieldHtml(f, i)).join("")}</div>`;
    }
    if (screen.layout === "settings") {
      const pa = screen.plan_actions || {};
      const saved = savedLabel(pa.last_saved_at || appState.last_saved_at);
      const planCard = `<div class="glass-inner plan-actions-card">
        <p class="field-label">Your plan</p>
        <p class="caption tight">${esc(pa.save_detail || "Save to restore on next launch")}</p>
        ${saved ? `<p class="caption tight">${esc(saved)}</p>` : ""}
        <p class="caption tight">${esc(pa.new_detail || "")}</p>
      </div>`;
      const items = (screen.items || []).map((it) =>
        `<button type="button" class="connect-btn" data-settings-action="${esc(it.id)}" style="width:100%;margin-bottom:8px;text-align:left">${esc(it.label)}<span style="display:block;font-size:11px;color:var(--muted);margin-top:4px">${esc(it.detail)}</span></button>`
      ).join("");
      return `${planCard}<div class="glass-inner settings-list">${items}</div>`;
    }
    if (screen.layout === "assumptions") {
      const groups = (screen.groups || []).map((g) => {
        const jump = g.guided_step_id ? ` data-jump-guided-step="${esc(g.guided_step_id)}"` : "";
        return `<details class="section-group liquid" data-expert-group data-was-open="1" open>
          <summary class="group-summary">
            <span class="group-title">${esc(g.title)}</span>
            <button type="button" class="group-guided-link"${jump}>Guided →</button>
          </summary>
          <div class="group-fields">${(g.fields || []).map((f) => expertFieldHtml(f)).join("")}</div>
        </details>`;
      }).join("");
      return `<div class="section-groups" data-expert-groups>${groups}</div>
        <p class="search-empty hidden" data-search-empty>No fields match your search.</p>`;
    }
    return `<div class="glass-inner"><p>Section not available.</p></div>`;
  }

  function expertPanelHtml(screen) {
    if (screen.layout === "llm") {
      return `<div class="content-glass liquid liquid-strong question-panel question-panel-ai expert-panel wide">
        ${expertPanelHeadHtml(screen)}
        ${llmPanelInputHtml(screen.llm)}
        ${llmSelectionHostHtml(screen.llm)}
        ${llmPanelActionsHtml(screen.llm)}
      </div>`;
    }
    return `<div class="content-glass liquid liquid-strong question-panel expert-panel wide">
      ${expertPanelHeadHtml(screen)}
      <div class="question-panel-scroll">${expertPanelScrollHtml(screen)}</div>
    </div>`;
  }

  function expertFooterHtml(screen) {
    const saved = savedLabel(appState.last_saved_at);
    const ring = completenessRingHtml(appState.completeness?.percent);
    const ds = dataSourcesHint();
    const dsMeta = ds ? `<span class="dock-meta-item">${ds}</span>` : "";
    const restored = appState.plan_restored && saved
      ? `<span class="dock-meta-item restored-hint">${esc(saved)} · auto-loads on restart</span>`
      : saved
        ? `<span class="dock-meta-item save-hint">${esc(saved)}</span>`
        : "";
    const meta = [dsMeta, restored].filter(Boolean).join("");
    return `<div class="chrome-dock liquid chrome-dock-compact">
      <div class="dock-bar">
        <div class="dock-progress dock-progress-wide">
          ${ring}
          <span class="dock-journey">${esc(screen.title)}</span>
        </div>
        <div class="dock-primary">
          <button type="button" class="btn btn-run" data-expert-run>Run plan</button>
        </div>
      </div>
      ${meta ? `<div class="dock-meta">${meta}</div>` : ""}
    </div>`;
  }

  function helpHtml(screen) {
    const items = (screen.help || []).map((h) =>
      `<div class="help-item"><h3>${h.h}</h3><p>${h.p}</p></div>`
    ).join("");
    return `<div class="help-backdrop" data-backdrop></div>
      <aside class="help-panel liquid liquid-strong" data-panel>
        <div class="help-head"><span>${esc(screen.helpTitle || "Help")}</span><button type="button" class="btn-x" data-help-close>✕</button></div>
        <div class="help-body">${items}</div>
      </aside>`;
  }

  function documentsPanelDocHtml(doc) {
    const status = doc.status || "missing";
    const thisStep = (doc.fields_this_step || [])
      .map((f) => esc(f.label || f.field_key))
      .slice(0, 6)
      .join(", ");
    const laterCount = doc.other_step_count || (doc.fields_other_steps || []).length;
    const laterNote = laterCount
      ? `<div class="docs-meta">Also fills ${laterCount} field(s) on later steps</div>`
      : "";
    const thisNote = thisStep
      ? `<div class="docs-meta">This step: ${thisStep}</div>`
      : "";
    let action = "";
    if (doc.acquisition === "plaid_link") {
      action = `<button type="button" class="connect-btn" data-docs-action="connect_plaid">Connect Plaid</button>
        <button type="button" class="connect-btn" data-docs-action="refresh_balances">Refresh balances</button>`;
    } else if (doc.acquisition === "plaid_refresh_expenses") {
      action = `<button type="button" class="connect-btn" data-docs-action="refresh_expenses">Refresh expenses</button>`;
    } else if (doc.acquisition === "refresh_market") {
      action = `<button type="button" class="connect-btn" data-docs-action="refresh_market">Refresh market</button>`;
    } else if (doc.acquisition === "upload" && doc.ready !== false) {
      action = `<button type="button" class="connect-btn" data-docs-upload="${esc(doc.id)}">Upload…</button>`;
    } else {
      action = `<span class="lazy-status-pill">Coming soon</span>`;
    }
    return `<div class="docs-row ${esc(status)}">
      <div class="docs-copy">
        <h3>${esc(doc.label || doc.id)}</h3>
        <p>${esc(doc.accountant_prompt || "")}</p>
        ${thisNote}
        ${laterNote}
        <div class="docs-meta">${esc(doc.status_detail || "")}</div>
      </div>
      <div class="docs-actions">
        <span class="lazy-status-pill ${esc(status)}">${esc(status)}</span>
        ${action}
      </div>
    </div>`;
  }

  function showDocumentsPanelModal(panel) {
    if (!panel) return;
    const docs = panel.documents || [];
    const body = docs.length
      ? `<p style="margin:0 0 14px;color:var(--secondary);line-height:1.5">${esc(panel.intro || "")}</p>
         <div class="docs-list">${docs.map(documentsPanelDocHtml).join("")}</div>`
      : `<p style="color:var(--secondary);line-height:1.5">${esc(panel.empty_message || "No documents for this step.")}</p>`;
    showModal(panel.title || "Documents", body, [{ label: "Close" }]);

    const root = document.querySelector(".modal, [data-modal], .modal-card") || document;
    // Bind inside modal after paint
    setTimeout(() => {
      document.querySelectorAll("[data-docs-upload]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const docId = btn.dataset.docsUpload;
          if (!api()) return;
          showProgress("Reading document…");
          try {
            const res = api().upload_step_document
              ? await api().upload_step_document(docId)
              : await api().lazy_upload_document(docId);
            if (res.cancelled) return;
            if (res.ok && res.review_rows?.length) {
              showDocumentImportModal({
                message: res.message,
                rows: res.review_rows,
                document_type: res.document_type,
                document_label: res.document_label,
              });
            } else if (res.failure && !res.ok) {
              showParserFailureModal(res.failure);
              return;
            } else if (!res.ok) {
              await showAlert(res.message || "Import failed.");
              return;
            } else {
              await showAlert(res.message || "Done.");
            }
            await refreshState();
            await renderCurrent();
          } finally {
            hideProgress();
          }
        });
      });
      document.querySelectorAll("[data-docs-action]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const action = btn.dataset.docsAction;
          if (!api()) return;
          if (action === "connect_plaid") {
            showProgress("Starting Plaid Link…");
            try {
              const res = await api().connect_plaid();
              await showAlert(res.message || (res.ok ? "Opened Plaid." : "Failed."));
              await refreshState();
              await renderCurrent();
            } finally { hideProgress(); }
            return;
          }
          if (action === "refresh_balances") {
            showProgress("Refreshing balances…");
            try {
              const res = await api().refresh_live_balances();
              if (!res.ok) await showAlert(res.message || "Failed.");
              else if (res.review) showBalanceReviewModal(res.review);
              await refreshState();
              await renderCurrent();
            } finally { hideProgress(); }
            return;
          }
          if (action === "refresh_expenses") {
            showProgress("Analyzing expenses…");
            try {
              const res = await api().refresh_expenses_review();
              if (!res.ok) await showAlert(res.message || "Failed.");
              else if (res.review) showExpenseReviewModal(res.review);
              await refreshState();
              await renderCurrent();
            } finally { hideProgress(); }
            return;
          }
          if (action === "refresh_market") {
            showProgress("Refreshing market data…");
            try {
              const res = await api().refresh_market();
              await showAlert(res.message || (res.ok ? "Market updated." : "Failed."));
              await refreshState();
              await renderCurrent();
            } finally { hideProgress(); }
          }
        });
      });
    }, 0);
  }

  function appHeaderHtml(activeMode) {
    const mode = activeMode === "lazy" ? "ai" : activeMode;
    const guidedOn = mode === "guided" ? " on" : "";
    const expertOn = mode === "expert" ? " on" : "";
    const aiOn = mode === "ai" ? " on" : "";
    return `<header class="header liquid">
      <div class="header-left">
        <span class="app-name">Retirement Planner</span>
        <div class="pill-toggle">
          <button type="button" class="${guidedOn ? "on" : ""}" data-mode="guided">Guided</button>
          <button type="button" class="${expertOn ? "on" : ""}" data-mode="expert">Expert</button>
          <button type="button" class="${aiOn ? "on" : ""}" data-mode="ai" title="Upload documents — local AI fills your facts">AI</button>
        </div>
      </div>
      <div class="header-right">
        <button type="button" class="btn btn-secondary btn-compact" data-save-plan>Save plan</button>
        <button type="button" class="btn btn-secondary btn-compact" data-start-new-plan>Start new plan</button>
        <button type="button" class="btn btn-secondary btn-compact" data-pdf-redactor title="Redact bank statements, W-2s, 1040s (local, private)">Redact PDF</button>
        <button type="button" class="btn btn-secondary btn-compact" data-restart-app title="Save plan and relaunch the app">Restart</button>
      </div>
    </header>`;
  }

  function guidedHeaderHtml() {
    return appHeaderHtml("guided");
  }

  function footerHtml(screen) {
    const f = screen.footer || {};
    const backOff = microBackDisabled(screen);
    const backCls = backOff ? " disabled" : "";
    const skipCls = f.skipProminent ? " btn-skip-prominent" : "";
    const showRun = f.showRun || screen.stepId === "review";
    const stepNum = screen.num || current + 1;
    const saved = savedLabel(appState.last_saved_at);
    const ring = completenessRingHtml(appState.completeness?.percent);
    const micro =
      screen.microTotal > 1
        ? `<span class="dock-meta-item micro-progress">${esc(screen.microTitle || "")} · ${screen.microStep}/${screen.microTotal}</span>`
        : "";
    const restored = appState.plan_restored && saved
      ? `<span class="dock-meta-item restored-hint">${esc(saved)} · auto-loads on restart</span>`
      : saved
        ? `<span class="dock-meta-item save-hint">${esc(saved)}</span>`
        : "";
    const ds = dataSourcesHint();
    const dsMeta = ds ? `<span class="dock-meta-item">${ds}</span>` : "";
    const meta = [micro, dsMeta, restored].filter(Boolean).join("");
    const primary = showRun
      ? `<button type="button" class="btn btn-run" data-run>Run plan</button>`
      : `<button type="button" class="btn btn-primary" data-continue>Continue →</button>`;
    return `<div class="chrome-dock liquid chrome-dock-compact">
      <div class="dock-bar">
        <div class="dock-nav">
          <button type="button" class="btn btn-secondary btn-dock${backCls}" data-back${backCls ? " disabled" : ""}>← Back</button>
          ${showRun ? "" : `<button type="button" class="btn btn-secondary btn-dock${skipCls}" data-skip>Skip</button>`}
        </div>
        <div class="dock-progress">
          ${ring}
          <span class="dock-step"><strong>${stepNum}</strong><span class="dock-step-total">/${totalSteps}</span></span>
          <div class="dots dots-compact">${dotsHtml(stepNum)}</div>
          <span class="dock-journey">${esc(screen.journey)}</span>
        </div>
        <div class="dock-primary">${primary}</div>
      </div>
      ${meta ? `<div class="dock-meta">${meta}</div>` : ""}
    </div>`;
  }

  function expertShellHtml(screen, nav) {
    const rail = (nav || []).map((n) =>
      `<button type="button" class="rail-link${n.id === expertSection ? " on" : ""}" data-expert-nav="${esc(n.id)}">${esc(n.label)}</button>`
    ).join("");
    return `<div class="window expert-window">
      ${appHeaderHtml("expert")}
      <div class="expert-body">
        <nav class="section-rail liquid"><span class="rail-label">Sections</span>${rail}</nav>
        <main class="expert-main">${expertPanelHtml(screen)}</main>
      </div>
      ${expertFooterHtml(screen)}
    </div>`;
  }

  function fieldErrorKey(key, person) {
    return `${person || "shared"}:${key || ""}`;
  }

  function findFieldBlock(root, key, person) {
    return root?.querySelector(`[data-field-block="${fieldErrorKey(key, person)}"]`);
  }

  function setFieldError(root, key, person, message) {
    const block = findFieldBlock(root, key, person);
    if (!block) return;
    const err = block.querySelector("[data-field-error]");
    if (err) err.textContent = message || "";
    block.classList.toggle("has-error", Boolean(message));
  }

  function clearFieldErrors(root) {
    root?.querySelectorAll("[data-field-block]").forEach((block) => {
      block.classList.remove("has-error");
      const err = block.querySelector("[data-field-error]");
      if (err) err.textContent = "";
    });
  }

  function applyFieldErrors(root, fieldErrors) {
    clearFieldErrors(root);
    (fieldErrors || []).forEach((row) => setFieldError(root, row.key, row.person, row.message));
  }

  function collectFieldUpdates(root) {
    const updates = [];
    root.querySelectorAll("select[data-key], input[data-key]").forEach((el) => {
      if (!el.dataset.key) return;
      updates.push({ key: el.dataset.key, value: el.value, person: el.dataset.person || null, scope: el.dataset.scope || null });
    });
    root.querySelectorAll(".radio-row[data-key]").forEach((row) => {
      const on = row.querySelector(".radio-pill.on");
      if (row.dataset.key && on) {
        updates.push({ key: row.dataset.key, value: on.dataset.value, person: row.dataset.person || null, scope: row.dataset.scope || null });
      }
    });
    return updates;
  }

  function updateFromControl(el) {
    return {
      key: el.dataset.key,
      value: el.value,
      person: el.dataset.person || null,
      scope: el.dataset.scope || null,
    };
  }

  async function validateControlInput(el, { required = false } = {}) {
    if (!api() || !el?.dataset?.key) return true;
    const root = document.getElementById("app");
    const res = await api().validate_field_updates([updateFromControl(el)], required);
    setFieldError(root, el.dataset.key, el.dataset.person || null, res.field_errors?.[0]?.message || "");
    return res.ok;
  }

  function deriveBirthYearFromAge(ageRaw) {
    const age = parseInt(String(ageRaw ?? "").trim(), 10);
    if (!Number.isFinite(age) || age < 1 || age > 120) return null;
    return String(new Date().getFullYear() - age);
  }

  function findFieldInput(root, key, person) {
    const scope = person || "self";
    return (
      root.querySelector(`input[data-key="${key}"][data-person="${scope}"]`) ||
      root.querySelector(`select[data-key="${key}"][data-person="${scope}"]`) ||
      (scope === "self" ? root.querySelector(`input[data-key="${key}"]:not([data-person])`) : null)
    );
  }

  function maybeAutoFillBirthYear(root, ageInput) {
    if (!ageInput || ageInput.dataset.key !== "current_age") return false;
    const person = ageInput.dataset.person || "self";
    const birthInput = findFieldInput(root, "birth_year", person);
    if (!birthInput) return false;
    if (birthInput.dataset.userEdited === "1") return false;
    const year = deriveBirthYearFromAge(ageInput.value);
    if (!year) return false;
    const empty = !String(birthInput.value || "").trim() || birthInput.value === "0";
    if (!empty && birthInput.dataset.autoFilled !== "1") return false;
    birthInput.value = year;
    birthInput.dataset.autoFilled = "1";
    return true;
  }

  function autoFillBirthYearsFromAges(root) {
    root.querySelectorAll("input[data-key='current_age']").forEach((input) => {
      maybeAutoFillBirthYear(root, input);
    });
  }

  function syncConditionalFields(root) {
    root.querySelectorAll("[data-show-when]").forEach((block) => {
      const key = block.dataset.showWhen;
      const expected = block.dataset.showValue;
      const row = root.querySelector(`.radio-row[data-key="${key}"]`);
      const select = root.querySelector(`select[data-key="${key}"]`);
      let current = "";
      if (row) current = row.querySelector(".radio-pill.on")?.dataset.value || "";
      else if (select) current = select.value;
      block.hidden = current !== expected;
    });
  }

  function clearExampleStyle(el) {
    if (!el) return;
    el.classList.remove("example");
    el.closest(".input-dollar")?.classList.remove("example");
  }

  function bindRadioPills(root, selector, onChange) {
    root.querySelectorAll(selector).forEach((pill) => {
      pill.addEventListener("click", () => {
        const row = pill.closest(".radio-row");
        row.querySelectorAll(".radio-pill").forEach((p) => p.classList.remove("on"));
        pill.classList.add("on");
        if (onChange) onChange(pill, row);
      });
    });
  }

  function showParserFailureModal(failure) {
    if (!failure) return;
    const body = `<p style="line-height:1.5;color:var(--secondary)">${esc(failure.user_message || "Could not read this document.")}</p>
      <p class="field-label" style="margin-top:16px">Update parser rules</p>
      <p style="font-size:12px;color:var(--secondary);word-break:break-all">${esc(failure.config_path || "config/lazy_mode/parsers/")}</p>
      ${failure.developer_message ? `<p style="font-size:12px;color:var(--muted);margin-top:12px">${esc(failure.developer_message)}</p>` : ""}
      ${failure.excerpt ? `<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--secondary)">Text preview</summary><pre style="white-space:pre-wrap;font-size:11px;color:var(--muted);margin-top:8px">${esc(failure.excerpt)}</pre></details>` : ""}`;
    showModal("Parser needs an update", body, [
      { label: "Close" },
      { label: "Open Guided", primary: true, onClick: async () => { await setMode("guided"); }},
    ]);
  }

  function lazyJourneyRailHtml(rail) {
    if (!rail || !rail.length) return "";
    return rail
      .map(
        (block) => `<div class="journey-block">
        <span class="journey-heading">${esc(block.journey)}</span>
        ${(block.steps || [])
          .map((step) => {
            const cls = [
              "journey-step",
              step.current ? "current" : "",
              step.completed ? "done" : "",
              step.skipped ? "skipped" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return `<button type="button" class="${cls}" data-lazy-journey-step="${step.index}" title="${esc(step.label)}">${esc(step.label)}</button>`;
          })
          .join("")}
      </div>`
      )
      .join("");
  }

  function lazyWhereToGetHtml(items) {
    if (!items || !items.length) return "";
    return `<div class="lazy-where-to-get">
      <p class="lazy-where-label">Where to get this</p>
      ${items.map((item) => `<div class="lazy-tip-card liquid">
        <h4>${esc(item.h || "")}</h4>
        <p>${esc(item.p || "")}</p>
      </div>`).join("")}
    </div>`;
  }

  function lazyDocActionsHtml(doc) {
    const id = esc(doc.id);
    if (doc.acquisition === "plaid_link") {
      return `<button type="button" class="connect-btn" data-lazy-action="connect_plaid">Connect Plaid</button>
        <button type="button" class="connect-btn" data-lazy-action="manage_accounts">Manage accounts</button>
        <button type="button" class="connect-btn" data-lazy-action="refresh_balances">Refresh balances</button>`;
    }
    if (doc.acquisition === "plaid_refresh_expenses") {
      return `<button type="button" class="connect-btn" data-lazy-action="refresh_expenses">Refresh expenses</button>
        <button type="button" class="connect-btn" data-spending-builder="self">Spending builder…</button>`;
    }
    if (doc.acquisition === "refresh_market") {
      return `<button type="button" class="connect-btn" data-lazy-action="refresh_market">Refresh market</button>`;
    }
    if (doc.acquisition === "upload" && doc.ready !== false) {
      const pending = Number(doc.pending_extract || 0);
      const onFile = Number(doc.library_count || (doc.library || []).length || 0);
      const extractBtn = pending
        ? `<button type="button" class="connect-btn" data-lazy-extract-pending="${id}">Extract pending (${pending})</button>`
        : "";
      const refreshBtn = onFile
        ? `<button type="button" class="connect-btn" data-lazy-refresh-all="${id}">Refresh all</button>`
        : "";
      return `<button type="button" class="connect-btn" data-lazy-upload="${id}">Add file(s)…</button>${extractBtn}${refreshBtn}`;
    }
    if (doc.acquisition === "upload" && doc.ready === false) {
      return `<span class="lazy-status-pill">Coming soon</span>`;
    }
    if (doc.acquisition === "guided_fallback") {
      return `<button type="button" class="connect-btn" data-lazy-action="open_guided">Open Guided</button>`;
    }
    return "";
  }

  function lazyLibraryListHtml(doc) {
    const files = doc.library || [];
    if (!files.length) return "";
    const showPerson = !!doc.joint_person_picker;
    const rows = files
      .map((f) => {
        const st = f.status || "queued";
        const personBtns = showPerson
          ? `<span class="lazy-person-pills" data-lib-person-group="${esc(f.id)}">
              <button type="button" class="lazy-person-pill${f.person === "self" || !f.person ? " on" : ""}" data-lib-set-person="${esc(f.id)}" data-person="self">You</button>
              <button type="button" class="lazy-person-pill${f.person === "wife" ? " on" : ""}" data-lib-set-person="${esc(f.id)}" data-person="wife">Spouse</button>
            </span>`
          : `<span class="lazy-lib-person">${esc(f.person_label || "Household")}</span>`;
        const extractLabel =
          st === "applied" || st === "extracted" ? "Refresh" : "Extract";
        const extract = `<button type="button" class="connect-btn lazy-lib-btn" data-lazy-refresh="${esc(f.id)}">${extractLabel}</button>`;
        const replace = `<button type="button" class="connect-btn lazy-lib-btn" data-lazy-replace="${esc(f.id)}">Replace file…</button>`;
        return `<div class="lazy-lib-file ${esc(st)}" data-lib-entry="${esc(f.id)}">
          <div class="lazy-lib-file-main">
            <span class="lazy-lib-name" title="${esc(f.original_name || "")}">${esc(f.original_name || "file")}</span>
            ${personBtns}
            <span class="lazy-status-pill ${esc(st)}">${esc(st)}</span>
            ${f.field_count ? `<span class="lazy-lib-meta">${esc(String(f.field_count))} fields</span>` : ""}
          </div>
          <div class="lazy-lib-file-actions">
            ${extract}
            ${replace}
            <button type="button" class="connect-btn lazy-lib-btn danger" data-lazy-remove="${esc(f.id)}">Remove</button>
          </div>
        </div>`;
      })
      .join("");
    return `<div class="lazy-lib-list">
      <p class="lazy-lib-heading">On file (${files.length}) — duplicates are blocked; use Refresh after parser updates or Replace file for a newer PDF</p>
      ${rows}
    </div>`;
  }

  function lazyDocRowHtml(doc) {
    const status = doc.status || "missing";
    const personHint =
      doc.joint_person_picker
        ? `<p class="lazy-doc-hint">Joint plan: tag each file as You or Spouse. Multiple W-2s / statements are fine.</p>`
        : doc.allows_multiple
          ? `<p class="lazy-doc-hint">You can add multiple files (e.g. several accounts or tax years).</p>`
          : "";
    const personPicker =
      doc.joint_person_picker && doc.acquisition === "upload" && doc.ready !== false
        ? `<div class="lazy-add-person" data-lazy-add-person-for="${esc(doc.id)}">
            <span class="lazy-lib-heading">Add as</span>
            <span class="lazy-person-pills">
              <button type="button" class="lazy-person-pill on" data-lazy-default-person="${esc(doc.id)}" data-person="self">You</button>
              <button type="button" class="lazy-person-pill" data-lazy-default-person="${esc(doc.id)}" data-person="wife">Spouse</button>
            </span>
          </div>`
        : "";
    const plaidList =
      doc.acquisition === "plaid_link" || doc.acquisition === "plaid_refresh_expenses"
        ? plaidInstitutionsListHtml(doc.institutions || appState.plaid_connections?.institutions || [])
        : "";
    return `<div class="lazy-doc-row ${esc(status)}" data-lazy-doc="${esc(doc.id)}">
      <div class="lazy-doc-copy">
        <h3>${esc(doc.label)}</h3>
        <p>${esc(doc.accountant_prompt || "")}</p>
        ${personHint}
        <div class="lazy-doc-meta">${esc(doc.status_detail || "")}${doc.required_for_core ? " · Core" : ""}</div>
        ${personPicker}
        ${plaidList}
        ${lazyLibraryListHtml(doc)}
      </div>
      <div class="lazy-doc-actions">
        <span class="lazy-status-pill ${esc(status)}">${esc(status)}</span>
        ${lazyDocActionsHtml(doc)}
      </div>
    </div>`;
  }

  function lazyImportSummaryHtml(summary, { compact = false } = {}) {
    if (!summary || !summary.has_imports) {
      return compact
        ? ""
        : `<div class="lazy-import-summary empty">
            <p class="lazy-tier-label">Import summary</p>
            <p class="caption">Nothing imported yet. Upload and apply a document to see field values here.</p>
          </div>`;
    }
    const entries = summary.entries || [];
    const entryBlocks = entries
      .map((entry) => {
        const fields = entry.fields || [];
        const fieldRows = fields.length
          ? `<table class="lazy-import-table"><thead><tr><th>Field</th><th>Value</th><th>Note</th></tr></thead><tbody>${fields
              .map(
                (f) => `<tr>
              <td>${esc(f.label || f.field_key || "")}</td>
              <td class="lazy-import-value">${esc(f.value || "")}</td>
              <td class="lazy-import-note">${esc(f.source_quote || f.confidence || "")}</td>
            </tr>`
              )
              .join("")}</tbody></table>`
          : `<p class="caption">${esc(String(entry.fields_applied || 0))} field(s) applied${
              entry.when ? ` · ${esc(entry.when)}` : ""
            }</p>`;
        return `<div class="lazy-import-entry">
          <div class="lazy-import-entry-head">
            <strong>${esc(entry.label || "Document")}</strong>
            <span class="lazy-status-pill parsed">${esc(String(entry.fields_applied || fields.length || 0))} fields</span>
          </div>
          ${fieldRows}
        </div>`;
      })
      .join("");

    let liveBlock = "";
    if (!entries.some((e) => (e.fields || []).length) && (summary.live_fields || []).length) {
      liveBlock = `<div class="lazy-import-entry">
        <div class="lazy-import-entry-head"><strong>Currently in your plan</strong></div>
        <table class="lazy-import-table"><thead><tr><th>Field</th><th>Value</th><th>Source</th></tr></thead><tbody>${(
          summary.live_fields || []
        )
          .map(
            (f) => `<tr>
            <td>${esc(f.label || f.field_key || "")}</td>
            <td class="lazy-import-value">${esc(f.value || "")}</td>
            <td class="lazy-import-note">${esc(f.source || "")}</td>
          </tr>`
          )
          .join("")}</tbody></table>
      </div>`;
    }

    return `<div class="lazy-import-summary">
      <p class="lazy-tier-label">Import summary</p>
      <p class="caption" style="margin-bottom:12px">${esc(
        String(summary.total_fields || 0)
      )} field(s) from ${esc(String(summary.entry_count || 0))} import(s) — review values below.</p>
      ${entryBlocks}
      ${liveBlock}
    </div>`;
  }

  function lazyReviewHtml(screen) {
    const rows = (screen.summary || [])
      .map((row) => `<button type="button" class="review-row liquid" data-lazy-review-jump="${esc(row.section_id || "")}">
        <span class="review-h">${esc(row.h)}</span>
        <span class="review-p">${esc(row.p)}</span>
      </button>`)
      .join("");
    const cl = screen.checklist || {};
    const docs = (cl.documents || []).filter((d) => d.status === "parsed" || d.status === "provided");
    const docList = docs.length
      ? `<div class="lazy-review-docs">${docs.map((d) => `<span class="lazy-status-pill ${esc(d.status)}">${esc(d.label)}</span>`).join("")}</div>`
      : `<p class="caption">No documents uploaded yet — use the journey rail to go back.</p>`;
    return `<div class="lazy-review-block">
      ${rows}
      <p class="lazy-tier-label" style="margin-top:20px">On file</p>
      ${docList}
      ${lazyImportSummaryHtml(screen.import_summary)}
      <button type="button" class="connect-btn" data-lazy-action="open_guided" style="margin-top:16px">Open Guided to fine-tune →</button>
    </div>`;
  }

  function lazyGapsHtml(screen) {
    const gaps = screen.gaps || {};
    const userGaps = gaps.user_gaps || gaps.gaps || [];
    const estGaps = gaps.estimate_gaps || [];
    const rows = userGaps.length
      ? userGaps
          .map(
            (g) => `<div class="lazy-gap-row">
        <div class="lazy-doc-copy">
          <h3>${esc(g.label || g.field_key)}</h3>
          <p class="lazy-doc-meta">${esc(g.note || ((g.documents || []).length ? `Try: ${(g.documents || []).join(", ")}` : "Enter in Guided"))}</p>
        </div>
        <div class="lazy-doc-actions">
          ${
            g.guided_step_id
              ? `<button type="button" class="connect-btn" data-lazy-jump-guided="${esc(g.guided_step_id)}">Open Guided →</button>`
              : ""
          }
        </div>
      </div>`
          )
          .join("")
      : `<p class="caption">No empty personal core fields — nice work.</p>`;
    const est = estGaps.length
      ? `<p class="lazy-tier-label" style="margin-top:16px">Good for Estimates</p>
         <p class="caption">${estGaps.map((g) => esc(g.label || g.field_key)).join(" · ")}</p>`
      : "";
    return `<div class="lazy-gaps">
      <p class="lazy-tier-label">Still needed</p>
      <p class="caption" style="margin-bottom:12px">${esc(gaps.message || "")}</p>
      ${rows}
      ${est}
      <button type="button" class="connect-btn" data-lazy-action="open_guided" style="margin-top:16px">Open Guided →</button>
    </div>`;
  }

  function lazyEstimatesHtml(screen) {
    const llm = screen.llm || {};
    return `<div class="lazy-estimates">
      ${lazyWhereToGetHtml(screen.where_to_get)}
      <p class="caption" style="margin-bottom:12px">${esc(llm.status || "Configure an LLM in settings for estimates.")}</p>
      <div class="lazy-doc-actions" style="flex-wrap:wrap;gap:10px">
        <button type="button" class="connect-btn" data-lazy-action="open_llm_assist">Open AI Estimates step →</button>
        <button type="button" class="connect-btn" data-lazy-action="open_guided">Open Guided →</button>
      </div>
      ${(screen.gaps && screen.gaps.estimate_gap_count)
        ? `<p class="lazy-progress" style="margin-top:16px">${esc(String(screen.gaps.estimate_gap_count))} assumption field(s) still empty</p>`
        : ""}
    </div>`;
  }

  function lazySectionBodyHtml(screen) {
    if (screen.layout === "review") return lazyReviewHtml(screen);
    if (screen.layout === "gaps" || screen.section_id === "wrap_up") {
      return `${lazyWhereToGetHtml(screen.where_to_get)}${lazyGapsHtml(screen)}`;
    }
    if (screen.layout === "ai_estimates" || screen.section_id === "ai_estimates") {
      return lazyEstimatesHtml(screen);
    }
    const docs = screen.documents || [];
    const docBlock = docs.length
      ? `<div class="lazy-checklist">${docs.map(lazyDocRowHtml).join("")}</div>`
      : "";
    const comp = screen.completeness || {};
    const prog = (screen.checklist || {}).core_progress || {};
    const showImport = screen.section_id === "tax_income" || (screen.import_summary && screen.import_summary.has_imports);
    const importBlock = showImport ? lazyImportSummaryHtml(screen.import_summary, { compact: screen.section_id !== "tax_income" }) : "";
    return `${lazyWhereToGetHtml(screen.where_to_get)}
      ${docBlock}
      ${importBlock}
      <p class="lazy-progress">${esc(prog.label || "")} · Plan ${Math.round(comp.percent || 0)}% complete</p>
      <p class="caption loose" data-lazy-status style="min-height:1.2em;margin-top:12px"></p>`;
  }

  function lazyPanelHtml(screen) {
    const opt = screen.optional ? '<span class="optional-tag">optional</span>' : "";
    return `<div class="content-glass liquid liquid-strong question-panel wide">
      <div class="glass-inner question-panel-head">
        <div class="step-row">
          <h1 class="step-title">${esc(screen.title || "Documents")}</h1>
          ${opt}
          <button type="button" class="btn-help" data-help-open>Help</button>
        </div>
        ${screen.intro ? `<div class="step-intro"><p>${esc(screen.intro)}</p></div>` : ""}
      </div>
      <div class="question-panel-scroll">${lazySectionBodyHtml(screen)}</div>
    </div>`;
  }

  function lazyFooterHtml(screen) {
    const f = screen.footer || {};
    const backOff = f.backDisabled || lazyCurrent <= 0;
    const backCls = backOff ? " disabled" : "";
    const skipCls = f.skipProminent ? " btn-skip-prominent" : "";
    const showRun = f.showRun || screen.section_id === "review";
    const stepNum = screen.num || lazyCurrent + 1;
    const total = screen.totalSteps || lazyTotalSteps;
    const saved = savedLabel(appState.last_saved_at);
    const ring = completenessRingHtml(appState.completeness?.percent);
    const skipLabel = screen.skip_label || "Skip";
    const showSkip = screen.optional && !showRun;
    const primary = showRun
      ? `<button type="button" class="btn btn-run" data-lazy-run>Run plan</button>`
      : `<button type="button" class="btn btn-primary" data-lazy-continue>Continue →</button>`;
    return `<div class="chrome-dock liquid chrome-dock-compact">
      <div class="dock-bar">
        <div class="dock-nav">
          <button type="button" class="btn btn-secondary btn-dock${backCls}" data-lazy-back${backCls ? " disabled" : ""}>← Back</button>
          ${showSkip ? `<button type="button" class="btn btn-secondary btn-dock${skipCls}" data-lazy-skip>${esc(skipLabel)}</button>` : ""}
        </div>
        <div class="dock-progress">
          ${ring}
          <span class="dock-step"><strong>${stepNum}</strong><span class="dock-step-total">/${total}</span></span>
          <div class="dots dots-compact">${Array.from({ length: total }, (_, i) =>
            `<span class="dot${i + 1 === stepNum ? " on" : i + 1 < stepNum ? " done" : ""}"></span>`
          ).join("")}</div>
          <span class="dock-journey">${esc(screen.journey || "Documents")}</span>
        </div>
        <div class="dock-primary">${primary}</div>
      </div>
      ${saved ? `<div class="dock-meta"><span class="dock-meta-item save-hint">${esc(saved)}</span></div>` : ""}
    </div>`;
  }

  function showDocumentImportModal(res) {
    const rows = res.rows || [];
    const table = `<p style="margin:0 0 12px;color:var(--secondary);line-height:1.5">${esc(res.message || "")}</p>
      <p style="margin:0 0 12px;font-size:12px;color:var(--muted);line-height:1.45">Values below were <strong>read from your document</strong> (rules + local AI extraction). This is not planning advice — uncheck anything that looks wrong.</p>
      <table class="review-table"><thead><tr><th>Apply</th><th>Field</th><th>Extracted</th><th>Conf.</th><th>From document</th></tr></thead><tbody>${
        rows.map((r, i) => `<tr class="${r.ok ? "" : "error"}"><td><input type="checkbox" data-doc-review-idx="${i}" ${r.apply_default ? "checked" : ""} ${r.ok ? "" : "disabled"} /></td><td>${esc(r.label)}</td><td>${esc(r.suggested)}</td><td>${esc(r.confidence)}</td><td>${esc(r.notes || r.source_quote || "")}</td></tr>`).join("")
      }</tbody></table>`;
    const isTaxReturn = /tax_return|pdf|1040/i.test(String(res.document_type || ""))
      || /1040|tax return/i.test(String(res.document_label || ""));
    const applyLabel = isTaxReturn ? "Replace & apply" : "Apply selected";
    const hint = isTaxReturn
      ? `<p style="margin:0 0 12px;font-size:12px;color:var(--muted);line-height:1.45">This replaces any previous Form 1040 import (history and field values), then applies the selected rows.</p>`
      : "";
    const finish = () => {
      if (typeof res.onDone === "function") res.onDone();
    };
    showModal("Review extracted values", hint + table, [
      { label: "Cancel", onClick: () => { finish(); } },
      { label: applyLabel, primary: true, onClick: async () => {
        const payload = rows.map((r, i) => {
          const cb = document.querySelector(`[data-doc-review-idx="${i}"]`);
          return { ...r, apply: cb && cb.checked };
        });
        const applied = await api().apply_document_import(
          payload,
          res.document_type || "",
          res.document_label || "",
          res.library_entry_id || null
        );
        await showAlert(applied.message || "Applied.");
        await refreshState();
        await renderCurrent();
        finish();
      }},
    ]);
  }

  function showLlmReviewModal(rows) {
    const table = `<table class="review-table"><thead><tr><th>Apply</th><th>Field</th><th>Suggested</th><th>Conf.</th><th>Notes</th></tr></thead><tbody>${
      rows.map((r, i) => `<tr class="${r.ok ? "" : "error"}"><td><input type="checkbox" data-review-idx="${i}" ${r.apply_default ? "checked" : ""} ${r.ok ? "" : "disabled"} /></td><td>${esc(r.label)}</td><td>${esc(r.suggested)}</td><td>${esc(r.confidence)}</td><td>${esc(r.notes)}</td></tr>`).join("")
    }</tbody></table>`;
    showModal("Review AI suggestions", table, [
      { label: "Cancel" },
      { label: "Apply selected", primary: true, onClick: async () => {
        const payload = rows.map((r, i) => {
          const cb = document.querySelector(`[data-review-idx="${i}"]`);
          return { ...r, apply: cb && cb.checked };
        });
        const res = await api().apply_llm_research(payload);
        await showAlert(res.message || "Applied.");
        await refreshState();
        await renderCurrent();
      }},
    ]);
  }

  function showImportReviewModal(rows) {
    const table = `<table class="review-table"><thead><tr><th>Account</th><th>Balance</th><th>Owner</th></tr></thead><tbody>${
      rows.map((r) => `<tr><td>${esc(r.account_name)}</td><td>$${Number(r.balance).toLocaleString()}</td><td>${esc(r.owner)}</td></tr>`).join("")
    }</tbody></table>`;
    showModal("Review CSV import", table, [
      { label: "Cancel" },
      { label: "Apply balances", primary: true, onClick: async () => {
        const res = await api().apply_import_balances(rows);
        await showAlert(res.message || "Applied.");
        await refreshState();
        await renderCurrent();
      }},
    ]);
  }

  function ownerSelectHtml(choices, current) {
    return (choices || []).map((o) =>
      `<option value="${esc(o)}"${o === current ? " selected" : ""}>${esc(o)}</option>`
    ).join("");
  }

  function collectBalanceReviewRows(review) {
    return (review.rows || []).map((row, i) => ({
      account_id: row.account_id,
      institution: row.institution,
      account: row.account,
      balance: row.balance,
      type: row.type,
      subtype: row.subtype,
      bucket: row.bucket,
      owner: document.querySelector(`[data-bal-owner="${i}"]`)?.value ?? row.owner,
      include: !!document.querySelector(`[data-bal-include="${i}"]`)?.checked,
    }));
  }

  function institutionsSectionHtml(review) {
    const institutions = review.institutions || [];
    if (!institutions.length) return "";
    const rows = institutions.map((inst) => {
      const connected = inst.connected_at ? ` · linked ${esc(inst.connected_at.slice(0, 10))}` : "";
      const count = inst.account_count === 1 ? "1 account" : `${inst.account_count} accounts`;
      return `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.08)">
        <div style="min-width:0">
          <div style="font-weight:600">${esc(inst.name)}</div>
          <div style="font-size:12px;color:var(--secondary)">${esc(count)}${connected}</div>
        </div>
        <button type="button" class="btn btn-secondary" data-disc-inst="${esc(inst.item_id)}" data-inst-name="${esc(inst.name)}">Disconnect</button>
      </div>`;
    }).join("");
    const note = review.institutions_note
      ? `<p style="margin:8px 0 0;font-size:12px;color:var(--secondary);line-height:1.45">${esc(review.institutions_note)}</p>`
      : "";
    return `<div style="margin:0 0 16px;padding:12px;border:1px solid rgba(255,255,255,0.1);border-radius:12px;background:rgba(0,0,0,0.15)">
      <p style="margin:0 0 8px;font-weight:600">Connected institutions</p>
      ${rows}
      ${note}
    </div>`;
  }

  function bindInstitutionDisconnectHandlers() {
    document.querySelectorAll("[data-disc-inst]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const itemId = btn.dataset.discInst;
        const name = btn.dataset.instName || "this institution";
        const ok = await showConfirm(
          `Disconnect ${name}? This revokes Plaid access and removes the local token. On a Trial plan, the slot may not free up for a new institution.`,
          "Disconnect institution"
        );
        if (!ok) return;
        const res = await api().disconnect_plaid_item(itemId);
        if (res.review) {
          showBalanceReviewModal(res.review);
          return;
        }
        closeModal();
        await showAlert(res.message || (res.ok ? "Disconnected." : "Failed."));
        if (res.ok) {
          await refreshState();
          await renderCurrent();
        }
      });
    });
  }

  function showBalanceReviewModal(review) {
    const mappingOnly = review.mode === "manage";
    const title = mappingOnly ? "Manage Account Mappings" : "Review Live Balances";
    const choices = review.owner_choices || ["self", "wife", "exclude", "unassigned"];
    const institutionsBlock = mappingOnly ? institutionsSectionHtml(review) : "";
    const bucketHint = review.bucket_hint
      ? `<p style="margin:0 0 10px;font-size:12px;color:var(--muted);line-height:1.45">${esc(review.bucket_hint)}</p>`
      : "";
    const table = `${institutionsBlock}<p style="margin:0 0 12px;color:var(--secondary);line-height:1.5">${esc(review.header || "")}</p>
      ${bucketHint}
      <table class="review-table"><thead><tr><th>Institution</th><th>Account</th><th>Balance</th><th>Role</th><th>Owner</th><th>In assets</th></tr></thead><tbody>${
        (review.rows || []).map((r, i) => `<tr>
          <td>${esc(r.institution)}</td><td>${esc(r.account)}</td>
          <td>$${Number(r.balance).toLocaleString()}</td>
          <td style="font-size:11px;color:var(--muted)">${esc(r.role_label || r.bucket || "—")}</td>
          <td><select class="glass-input" data-bal-owner="${i}">${ownerSelectHtml(choices, r.owner)}</select></td>
          <td><input type="checkbox" data-bal-include="${i}" ${r.include ? "checked" : ""} ${r.bucket === "skip" ? "title=\"Credit/loan — used for spending, not assets\"" : ""} /></td>
        </tr>`).join("")
      }</tbody></table>
      <p class="field-label" style="margin-top:12px" data-bal-totals>${esc(review.totals_label || "")}</p>
      ${mappingOnly ? `<label style="display:flex;align-items:center;gap:8px;margin-top:8px;color:var(--secondary)"><input type="checkbox" data-bal-apply-assets /> Also update Initial Assets (+ taxable / IRA / Roth buckets) from mapped totals</label>` : ""}
      ${review.errors?.length ? `<p style="color:#c90;margin-top:8px">${esc(review.errors.join("; "))}</p>` : ""}`;
    showModal(title, table, [
      { label: "Cancel" },
      { label: mappingOnly ? "Save mappings" : "Apply balances", primary: true, onClick: async () => {
        const rows = collectBalanceReviewRows(review);
        const applyAssets = mappingOnly
          ? !!document.querySelector("[data-bal-apply-assets]")?.checked
          : true;
        const res = await api().apply_balances_review(rows, applyAssets);
        await showAlert(res.message || (res.ok ? "Applied." : "Failed."));
        if (res.ok) { await refreshState(); await renderCurrent(); }
      }},
    ]);
    const updateBalanceTotals = () => {
      const rows = collectBalanceReviewRows(review);
      let self = 0;
      let wife = 0;
      rows.forEach((r) => {
        if (!r.include) return;
        if (r.owner === "self") self += Number(r.balance) || 0;
        if (r.owner === "wife") wife += Number(r.balance) || 0;
      });
      const el = document.querySelector("[data-bal-totals]");
      if (el) el.textContent = `Owner totals — self ${fmtMoney(self)}  wife ${fmtMoney(wife)}`;
    };
    document.querySelectorAll("[data-bal-owner], [data-bal-include]").forEach((el) => {
      el.addEventListener("change", updateBalanceTotals);
    });
    if (mappingOnly) bindInstitutionDisconnectHandlers();
  }

  function collectLineItemRows(review, prefix) {
    return (review.rows || []).map((row, i) => {
      const next = {
        ...row,
        included: !!document.querySelector(`[data-${prefix}-include="${i}"]`)?.checked,
        owner: document.querySelector(`[data-${prefix}-owner="${i}"]`)?.value ?? row.owner,
      };
      if (prefix === "exp") {
        const typeEl = document.querySelector(`[data-exp-type-select="${i}"]`);
        if (typeEl) next.expense_type = typeEl.value === "recurring" ? "recurring" : "variable";
        const catEl = document.querySelector(`[data-exp-category="${i}"]`);
        if (catEl) next.category = catEl.value || row.category;
      }
      return next;
    });
  }

  function showExpenseReviewModal(review) {
    const choices = review.owner_choices || ["self", "wife", "unassigned"];
    const catChoices = review.category_choices || [
      "food", "dining", "entertainment", "other", "utilities", "subscriptions",
      "transportation", "healthcare", "insurance", "education", "travel", "property_tax", "mortgage",
    ];
    const typeSelectHtml = (i, current) => {
      const t = current === "recurring" ? "recurring" : "variable";
      return `<select class="glass-input" data-exp-type-select="${i}">
        <option value="recurring"${t === "recurring" ? " selected" : ""}>Recurring</option>
        <option value="variable"${t === "variable" ? " selected" : ""}>One-off</option>
      </select>`;
    };
    const catSelectHtml = (i, current) => {
      const cur = current || "other";
      const opts = catChoices.map((c) =>
        `<option value="${esc(c)}"${c === cur ? " selected" : ""}>${esc(c)}</option>`
      ).join("");
      return `<select class="glass-input" data-exp-category="${i}">${opts}</select>`;
    };
    const table = `<p style="margin:0 0 12px;color:var(--secondary);line-height:1.5">${esc(review.header || "")}</p>
      <p class="caption" style="margin:0 0 10px"><strong>Base Expenses on Apply</strong> = recurring bills (utilities, insurance, …) + food + dining + entertainment + travel. Ignores <em>other</em>, education, and mortgage even if checked. Categorize lifestyle spend, then Apply.</p>
      <div class="spend-ai-row" style="margin-bottom:10px">
        <button type="button" class="connect-btn" data-exp-ai-categorize="local">Categorize with Local Ollama…</button>
        <button type="button" class="connect-btn" data-exp-ai-categorize="cloud">Categorize with Cloud API…</button>
        <button type="button" class="connect-btn" data-exp-include-base>Include for Base living</button>
        <button type="button" class="connect-btn" data-exp-recurring-only>Include recurring bills only</button>
        <button type="button" class="connect-btn" data-exp-include-all>Check all rows</button>
        <button type="button" class="connect-btn" data-exp-mark-checked-recurring>Mark checked as recurring</button>
        <span class="caption" data-exp-ai-status></span>
      </div>
      <table class="review-table"><thead><tr><th>Include</th><th>Merchant</th><th>Owner</th><th>Category</th><th>Type</th><th>Monthly</th><th>Annual</th></tr></thead><tbody>${
        (review.rows || []).map((r, i) => `<tr class="${r.owner === "unassigned" && r.included ? "error" : ""}" data-exp-row="${i}" data-exp-type="${esc(r.expense_type === "recurring" ? "recurring" : "variable")}">
          <td><input type="checkbox" data-exp-include="${i}" ${r.included ? "checked" : ""} /></td>
          <td>${esc(r.merchant)}</td>
          <td><select class="glass-input" data-exp-owner="${i}">${ownerSelectHtml(choices, r.owner)}</select></td>
          <td>${catSelectHtml(i, r.category)}</td>
          <td>${typeSelectHtml(i, r.expense_type)}</td>
          <td>$${Number(r.monthly_amount).toLocaleString()}</td>
          <td>$${Number(r.annual_amount).toLocaleString()}</td>
        </tr>`).join("")
      }</tbody></table>
      <p class="field-label" style="margin-top:12px">${esc(review.totals_label || "")}</p>
      ${review.unassigned_warning ? `<p style="color:#c90;margin-top:8px">${esc(review.unassigned_warning)}</p>` : ""}`;
    showModal("Review Expenses", table, [
      { label: "Cancel" },
      { label: "Apply expenses", primary: true, onClick: async () => {
        const rows = collectLineItemRows(review, "exp");
        const res = await api().apply_expenses_review(rows);
        await showAlert(res.message || (res.ok ? "Applied." : "Failed."));
        if (res.ok) { await refreshState(); await renderCurrent(); }
      }},
    ]);
    document.querySelectorAll("[data-exp-type-select]").forEach((sel) => {
      sel.addEventListener("change", () => {
        const i = sel.getAttribute("data-exp-type-select");
        const tr = document.querySelector(`tr[data-exp-row="${i}"]`);
        const box = document.querySelector(`[data-exp-include="${i}"]`);
        if (tr) tr.dataset.expType = sel.value;
        if (box) box.checked = sel.value === "recurring";
      });
    });
    document.querySelectorAll("[data-exp-ai-categorize]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!api()?.ai_categorize_expenses) {
          await showAlert("AI categorize is not available in this build. Restart the app.");
          return;
        }
        const backend = btn.dataset.expAiCategorize === "cloud" ? "cloud" : "local";
        const status = document.querySelector("[data-exp-ai-status]");
        if (status) {
          status.textContent =
            backend === "local"
              ? "Using Local Ollama (offline)…"
              : "Using Cloud API (LLM Settings)…";
        }
        showProgress(
          backend === "local"
            ? "Local Ollama categorizing merchants…"
            : "Cloud API categorizing merchants…"
        );
        try {
          const rows = collectLineItemRows(review, "exp");
          const res = await api().ai_categorize_expenses(rows, backend);
          if (!res.ok) {
            await showAlert(res.message || "AI categorize failed.");
            if (status) status.textContent = res.backend_label || "";
            return;
          }
          (res.rows || []).forEach((r, i) => {
            const sel = document.querySelector(`[data-exp-category="${i}"]`);
            if (sel && r.category) sel.value = r.category;
            if (review.rows && review.rows[i]) review.rows[i].category = r.category;
          });
          if (status) status.textContent = res.message || res.backend_label || "Updated.";
        } finally {
          hideProgress();
        }
      });
    });
    const refreshExpenseTotalsHint = () => {
      // Totals label is static from server; Apply recalculates. Status can nudge the user.
      const status = document.querySelector("[data-exp-ai-status]");
      if (status && !status.textContent) {
        status.textContent = "Tip: Apply uses Base living formula — not every checked row.";
      }
    };
    document.querySelector("[data-exp-include-base]")?.addEventListener("click", () => {
      document.querySelectorAll("tr[data-exp-row]").forEach((tr) => {
        const box = tr.querySelector("[data-exp-include]");
        const catSel = tr.querySelector("[data-exp-category]");
        const typeSel = tr.querySelector("[data-exp-type-select]");
        const cat = (catSel?.value || "").toLowerCase();
        const isLifestyle = ["food", "dining", "entertainment", "travel"].includes(cat);
        const isRecurringBill =
          ["utilities", "insurance", "subscriptions", "healthcare", "property_tax", "transportation"].includes(cat) &&
          (typeSel?.value || tr.dataset.expType) === "recurring";
        if (box) box.checked = isLifestyle || isRecurringBill;
      });
      refreshExpenseTotalsHint();
    });
    document.querySelector("[data-exp-recurring-only]")?.addEventListener("click", () => {
      document.querySelectorAll("tr[data-exp-row]").forEach((tr) => {
        const box = tr.querySelector("[data-exp-include]");
        const catSel = tr.querySelector("[data-exp-category]");
        const typeSel = tr.querySelector("[data-exp-type-select]");
        const cat = (catSel?.value || "").toLowerCase();
        const isRecurring = (typeSel?.value || tr.dataset.expType) === "recurring";
        const isBill = ["utilities", "insurance", "subscriptions", "healthcare", "property_tax", "transportation"].includes(cat);
        if (box) box.checked = isRecurring && isBill;
      });
    });
    document.querySelector("[data-exp-include-all]")?.addEventListener("click", () => {
      document.querySelectorAll("[data-exp-include]").forEach((box) => { box.checked = true; });
      const status = document.querySelector("[data-exp-ai-status]");
      if (status) {
        status.textContent =
          "All rows checked — Apply still keeps property tax, healthcare, mortgage, other, and education out of Base Expenses.";
      }
    });
    document.querySelector("[data-exp-mark-checked-recurring]")?.addEventListener("click", () => {
      document.querySelectorAll("tr[data-exp-row]").forEach((tr) => {
        const box = tr.querySelector("[data-exp-include]");
        const typeSel = tr.querySelector("[data-exp-type-select]");
        if (box?.checked && typeSel) {
          typeSel.value = "recurring";
          tr.dataset.expType = "recurring";
        }
      });
    });
  }

  function showContributionReviewModal(review) {
    const choices = review.owner_choices || ["self", "wife", "unassigned"];
    const table = `<p style="margin:0 0 12px;color:var(--secondary);line-height:1.5">${esc(review.header || "")}</p>
      <table class="review-table"><thead><tr><th>Include</th><th>Merchant</th><th>Owner</th><th>Type</th><th>Monthly</th><th>Annual</th></tr></thead><tbody>${
        (review.rows || []).map((r, i) => `<tr class="${r.owner === "unassigned" && r.included ? "error" : ""}">
          <td><input type="checkbox" data-con-include="${i}" ${r.included ? "checked" : ""} /></td>
          <td>${esc(r.merchant)}</td>
          <td><select class="glass-input" data-con-owner="${i}">${ownerSelectHtml(choices, r.owner)}</select></td>
          <td>${esc(r.source_type)}</td>
          <td>$${Number(r.monthly_amount).toLocaleString()}</td>
          <td>$${Number(r.annual_amount).toLocaleString()}</td>
        </tr>`).join("")
      }</tbody></table>
      <p class="field-label" style="margin-top:12px">${esc(review.totals_label || "")}</p>
      ${review.unassigned_warning ? `<p style="color:#c90;margin-top:8px">${esc(review.unassigned_warning)}</p>` : ""}`;
    showModal("Review Contributions", table, [
      { label: "Cancel" },
      { label: "Apply contributions", primary: true, onClick: async () => {
        const rows = collectLineItemRows(review, "con");
        const res = await api().apply_contributions_review(rows);
        await showAlert(res.message || (res.ok ? "Applied." : "Failed."));
        if (res.ok) { await refreshState(); await renderCurrent(); }
      }},
    ]);
  }

  async function showSpendingBuilderModal(person) {
    if (!api()?.get_spending_builder) {
      await showAlert("Spending builder is not available in this build. Restart the app.");
      return;
    }
    const data = await api().get_spending_builder(person || "self");
    if (!data?.ok && data?.categories == null) {
      await showAlert(data?.message || "Could not load spending builder.");
      return;
    }
    const cats = data.categories || [];
    const rows = cats
      .map((c, i) => {
        const src = c.source ? `<span class="spend-src">${esc(c.source)}</span>` : "";
        return `<tr data-spend-id="${esc(c.id)}">
          <td class="spend-label"><strong>${esc(c.label)}</strong><div class="caption">${esc(c.hint || "")}</div>${src}</td>
          <td><input class="glass-input" type="number" min="0" step="1" data-spend-monthly value="${esc(String(Math.round(c.monthly || 0)))}" /></td>
          <td><input class="glass-input" type="number" min="0" step="100" data-spend-annual value="${esc(String(Math.round(c.annual || 0)))}" /></td>
        </tr>`;
      })
      .join("");
    const body = `<p class="missing-intro">Edit typical <strong>ongoing</strong> annual spending for <strong>${esc(
      data.person_label || "You"
    )}</strong> — this becomes Base Expenses (mortgage is separate and stops after payoff). Prefer permanent costs (utilities, property tax, food). Leave out one-offs (car purchase, a single big trip). Re-run <strong>Refresh expenses</strong> to re-seed from Plaid, then tweak here anytime.</p>
      <div class="spend-ai-row">
        <button type="button" class="connect-btn" data-spend-from-expenses-ai="local">Fill food/dining/entertainment — Local Ollama…</button>
        <button type="button" class="connect-btn" data-spend-from-expenses-ai="cloud">Fill food/dining/entertainment — Cloud API…</button>
        <button type="button" class="connect-btn" data-spend-ai>Typical averages — Cloud API…</button>
        <button type="button" class="connect-btn" data-spend-phase-pcts>Suggest Go-Go / Slow-Go % from mix…</button>
        <span class="caption" data-spend-ai-status></span>
      </div>
      ${
        data.phase_suggestion?.ok
          ? `<p class="caption" data-spend-phase-hint style="margin:8px 0 0">Suggested from current mix: Go-Go <strong>${esc(String(data.phase_suggestion.expense_go_go_pct))}%</strong> · Slow-Go <strong>${esc(String(data.phase_suggestion.expense_slow_go_pct))}%</strong> · No-Go <strong>${esc(String(data.phase_suggestion.expense_no_go_pct))}%</strong> (permanent $${Number(data.phase_suggestion.permanent || 0).toLocaleString()} stays; lifestyle $${Number(data.phase_suggestion.lifestyle || 0).toLocaleString()} steps down). Mortgage is separate until payoff.</p>`
          : `<p class="caption" style="margin:8px 0 0">Fill bills + food/dining/entertainment/travel, then suggest Go-Go/Slow-Go %. Mortgage stays outside those % until its payoff year; property tax stays in Base as permanent.</p>`
      }
      <div class="pre-run-scroll">
        <table class="pre-run-table spend-table">
          <thead><tr><th>Category</th><th>Monthly</th><th>Annual</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="spend-mortgage-row">
          <label class="field-label">Mortgage payoff year (optional)</label>
          <input class="glass-input" type="number" min="2020" max="2100" step="1" data-spend-payoff value="${esc(
            data.mortgage_payoff_year || ""
          )}" placeholder="e.g. 2035 — blank if renting / paid off" />
        </div>
        <p class="field-label" style="margin-top:12px" data-spend-totals>
          Base Expenses <strong>$${(data.base_total || 0).toLocaleString()}</strong>/yr
          · Mortgage <strong>$${(data.mortgage_total || 0).toLocaleString()}</strong>/yr
          · Total <strong>$${(data.grand_total || 0).toLocaleString()}</strong>/yr
        </p>
        <p class="caption">Current Base Expenses on plan: $${Number(data.current_base_expenses || 0).toLocaleString()}/yr</p>
      </div>`;
    const baseSpendIds = new Set([
      "housing_rent", "utilities", "property_tax", "food", "dining", "transportation",
      "healthcare", "insurance_other", "entertainment", "travel", "subscriptions",
    ]);
    const syncTotals = () => {
      let base = 0;
      let mortgage = 0;
      document.querySelectorAll("tr[data-spend-id]").forEach((tr) => {
        const id = tr.dataset.spendId;
        const annual = Number(tr.querySelector("[data-spend-annual]")?.value || 0);
        if (id === "mortgage") mortgage += annual;
        else if (baseSpendIds.has(id)) base += annual;
      });
      const el = document.querySelector("[data-spend-totals]");
      if (el) {
        el.innerHTML = `Base Expenses <strong>$${Math.round(base).toLocaleString()}</strong>/yr
          · Mortgage <strong>$${Math.round(mortgage).toLocaleString()}</strong>/yr
          · Total <strong>$${Math.round(base + mortgage).toLocaleString()}</strong>/yr
          <span class="caption">(Base = bills + food/dining/entertainment/travel; ignores other/education)</span>`;
      }
    };
    const bindRowSync = () => {
      document.querySelectorAll("tr[data-spend-id]").forEach((tr) => {
        const monthly = tr.querySelector("[data-spend-monthly]");
        const annual = tr.querySelector("[data-spend-annual]");
        monthly?.addEventListener("input", () => {
          const m = Number(monthly.value || 0);
          annual.value = String(Math.round(m * 12));
          syncTotals();
        });
        annual?.addEventListener("input", () => {
          const a = Number(annual.value || 0);
          monthly.value = String(Math.round(a / 12));
          syncTotals();
        });
      });
    };
    showModal(`Spending builder — ${data.person_label || "You"}`, body, [
      { label: "Cancel" },
      {
        label: "Apply to plan",
        primary: true,
        onClick: async () => {
          const amounts = {};
          document.querySelectorAll("tr[data-spend-id]").forEach((tr) => {
            amounts[tr.dataset.spendId] = Number(tr.querySelector("[data-spend-annual]")?.value || 0);
          });
          const payoff = document.querySelector("[data-spend-payoff]")?.value || "";
          const res = await api().apply_spending_builder(person || "self", amounts, payoff);
          await showAlert(res.message || (res.ok ? "Applied." : "Failed."));
          if (res.ok) {
            await refreshState();
            await renderCurrent();
          }
        },
      },
    ]);
    bindRowSync();
    document.querySelector("[data-spend-phase-pcts]")?.addEventListener("click", async () => {
      if (!api()?.suggest_expense_phase_pcts) {
        await showAlert("Phase % suggestion is not in this build. Restart the app.");
        return;
      }
      const status = document.querySelector("[data-spend-ai-status]");
      const amounts = {};
      document.querySelectorAll("tr[data-spend-id]").forEach((tr) => {
        amounts[tr.dataset.spendId] = Number(tr.querySelector("[data-spend-annual]")?.value || 0);
      });
      // Persist current edits first so suggestion uses on-screen mix
      showProgress("Calculating Go-Go / Slow-Go % from spending mix…");
      try {
        await api().apply_spending_builder(
          person || "self",
          amounts,
          document.querySelector("[data-spend-payoff]")?.value || ""
        );
        const res = await api().suggest_expense_phase_pcts(person || "self");
        if (!res.ok) {
          await showAlert(res.message || "Could not suggest phase percentages.");
          if (status) status.textContent = "";
          return;
        }
        if (status) status.textContent = res.message || "Phase % updated.";
        const hint = document.querySelector("[data-spend-phase-hint]");
        if (hint) {
          hint.innerHTML =
            `Suggested from current mix: Go-Go <strong>${esc(String(res.expense_go_go_pct))}%</strong> · ` +
            `Slow-Go <strong>${esc(String(res.expense_slow_go_pct))}%</strong> · ` +
            `No-Go <strong>${esc(String(res.expense_no_go_pct))}%</strong> ` +
            `(permanent $${Number(res.permanent || 0).toLocaleString()} stays; ` +
            `lifestyle $${Number(res.lifestyle || 0).toLocaleString()} steps down). ` +
            `Mortgage is separate until payoff.`;
        }
        await showAlert(res.message || "Go-Go / Slow-Go / No-Go % updated on the plan.");
      } finally {
        hideProgress();
      }
    });
    document.querySelector("[data-spend-ai]")?.addEventListener("click", async () => {
      const status = document.querySelector("[data-spend-ai-status]");
      if (status) status.textContent = "Using Cloud API (typical averages)…";
      showProgress("Cloud API — typical spending averages…");
      try {
        const res = await api().suggest_spending_averages(person || "self");
        if (!res.ok) {
          await showAlert(res.message || "AI averages failed.");
          if (status) status.textContent = "";
          return;
        }
        const amounts = res.amounts || {};
        document.querySelectorAll("tr[data-spend-id]").forEach((tr) => {
          const id = tr.dataset.spendId;
          if (amounts[id] == null) return;
          const annual = Number(amounts[id] || 0);
          const annualEl = tr.querySelector("[data-spend-annual]");
          const monthlyEl = tr.querySelector("[data-spend-monthly]");
          if (annualEl) annualEl.value = String(Math.round(annual));
          if (monthlyEl) monthlyEl.value = String(Math.round(annual / 12));
          const src = tr.querySelector(".spend-src");
          if (src) src.textContent = "Cloud API · typical average";
          else {
            const label = tr.querySelector(".spend-label");
            if (label) {
              const span = document.createElement("span");
              span.className = "spend-src";
              span.textContent = "Cloud API · typical average";
              label.appendChild(span);
            }
          }
        });
        syncTotals();
        if (status) {
          status.textContent =
            `Cloud API (${res.provider || "?"}/${res.model || "?"}): ${res.message || "Filled."}`;
        }
      } finally {
        hideProgress();
      }
    });
    document.querySelectorAll("[data-spend-from-expenses-ai]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!api()?.fill_spending_from_expenses_ai) {
          await showAlert("This build is missing AI expense fill. Restart the app.");
          return;
        }
        const backend = btn.dataset.spendFromExpensesAi === "cloud" ? "cloud" : "local";
        const status = document.querySelector("[data-spend-ai-status]");
        if (status) {
          status.textContent =
            backend === "local"
              ? "Using Local Ollama (offline)…"
              : "Using Cloud API (LLM Settings)…";
        }
        showProgress(
          backend === "local"
            ? "Local Ollama — categorizing your expenses…"
            : "Cloud API — categorizing your expenses…"
        );
        try {
          const res = await api().fill_spending_from_expenses_ai(person || "self", backend);
          if (!res.ok) {
            await showAlert(res.message || "Could not fill from expenses.");
            if (status) status.textContent = res.backend_label || "";
            return;
          }
          const amounts = res.amounts || {};
          const srcLabel = res.backend_label || (backend === "local" ? "Local Ollama" : "Cloud API");
          document.querySelectorAll("tr[data-spend-id]").forEach((tr) => {
            const id = tr.dataset.spendId;
            if (amounts[id] == null) return;
            const annual = Number(amounts[id] || 0);
            const annualEl = tr.querySelector("[data-spend-annual]");
            const monthlyEl = tr.querySelector("[data-spend-monthly]");
            if (annualEl) annualEl.value = String(Math.round(annual));
            if (monthlyEl) monthlyEl.value = String(Math.round(annual / 12));
            const src = tr.querySelector(".spend-src");
            if (src) src.textContent = srcLabel;
            else {
              const label = tr.querySelector(".spend-label");
              if (label) {
                const span = document.createElement("span");
                span.className = "spend-src";
                span.textContent = srcLabel;
                label.appendChild(span);
              }
            }
          });
          syncTotals();
          if (status) status.textContent = res.message || "Filled from your expenses.";
        } finally {
          hideProgress();
        }
      });
    });
  }

  async function showLlmSettingsModal() {
    const settings = await api().get_llm_settings();
    const body = `<p class="field-label">Provider</p>
      <p class="caption" style="margin:0 0 6px">Use none, openai, anthropic, or xai</p>
      <input class="glass-input" data-set-provider value="${esc(settings.provider || "none")}" style="width:100%;margin-bottom:12px" />
      <p class="field-label">Model</p><input class="glass-input" data-set-model value="${esc(settings.model || "")}" style="width:100%;margin-bottom:12px" />
      <p class="field-label">API key (leave blank to keep)</p><input class="glass-input" data-set-key type="password" placeholder="sk-…" style="width:100%" />
      <p class="caption" style="margin-top:10px"><strong>Cloud API</strong> (this form): AI Estimates / typical regional averages.<br/>
        <strong>Local Ollama</strong> (127.0.0.1): document extract &amp; merchant categorize when you pick Local — no internet.</p>`;
    showModal("LLM Settings", body, [
      { label: "Cancel" },
      { label: "Save", primary: true, onClick: async () => {
        try {
          const provider = document.querySelector("[data-set-provider]")?.value || "none";
          const model = document.querySelector("[data-set-model]")?.value || "";
          const key = document.querySelector("[data-set-key]")?.value || "";
          const res = await api().save_llm_settings(provider, model, key);
          await showAlert(res.message || (res.ok ? "Saved." : "Save failed."));
          if (res.ok) await renderCurrent();
        } catch (e) {
          await showAlert(e.message || String(e) || "Save failed.");
        }
      }},
    ]);
  }

  async function runLlmBatch(root) {
    const prompt = root.querySelector("[data-llm-prompt]")?.value || "";
    await api().persist_llm_prompt(prompt);
    const selections = [];
    llmCheckboxScope(root).querySelectorAll("[data-llm-id]").forEach((cb) => {
      if (!cb.checked) return;
      selections.push({ field_key: cb.dataset.fieldKey, person: cb.dataset.person || null });
    });
    if (!selections.length) {
      await showAlert("Select at least one field.");
      return;
    }
    showProgress("Running AI research…");
    try {
      const res = await api().run_llm_research(selections, prompt);
      if (!res.ok && !res.rows) {
        await showAlert(res.message || "Research failed.");
        return;
      }
      if (res.rows && res.rows.length) showLlmReviewModal(res.rows);
      else await showAlert(res.message || "Done.");
    } finally {
      hideProgress();
    }
  }

  function llmCheckboxScope(root) {
    return root.querySelector(".llm-selection-host") || root;
  }

  function updateLlmFieldSummary(root) {
    const scope = llmCheckboxScope(root);
    const total = scope.querySelectorAll("[data-llm-id]").length;
    const selected = scope.querySelectorAll("[data-llm-id]:checked").length;
    const el = root.querySelector("[data-llm-field-summary]");
    if (el) el.textContent = `${selected} of ${total} fields selected for AI`;
  }

  function syncLlmCheckboxes(fromRoot, toRoot) {
    fromRoot.querySelectorAll("[data-llm-id]").forEach((src) => {
      const key = src.dataset.fieldKey;
      const person = src.dataset.person || "";
      const dst = toRoot.querySelector(`[data-llm-id][data-field-key="${key}"][data-person="${person}"]`);
      if (dst) dst.checked = src.checked;
    });
    const fromMode = fromRoot.querySelector("[data-llm-select-mode] .radio-pill.on")?.dataset.mode || "empty";
    toRoot.querySelectorAll("[data-llm-select-mode] .radio-pill").forEach((pill) => {
      pill.classList.toggle("on", pill.dataset.mode === fromMode);
    });
  }

  function bindLlmSelectMode(root, summaryRoot) {
    bindRadioPills(root, "[data-llm-select-mode] .radio-pill", (pill) => {
      const mode = pill.dataset.mode;
      root.querySelectorAll("[data-llm-id]").forEach((cb) => {
        if (mode === "all") cb.checked = true;
        else if (mode === "none") cb.checked = false;
        else cb.checked = cb.hasAttribute("data-empty");
      });
      const target = summaryRoot || root;
      if (target.querySelector("[data-llm-field-summary]")) updateLlmFieldSummary(target);
    });
  }

  function showLlmFieldsPicker(root) {
    const host = root.querySelector(".llm-selection-host");
    if (!host) return;
    const activeMode = host.querySelector("[data-llm-select-mode] .radio-pill.on")?.dataset.mode || "empty";
    const checklist = host.querySelector(".llm-checklist")?.innerHTML || "";
    showModal(
      "Fields For AI Estimates",
      `<p class="llm-modal-intro">Choose which plan fields AI may suggest values for. You'll review every suggestion before anything updates your plan.</p>
      ${llmSelectToolbarHtml(activeMode)}
      <div class="llm-checklist llm-checklist-modal">${checklist}</div>`,
      [
        { label: "Cancel" },
        {
          label: "Done",
          primary: true,
          onClick: () => {
            const modal = document.getElementById("modal-root");
            syncLlmCheckboxes(modal, host);
            updateLlmFieldSummary(root);
          },
        },
      ]
    );
    const modal = document.getElementById("modal-root");
    syncLlmCheckboxes(host, modal);
    bindLlmSelectMode(modal, root);
  }

  function bindLlmPanel(root) {
    const scope = llmCheckboxScope(root);
    bindLlmSelectMode(scope, root);
    updateLlmFieldSummary(root);
    root.querySelector("[data-llm-fields-open]")?.addEventListener("click", () => showLlmFieldsPicker(root));
    const runBtn = root.querySelector("[data-llm-run]");
    if (runBtn) runBtn.onclick = () => runLlmBatch(root);
    const setBtn = root.querySelector("[data-llm-settings]");
    if (setBtn) setBtn.onclick = () => showLlmSettingsModal();
  }

  function preRunReviewHtml(review) {
    const sections = review?.sections || [];
    if (!sections.length) {
      return `<p class="caption">No inputs to show.</p>`;
    }
    const summary = esc(review.summary || "");
    const blocks = sections
      .map((sec) => {
        const rows = (sec.rows || [])
          .map((r) => {
            const blank = r.blank ? " pre-run-blank" : "";
            const src = r.source && r.source !== "Manual" ? esc(r.source) : "Manual";
            return `<tr class="pre-run-row${blank}" data-pre-run-jump="${esc(r.step_id || sec.step_id || "")}" data-pre-run-field="${esc(r.field_key || "")}" data-pre-run-person="${esc(r.person || "")}">
              <td class="pre-run-label">${esc(r.label || r.field_key)}</td>
              <td class="pre-run-value">${esc(r.value || "—")}</td>
              <td class="pre-run-source">${src}</td>
              <td class="pre-run-go"><button type="button" class="connect-btn lazy-lib-btn" data-pre-run-edit>Edit</button></td>
            </tr>`;
          })
          .join("");
        return `<div class="pre-run-section">
          <div class="pre-run-section-head">
            <strong>${esc(sec.title || sec.id)}</strong>
            <span class="pre-run-section-meta">${esc(String(sec.filled || 0))}/${esc(String(sec.total || 0))} set</span>
          </div>
          <table class="pre-run-table"><thead><tr><th>Input</th><th>Value</th><th>Source</th><th></th></tr></thead><tbody>${rows}</tbody></table>
        </div>`;
      })
      .join("");
    return `<p class="missing-intro">${summary}. Review the master list below. Edit anything that looks wrong, then run.</p>
      <div class="pre-run-scroll">${blocks}</div>`;
  }

  function showPreRunReviewModal(prep) {
    return new Promise((resolve) => {
      const review = prep.review || {};
      const body = preRunReviewHtml(review);
      showModal("Review inputs before run", body, [
        {
          label: "Cancel",
          onClick: () => resolve({ proceed: false }),
        },
        {
          label: "Run plan →",
          primary: true,
          onClick: () => resolve({ proceed: true }),
        },
      ]);
      // Bind edit buttons (modal stays open until jump or cancel/run)
      const panel = document.querySelector("[data-modal-body]");
      panel?.querySelectorAll("[data-pre-run-edit]").forEach((btn) => {
        btn.addEventListener("click", async (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const tr = btn.closest("tr");
          const stepId = tr?.dataset?.preRunJump || "";
          const fieldKey = tr?.dataset?.preRunField || "";
          const person = tr?.dataset?.preRunPerson || null;
          closeModal();
          resolve({ proceed: false, edit: { step_id: stepId, field_key: fieldKey, person } });
        });
      });
    });
  }

  async function jumpToPreRunEdit(edit) {
    if (!edit?.step_id || !api()) return;
    try {
      await setMode("guided");
      const jump = await api().jump_to_guided_step(edit.step_id);
      if (jump && jump.ok) {
        await refreshState();
        await renderCurrent();
        setTimeout(() => {
          const root = document.getElementById("app");
          let el = null;
          if (edit.person) {
            el = root?.querySelector(
              `[data-key="${edit.field_key}"][data-person="${edit.person}"]`
            );
          }
          if (!el) el = root?.querySelector(`[data-key="${edit.field_key}"]`);
          if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            el.focus?.();
            el.classList?.add("field-error-flash");
            setTimeout(() => el.classList?.remove("field-error-flash"), 2500);
          }
        }, 100);
      }
    } catch (e) {
      console.warn("pre-run jump failed", e);
      await showAlert(`Open Guided → ${edit.step_id} to edit ${edit.field_key || "that field"}.`);
    }
  }

  async function runPlanFlow() {
    if (api()) await persistDraft();
    await refreshState();
    const prep = await api().prepare_run_plan();
    if (!prep.ready || prep.blocked) {
      showRunPlanBlockedModal(prep.completeness || appState.completeness);
      return;
    }
    // Master input checklist — review before simulation
    const reviewChoice = await showPreRunReviewModal(prep);
    if (reviewChoice?.edit) {
      await jumpToPreRunEdit(reviewChoice.edit);
      return;
    }
    if (!reviewChoice?.proceed) return;

    if (prep.warnings && prep.warnings.length) {
      const ok = await showConfirm(prep.warnings.join("\n\n"), "Planning approximations");
      if (!ok) return;
    }
    let confirmCpa = true;
    if (prep.needs_cpa_confirm) {
      confirmCpa = await showConfirm(
        `CPA Precision runs slower per path. Consider 1,000 paths or fewer.\n\nContinue with ${prep.num_paths.toLocaleString()} paths?`,
        "CPA Precision + Monte Carlo"
      );
      if (!confirmCpa) return;
    }
    showProgress("Running simulation…");
    try {
      const res = await api().run_plan(confirmCpa);
      if (!res) {
        await showAlert("Run plan failed — no response from app.");
        return;
      }
      if (!res.ok) {
        if (res.blocked && res.completeness) {
          showRunPlanBlockedModal(res.completeness);
          return;
        }
        if (!res.cancelled) await showRunErrorModal(res);
        return;
      }
      if (res.results) showResults(res.results);
      else await showAlert(res.message || "Simulation complete.");
      await refreshState();
    } catch (e) {
      console.error("run_plan failed", e);
      await showAlert("Run plan failed. See console for details.");
    } finally {
      hideProgress();
    }
  }

  function bindInteractions(screen) {
    const root = document.getElementById("app");
    ensureChrome();

    const syncDraftAndCompleteness = async () => {
      if (!api()) return;
      await persistDraft();
      await refreshState();
    };
    let completenessInputTimer = null;
    const queueCompletenessSync = () => {
      clearTimeout(completenessInputTimer);
      completenessInputTimer = setTimeout(() => {
        syncDraftAndCompleteness();
      }, 350);
    };

    bindRadioPills(root, ".radio-row[data-key] .radio-pill", async () => {
      syncConditionalFields(root);
      await syncDraftAndCompleteness();
      if (screen.stepId === "setup") {
        const cap = root.querySelector(".caption");
        if (cap && appState.policy_label) cap.textContent = `Tax tables in use — ${appState.policy_label}`;
      }
    });

    const validateAndSync = async (el) => {
      if (el?.dataset?.key === "current_age") {
        maybeAutoFillBirthYear(root, el);
      }
      await validateControlInput(el);
      await syncDraftAndCompleteness();
    };

    root.querySelectorAll("select[data-key]").forEach((select) => {
      select.addEventListener("change", () => validateAndSync(select));
    });
    root.querySelectorAll("input[data-key]").forEach((input) => {
      if (input.dataset.key === "birth_year") {
        input.addEventListener("input", () => {
          input.dataset.userEdited = "1";
          delete input.dataset.autoFilled;
        });
      }
      input.addEventListener("change", () => validateAndSync(input));
      input.addEventListener("blur", () => validateAndSync(input));
      input.addEventListener("input", () => {
        clearExampleStyle(input);
        if (input.dataset.key === "current_age") {
          maybeAutoFillBirthYear(root, input);
        }
        queueCompletenessSync();
      });
    });
    autoFillBirthYearsFromAges(root);
    syncConditionalFields(root);

    bindRadioPills(root, ".radio-pill[data-sim]", async () => {
      const on = root.querySelector(".radio-pill[data-sim].on");
      if (on && api()) await api().persist_simulation_mode(on.dataset.sim);
      await refreshState();
    });

    root.querySelectorAll(".pill-toggle [data-mode]").forEach((btn) => {
      btn.onclick = () => setMode(btn.dataset.mode);
    });

    root.querySelectorAll("[data-save-plan]").forEach((btn) => {
      btn.addEventListener("click", () => savePlanNow());
    });
    root.querySelectorAll("[data-start-new-plan]").forEach((btn) => {
      btn.addEventListener("click", () => startNewPlan());
    });
    root.querySelectorAll("[data-restart-app]").forEach((btn) => {
      btn.addEventListener("click", () => restartApp());
    });
    root.querySelectorAll("[data-pdf-redactor]").forEach((btn) => {
      btn.addEventListener("click", () => showPdfRedactorModal());
    });

    const helpOpen = root.querySelector("[data-help-open]");
    const panel = root.querySelector("[data-panel]");
    const backdrop = root.querySelector("[data-backdrop]");
    const closeHelp = () => { panel?.classList.remove("open"); backdrop?.classList.remove("open"); };
    if (helpOpen) helpOpen.onclick = () => { panel?.classList.add("open"); backdrop?.classList.add("open"); };
    root.querySelector("[data-help-close]")?.addEventListener("click", closeHelp);
    backdrop?.addEventListener("click", closeHelp);
    root.querySelectorAll("[data-docs-open]").forEach((btn) => {
      btn.addEventListener("click", () => showDocumentsPanelModal(screen.documents_panel));
    });

    root.querySelector("[data-back]:not([disabled])")?.addEventListener("click", async () => {
      const scr = currentScreen;
      if (scr?.microTotal > 1 && (scr.microStep || 1) > 1) {
        await persistDraft();
        if (api()) await api().set_sub_step((scr.microStep || 1) - 2);
        await refreshState();
        await setScreen(current);
        return;
      }
      await goStep(current - 1);
    });
    root.querySelector("[data-continue]")?.addEventListener("click", () => persistAndAdvance().catch((e) => {
      if (e.message !== "validation") console.error(e);
    }));
    root.querySelector("[data-skip]")?.addEventListener("click", async () => {
      if (api()) await api().mark_step_skipped();
      await goStep(current + 1);
    });

    root.querySelector("[data-run]")?.addEventListener("click", async () => {
      try {
        await persistCurrentStep();
        await runPlanFlow();
      } catch (e) {
        if (e.message !== "validation") console.error(e);
      }
    });

    root.querySelector("[data-view-results]")?.addEventListener("click", async () => {
      const res = await api().get_simulation_results();
      if (res.ok && res.results) showResults(res.results);
      else await showAlert(res.message || "No results.");
    });

    root.querySelectorAll("[data-completeness-open]").forEach((btn) => {
      btn.addEventListener("click", () => showMissingModal());
    });
    updateCompletenessChrome();
    root.querySelectorAll("[data-journey-step]").forEach((btn) => {
      btn.addEventListener("click", () => goStep(Number(btn.dataset.journeyStep)));
    });
    root.querySelectorAll("[data-review-jump]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const idx = stepIndexForId(btn.dataset.reviewJump);
        if (idx != null) await goStep(idx);
      });
    });

    const setStatus = (msg) => {
      const el = root.querySelector("[data-action-status]");
      if (el) el.textContent = msg || "";
    };

    root.querySelector("[data-connect-plaid]")?.addEventListener("click", async () => {
      showProgress("Starting Plaid Link…");
      try {
        const res = await api().connect_plaid();
        setStatus(res.message || "");
        if (!res.ok) await showAlert(res.message || "Connect failed.");
        else await showAlert(res.message);
        await refreshState();
        await renderCurrent();
      } finally { hideProgress(); }
    });

    root.querySelector("[data-manage-accounts]")?.addEventListener("click", async () => {
      showProgress("Loading accounts…");
      try {
        const res = await api().refresh_balances_manage();
        if (!res.ok) await showAlert(res.message || "Failed.");
        else if (res.review) showBalanceReviewModal(res.review);
        await refreshState();
        await renderCurrent();
      } finally { hideProgress(); }
    });

    root.querySelector("[data-refresh-balances]")?.addEventListener("click", async () => {
      showProgress("Refreshing balances…");
      try {
        const res = await api().refresh_live_balances();
        if (!res.ok) await showAlert(res.message || "Refresh failed.");
        else if (res.review) showBalanceReviewModal(res.review);
        await refreshState();
      } finally { hideProgress(); }
    });

    root.querySelector("[data-refresh-expenses]")?.addEventListener("click", async () => {
      showProgress("Analyzing expenses…");
      try {
        const res = await api().refresh_expenses_review();
        if (!res.ok) await showAlert(res.message || "Expense refresh failed.");
        else if (res.review) showExpenseReviewModal(res.review);
        await refreshState();
        await renderCurrent();
      } finally { hideProgress(); }
    });

    root.querySelectorAll("[data-spending-builder]").forEach((btn) => {
      btn.addEventListener("click", () =>
        showSpendingBuilderModal(btn.dataset.spendingBuilder || "self")
      );
    });

    root.querySelector("[data-import-balances]")?.addEventListener("click", async () => {
      showProgress("Choose a CSV file…");
      try {
        const res = await api().import_balances();
        if (res.cancelled) return;
        if (!res.ok) { await showAlert(res.message || "Import failed."); return; }
        if (res.rows) showImportReviewModal(res.rows);
      } finally { hideProgress(); }
    });

    root.querySelectorAll("[data-import-document]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        showProgress("Reading document…");
        try {
          const res = await api().import_document();
          setStatus(res.message || "");
          if (res.cancelled) return;
          if (!res.ok) {
            await showAlert(res.message || "Import failed.");
            return;
          }
          if (res.rows) showDocumentImportModal(res);
        } finally {
          hideProgress();
        }
      });
    });

    bindLlmPanel(root);

    /* Expert bindings */
    root.querySelectorAll("[data-expert-nav]").forEach((btn) => {
      btn.onclick = async () => {
        expertSection = btn.dataset.expertNav;
        if (api()) await api().set_expert_section(expertSection);
        await persistExpertDraft();
        await renderExpert(expertSection);
      };
    });

    root.querySelector("[data-jump-guided]")?.addEventListener("click", async () => {
      const stepId = screen.guided_step_id;
      if (stepId && api()) {
        await persistExpertDraft();
        const res = await api().jump_to_guided_step(stepId);
        if (res.ok) {
          expertSearchQuery = "";
          await refreshState();
          await renderCurrent();
          return;
        }
      }
      await setMode("guided");
    });
    root.querySelectorAll("[data-jump-guided-step]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const stepId = btn.dataset.jumpGuidedStep;
        if (!stepId || !api()) return;
        await persistExpertDraft();
        const res = await api().jump_to_guided_step(stepId);
        if (res.ok) {
          expertSearchQuery = "";
          await refreshState();
          await renderCurrent();
        }
      });
    });
    bindExpertSearch(root);
    root.querySelector("[data-expert-run]")?.addEventListener("click", async () => {
      await persistExpertDraft();
      await runPlanFlow();
    });

    bindRadioPills(root, "[data-expert-sim-mode] .radio-pill", async () => {
      await persistExpertSim();
      await refreshState();
    });
    bindRadioPills(root, "[data-expert-precision] .radio-pill", async () => {
      await persistExpertSim();
      await refreshState();
    });

    bindRadioPills(root, "[data-expert-scope-row] .radio-pill", async (pill) => {
      if (!api()) return;
      await persistExpertDraft();
      await api().set_expert_scope(pill.dataset.expertScope);
      await refreshState();
      await renderExpert("assumptions");
    });

    root.querySelectorAll("[data-live-action]").forEach((btn) => {
      btn.onclick = async () => {
        const action = btn.dataset.liveAction;
        if (action === "Connect Accounts") {
          showProgress("Starting Plaid Link…");
          try {
            const res = await api().connect_plaid();
            await showAlert(res.message || (res.ok ? "Opened Plaid." : "Failed."));
          } finally { hideProgress(); }
        } else if (action === "Manage Accounts") {
          showProgress("Loading accounts…");
          try {
            const res = await api().refresh_balances_manage();
            if (!res.ok) await showAlert(res.message || "Failed.");
            else if (res.review) showBalanceReviewModal(res.review);
          } finally { hideProgress(); }
        } else if (action === "Refresh Balances") {
          showProgress("Refreshing balances…");
          try {
            const res = await api().refresh_live_balances();
            if (!res.ok) await showAlert(res.message || "Refresh failed.");
            else if (res.review) showBalanceReviewModal(res.review);
          } finally { hideProgress(); }
        } else if (action === "Import CSV") {
          const res = await api().import_balances();
          if (res.cancelled) return;
          if (!res.ok) { await showAlert(res.message || "Import failed."); return; }
          if (res.rows) showImportReviewModal(res.rows);
        } else if (action === "Refresh Expenses") {
          showProgress("Analyzing expenses…");
          try {
            const res = await api().refresh_expenses_review();
            if (!res.ok) await showAlert(res.message || "Refresh failed.");
            else if (res.review) showExpenseReviewModal(res.review);
          } finally { hideProgress(); }
        } else if (action === "Refresh Contributions") {
          showProgress("Detecting contributions…");
          try {
            const res = await api().refresh_contributions_review();
            if (!res.ok) await showAlert(res.message || "Refresh failed.");
            else if (res.review) showContributionReviewModal(res.review);
          } finally { hideProgress(); }
        } else if (action === "Refresh Market") {
          showProgress("Refreshing market data…");
          try {
            const res = await api().refresh_market_data();
            await showAlert(res.message || (res.ok ? "Market data updated." : "Failed."));
          } finally { hideProgress(); }
        } else {
          await showAlert(`${action} is not available yet.`);
        }
        await refreshState();
        await renderExpert(expertSection);
      };
    });

    root.querySelectorAll("[data-settings-action]").forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.settingsAction;
        if (id === "save_plan") await savePlanNow();
        else if (id === "start_new_plan") await startNewPlan();
        else if (id === "llm") await showLlmSettingsModal();
        else if (id === "export_csv") {
          const pick = await api().pick_export_path();
          if (pick.ok && pick.path) {
            const res = await api().export_assumptions_csv(pick.path);
            await showAlert(res.message || "Exported.");
          }
        } else if (id === "validate_tax") {
          const pick = await api().pick_tax_return_path();
          if (pick.ok && pick.path) {
            const res = await api().validate_tax_return(pick.path);
            await showAlert(res.message || res.title, res.title);
          }
        }
      };
    });
  }

  async function persistExpertSim() {
    const root = document.getElementById("app");
    const modePill = root.querySelector("[data-expert-sim-mode] .radio-pill.on");
    const precPill = root.querySelector("[data-expert-precision] .radio-pill.on");
    const updates = { simulation_mode: modePill?.dataset.value, planning_precision: precPill?.dataset.value };
    root.querySelectorAll("input[data-key], select[data-key]").forEach((el) => {
      if (el.dataset.key) updates[el.dataset.key] = el.value;
    });
    if (api()) await api().persist_expert_simulation(updates);
  }

  async function persistExpertDraft() {
    if (!api()) return;
    const root = document.getElementById("app");
    await persistExpertSim();
    const updates = collectFieldUpdates(root);
    if (updates.length) await api().persist_expert_fields(updates);
    await refreshState();
  }

  async function persistSimulationChoice(root) {
    const on = root?.querySelector(".radio-pill[data-sim].on");
    if (on && api()) await api().persist_simulation_mode(on.dataset.sim);
  }

  async function persistDraft() {
    if (!api()) return;
    const root = document.getElementById("app");
    if (appState.presentation_mode === "expert") return persistExpertDraft();
    await persistSimulationChoice(root);
    await api().persist_step_draft(collectFieldUpdates(root));
    await refreshState();
  }

  async function persistCurrentStep() {
    if (!api()) return;
    const root = document.getElementById("app");
    await persistSimulationChoice(root);
    const updates = collectFieldUpdates(root);
    const res = await api().persist_step_values(updates);
    if (!res.ok) {
      applyFieldErrors(root, res.field_errors);
      const summary = (res.errors || []).join("\n");
      if (summary) await showAlert(summary, "Fix these fields");
      throw new Error("validation");
    }
    clearFieldErrors(root);
    await refreshState();
  }

  async function persistAndAdvance() {
    await persistCurrentStep();
    const screen = currentScreen;
    if (screen?.microTotal > 1 && (screen.microStep || 1) < screen.microTotal) {
      if (api()) await api().set_sub_step(screen.microStep);
      await refreshState();
      await setScreen(current);
      return;
    }
    if (api()) await api().mark_step_complete();
    await goStep(current + 1, { saveDraft: false });
  }

  async function goStep(index, { saveDraft = true } = {}) {
    if (saveDraft && current !== index) await persistDraft();
    if (api()) await api().go_to_step(index);
    await refreshState();
    await setScreen(appState.step_index ?? index);
  }

  async function setMode(mode) {
    if (mode === "lazy") mode = "guided";
    if (api()) await api().set_presentation_mode(mode);
    await refreshState();
    await renderCurrent();
  }

  async function renderGuided(index) {
    currentScreen = await loadScreen(index);
    if (!currentScreen) {
      showBoot(
        `Could not load planning step ${index + 1}. Restart the app, or run from the project folder with: python main.py --web`,
        { error: true }
      );
      return;
    }
    const screen = currentScreen;
    const opt = screen.optional ? '<span class="optional-tag">optional</span>' : "";
    const rail = journeyRailHtml(appState.journey_rail);
    document.getElementById("app").innerHTML = `<div class="window guided-window">
      ${guidedHeaderHtml()}
      <div class="guided-body">
        <nav class="journey-rail liquid"><span class="rail-label">Journey</span>${rail}</nav>
        <main class="guided-main">${guidedPanelHtml(screen, opt)}</main>
      </div>
      ${footerHtml(screen)}
      ${helpHtml(screen)}
    </div>`;
    bindInteractions(screen);
  }

  function bindLazyInteractions(screen) {
    const root = appRoot();
    const setStatus = (msg) => {
      const el = root?.querySelector("[data-lazy-status]");
      if (el) el.textContent = msg || "";
    };
    const defaultPersonFor = (docId) => {
      const on = root?.querySelector(
        `[data-lazy-default-person="${docId}"].on, [data-lazy-default-person="${docId}"][data-person].on`
      );
      return on?.dataset?.person || "self";
    };

    root?.querySelectorAll("[data-lazy-default-person]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const docId = btn.dataset.lazyDefaultPerson;
        root.querySelectorAll(`[data-lazy-default-person="${docId}"]`).forEach((el) => {
          el.classList.toggle("on", el === btn);
        });
      });
    });

    root?.querySelectorAll("[data-lazy-upload]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const docId = btn.dataset.lazyUpload;
        const person = defaultPersonFor(docId);
        showProgress("Adding file(s) to library…");
        try {
          const res = api().library_add_documents
            ? await api().library_add_documents(docId, person)
            : await api().lazy_upload_document(docId, person);
          if (res.cancelled) return;
          if (!res.ok) {
            await showAlert(res.message || "Could not add files.");
            return;
          }
          setStatus(res.message || "Files queued.");
          await refreshState();
          await renderLazy();
        } finally {
          hideProgress();
        }
      });
    });

    const showExtractResult = async (res, entryId) => {
      if (res.ok && res.review_rows?.length) {
        showDocumentImportModal({
          message: res.message,
          rows: res.review_rows,
          document_type: res.document_type,
          document_label: res.document_label,
          library_entry_id: res.library_entry_id || entryId,
        });
      } else if (res.failure && !res.ok) {
        showParserFailureModal(res.failure);
      } else if (!res.ok) {
        await showAlert(res.message || "Extract failed.");
      } else {
        await showAlert(res.message || "Done.");
      }
    };

    const runExtractBatches = async (res) => {
      const batches = res.review_batches || [];
      if (!batches.length) {
        await showAlert(res.message || "Nothing to review.");
        return;
      }
      for (const batch of batches) {
        await new Promise((resolve) => {
          showDocumentImportModal({
            message: batch.message,
            rows: batch.review_rows,
            document_type: batch.document_type,
            document_label: batch.document_label,
            library_entry_id: batch.library_entry_id,
            onDone: resolve,
          });
        });
      }
    };

    root?.querySelectorAll("[data-lazy-extract], [data-lazy-refresh]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const entryId = btn.dataset.lazyRefresh || btn.dataset.lazyExtract;
        if (!entryId) return;
        const refreshFn = api()?.library_refresh_document || api()?.library_extract_document;
        if (!refreshFn) return;
        showProgress("Refreshing document (parser + local AI)…");
        try {
          const res = await refreshFn.call(api(), entryId);
          await showExtractResult(res, entryId);
          await refreshState();
          await renderLazy();
        } finally {
          hideProgress();
        }
      });
    });

    root?.querySelectorAll("[data-lazy-replace]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const entryId = btn.dataset.lazyReplace;
        if (!entryId || !api()?.library_replace_document) return;
        showProgress("Replacing file…");
        try {
          const res = await api().library_replace_document(entryId);
          if (res.cancelled) return;
          if (!res.ok) {
            await showAlert(res.message || "Replace failed.");
            return;
          }
          // Immediately refresh extract on the new file
          if (api()?.library_refresh_document) {
            showProgress("Reading replaced document…");
            const extracted = await api().library_refresh_document(entryId);
            await showExtractResult(extracted, entryId);
          } else {
            await showAlert(res.message || "File replaced.");
          }
          await refreshState();
          await renderLazy();
        } finally {
          hideProgress();
        }
      });
    });

    root?.querySelectorAll("[data-lazy-extract-pending]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const docId = btn.dataset.lazyExtractPending;
        if (!api()?.library_extract_pending) return;
        showProgress("Extracting queued documents…");
        try {
          const res = await api().library_extract_pending(docId);
          await runExtractBatches(res);
          await refreshState();
          await renderLazy();
        } finally {
          hideProgress();
        }
      });
    });

    root?.querySelectorAll("[data-lazy-refresh-all]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const docId = btn.dataset.lazyRefreshAll;
        if (!api()?.library_extract_pending) return;
        showProgress("Refreshing all files for this document type…");
        try {
          const res = await api().library_extract_pending(docId, true);
          await runExtractBatches(res);
          await refreshState();
          await renderLazy();
        } finally {
          hideProgress();
        }
      });
    });

    root?.querySelectorAll("[data-lazy-remove]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const entryId = btn.dataset.lazyRemove;
        if (!entryId || !api()?.library_remove_document) return;
        const res = await api().library_remove_document(entryId);
        if (!res.ok) await showAlert(res.message || "Remove failed.");
        await refreshState();
        await renderLazy();
      });
    });

    root?.querySelectorAll("[data-lib-set-person]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const entryId = btn.dataset.libSetPerson;
        const person = btn.dataset.person;
        if (!entryId || !api()?.library_set_person) return;
        await api().library_set_person(entryId, person);
        await refreshState();
        await renderLazy();
      });
    });
    root?.querySelectorAll("[data-lazy-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.lazyAction;
        if (action === "open_guided") {
          await setMode("guided");
          return;
        }
        if (action === "open_llm_assist") {
          await setMode("guided");
          if (api()) {
            const res = await api().jump_to_guided_step("llm_assist");
            if (res && res.ok) {
              await refreshState();
              await renderCurrent();
            }
          }
          return;
        }
        if (action === "connect_plaid") {
          showProgress("Starting Plaid Link…");
          try {
            const res = await api().connect_plaid();
            setStatus(res.message || "");
            await showAlert(res.message || (res.ok ? "Opened Plaid." : "Failed."));
            await refreshState();
            await renderLazy();
          } finally { hideProgress(); }
          return;
        }
        if (action === "manage_accounts") {
          showProgress("Loading connected institutions…");
          try {
            const res = await api().refresh_balances_manage();
            if (!res.ok) await showAlert(res.message || "Failed.");
            else if (res.review) showBalanceReviewModal(res.review);
            await refreshState();
            await renderLazy();
          } finally { hideProgress(); }
          return;
        }
        if (action === "refresh_balances") {
          showProgress("Refreshing balances…");
          try {
            const res = await api().refresh_live_balances();
            if (!res.ok) await showAlert(res.message || "Failed.");
            else if (res.review) showBalanceReviewModal(res.review);
            await refreshState();
            await renderLazy();
          } finally { hideProgress(); }
          return;
        }
        if (action === "refresh_expenses") {
          showProgress("Analyzing expenses…");
          try {
            const res = await api().refresh_expenses_review();
            if (!res.ok) await showAlert(res.message || "Failed.");
            else if (res.review) showExpenseReviewModal(res.review);
            await refreshState();
            await renderLazy();
          } finally { hideProgress(); }
          return;
        }
        if (action === "refresh_market") {
          showProgress("Refreshing market data…");
          try {
            const res = await api().refresh_market_data();
            await showAlert(res.message || (res.ok ? "Market updated." : "Failed."));
            await refreshState();
            await renderLazy();
          } finally { hideProgress(); }
        }
      });
    });
    root?.querySelectorAll("[data-spending-builder]").forEach((btn) => {
      btn.addEventListener("click", () =>
        showSpendingBuilderModal(btn.dataset.spendingBuilder || "self")
      );
    });
    const panel = root?.querySelector("[data-panel]");
    const backdrop = root?.querySelector("[data-backdrop]");
    const closeHelp = () => { panel?.classList.remove("open"); backdrop?.classList.remove("open"); };
    root?.querySelector("[data-help-open]")?.addEventListener("click", () => {
      panel?.classList.add("open");
      backdrop?.classList.add("open");
    });
    root?.querySelector("[data-help-close]")?.addEventListener("click", closeHelp);
    backdrop?.addEventListener("click", closeHelp);
    root?.querySelectorAll("[data-save-plan]").forEach((btn) => {
      btn.addEventListener("click", () => savePlanNow());
    });
    root?.querySelectorAll("[data-start-new-plan]").forEach((btn) => {
      btn.addEventListener("click", () => startNewPlan());
    });
    root?.querySelectorAll("[data-restart-app]").forEach((btn) => {
      btn.addEventListener("click", () => restartApp());
    });
    root?.querySelectorAll("[data-pdf-redactor]").forEach((btn) => {
      btn.addEventListener("click", () => showPdfRedactorModal());
    });

    root.querySelectorAll(".pill-toggle [data-mode]").forEach((btn) => {
      btn.onclick = () => setMode(btn.dataset.mode);
    });

    root.querySelector("[data-lazy-back]:not([disabled])")?.addEventListener("click", async () => {
      await goLazyStep(lazyCurrent - 1);
    });
    root.querySelector("[data-lazy-continue]")?.addEventListener("click", async () => {
      if (api()) await api().advance_lazy_step();
      await refreshState();
      await renderLazy();
    });
    root.querySelector("[data-lazy-skip]")?.addEventListener("click", async () => {
      if (api()) await api().skip_lazy_section();
      await refreshState();
      await renderLazy();
    });
    root.querySelector("[data-lazy-run]")?.addEventListener("click", async () => {
      try {
        await runPlanFlow();
      } catch (e) {
        if (e.message !== "validation") console.error(e);
      }
    });

    root.querySelectorAll("[data-lazy-journey-step]").forEach((btn) => {
      btn.addEventListener("click", () => goLazyStep(Number(btn.dataset.lazyJourneyStep)));
    });

    const sectionIndex = (sectionId) => {
      for (const block of appState.journey_rail || []) {
        for (const step of block.steps || []) {
          if (step.section_id === sectionId) return step.index;
        }
      }
      return null;
    };
    root.querySelectorAll("[data-lazy-review-jump]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const idx = sectionIndex(btn.dataset.lazyReviewJump);
        if (idx != null) await goLazyStep(idx);
      });
    });
    root.querySelectorAll("[data-lazy-jump-guided]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const stepId = btn.dataset.lazyJumpGuided;
        if (!stepId || !api()) return;
        await setMode("guided");
        const res = await api().jump_to_guided_step(stepId);
        if (res && res.ok) {
          await refreshState();
          await renderCurrent();
        }
      });
    });

    root.querySelectorAll("[data-completeness-open]").forEach((btn) => {
      btn.addEventListener("click", () => showMissingModal());
    });
    updateCompletenessChrome();
  }

  async function goLazyStep(index) {
    const total = lazyTotalSteps || 10;
    const next = Math.max(0, Math.min(index, total - 1));
    if (api()) await api().go_to_lazy_step(next);
    await refreshState();
    await renderLazy();
  }

  async function renderLazy() {
    const screen = api()
      ? await (api().get_ai_screen
          ? api().get_ai_screen(lazyCurrent)
          : api().get_lazy_screen(lazyCurrent))
      : null;
    if (!screen) {
      showBoot("Could not load AI Mode.", { error: true });
      return;
    }
    currentScreen = screen;
    lazyCurrent = screen.step_index ?? lazyCurrent;
    lazyTotalSteps = screen.totalSteps ?? lazyTotalSteps;
    const rail = lazyJourneyRailHtml(appState.journey_rail);
    document.getElementById("app").innerHTML = `<div class="window guided-window lazy-window">
      ${appHeaderHtml("ai")}
      <div class="guided-body">
        <nav class="journey-rail liquid"><span class="rail-label">AI Mode</span>${rail}</nav>
        <main class="guided-main">${lazyPanelHtml(screen)}</main>
      </div>
      ${lazyFooterHtml(screen)}
      ${helpHtml(screen)}
    </div>`;
    bindLazyInteractions(screen);
  }

  async function renderExpert(section) {
    const nav = api() ? await api().get_expert_nav() : [];
    const screen = await loadExpertScreen(section);
    if (!screen) {
      showBoot("Could not load the expert editor. Restart the app and try again.", { error: true });
      return;
    }
    document.getElementById("app").innerHTML = expertShellHtml(screen, nav);
    bindInteractions(screen);
  }

  async function renderCurrent() {
    const mode = appState.presentation_mode;
    if (mode === "expert") await renderExpert(expertSection);
    else if (mode === "ai" || mode === "lazy") await renderLazy();
    else await renderGuided(current);
  }

  async function setScreen(index) {
    current = Math.max(0, Math.min(index, totalSteps - 1));
    await renderGuided(current);
  }

  async function init() {
    try {
      ensureChrome();
      const ready = await waitForApi();
      if (!ready || !api()) {
        showBoot(
          "Could not connect to the app backend. Launch with python main.py --web from the Retirement_Financial_Modeling folder.",
          { error: true }
        );
        return;
      }
      await refreshState();
      if (!appState || appState.step_index == null) {
        showBoot("Could not load session state from the app backend.", { error: true });
        return;
      }
      await renderCurrent();
      if (!appRoot()?.querySelector(".window")) {
        showBoot("The planning UI did not render. Restart the app and try again.", { error: true });
      }
    } catch (e) {
      console.error("init failed", e);
      showBoot(`Startup error: ${e.message || e}`, { error: true });
    }
  }

  init();
})();