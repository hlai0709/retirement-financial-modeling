# gui.py - GUI components for the retirement model

import json
import subprocess
import sys
import tkinter as tk
import urllib.request
from datetime import date
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import FuncFormatter, MaxNLocator

from config.assumptions_report import build_assumptions_dataframe
from config.field_values import format_field_value, parse_field_value
from config.model_fields import (
    FIELDS_BY_KEY,
    PERSON_FIELDS,
    SHARED_FIELDS,
    ModelField,
    default_display_value,
    field_choice_values,
    field_with_model_role,
    fields_with_data_source,
)
from services.data_service import (
    OWNER_ATTRIBUTION_CHOICES,
    VALID_OWNERS,
    ContributionSnapshot,
    DataService,
    ExpenseSnapshot,
    MarketSnapshot,
    RefreshSnapshot,
)
from providers.csv_provider import CsvBalanceRow
from adviser.advisor_engine import WhatIfChange, analyze
from config.policy.live import ensure_active_policy
from config.policy.tables import active_policy_summary
from model import collect_param_warnings
from simulation.monte_carlo import SimulationOutput, run_simulation
from validation.tax_return_validator import validate_tax_return_file
from providers.plaid_provider import HoldingSnapshot
from providers.assumption_research import AssumptionResearchContext, AssumptionSuggestion, research_assumption
from providers.llm.factory import default_model
from providers.llm.models_catalog import load_model_catalog, refresh_model_catalog
from app.core.presentation.live_data_ops import (
    disconnect_plaid_institution,
    serialize_connected_institutions,
)
from security.keychain import list_item_ids
from security.link_server import open_in_chrome, stop_stale_servers
from security.llm_keychain import delete_api_key, get_api_key, has_api_key, store_api_key
from settings import LLMSettings, PROJECT_ROOT, get_settings, link_server_info_path, load_llm_settings, save_llm_settings


def clean_key(label):
    """Clean label to key: lower, replace space/specials, strip trailing _."""
    key = label.lower().replace(" ", "_").replace("&", "").replace("(", "").replace(")", "").replace("%", "")
    return key.strip("_")


FieldWidget = ttk.Entry | ttk.Combobox


def _widget_get_text(widget: FieldWidget) -> str:
    return widget.get().strip()


def _widget_set_text(widget: FieldWidget, text: str) -> None:
    if isinstance(widget, ttk.Combobox):
        widget.set(text)
        return
    widget.delete(0, tk.END)
    widget.insert(0, text)


def _make_field_widget(parent, field: ModelField, *, person: str | None) -> FieldWidget:
    display = default_display_value(field, person=person)
    choices = field_choice_values(field)
    if choices:
        widget = ttk.Combobox(
            parent,
            values=list(choices),
            state="readonly",
            width=28,
        )
        widget.set(display)
        return widget
    entry = ttk.Entry(parent)
    entry.insert(0, display)
    return entry


def _field_row_widgets(
    parent,
    field: ModelField,
    row: int,
    *,
    person: str | None,
    entries: dict,
    source_labels: dict,
    on_research,
) -> int:
    """Render one catalog field row; returns next available row index."""
    parent.columnconfigure(1, weight=1)

    ttk.Label(parent, text=field.label + ":").grid(row=row, column=0, sticky="w")
    widget = _make_field_widget(parent, field, person=person)
    widget.grid(row=row, column=1, sticky="ew")
    entries[field.key] = widget

    col = 2
    source_var = tk.StringVar(value="Manual")
    source_labels[(person, field.key)] = source_var

    if field.data_source == "market":
        ttk.Label(parent, textvariable=source_var, foreground="#666", font=("", 10)).grid(
            row=row, column=col, sticky="w", padx=(8, 0)
        )
        col += 1
    elif field.data_source == "plaid":
        ttk.Label(parent, textvariable=source_var, foreground="#666", font=("", 10)).grid(
            row=row, column=col, sticky="w", padx=(8, 0)
        )
        col += 1
    elif field.supports_llm_research():
        ttk.Label(parent, textvariable=source_var, foreground="#666", font=("", 9)).grid(
            row=row, column=col, sticky="w", padx=(4, 0)
        )
        col += 1
        ttk.Button(
            parent,
            text="Research",
            command=lambda f=field, p=person: on_research(f, p),
        ).grid(row=row, column=col, padx=(4, 0))

    return row + 1


def _format_cell(value):
    """Format DataFrame values for display in the Treeview."""
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:,.2f}"
    return str(value)


def _format_dollar_amount(value):
    """Format a dollar amount with an explicit scale suffix (K, M, B)."""
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f} billion"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:,.2f} million"
    if abs_value >= 1_000:
        return f"${value / 1_000:,.0f} thousand"
    return f"${value:,.0f}"


def _dollar_axis_formatter(value, _pos):
    """Compact tick labels for chart axes."""
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def _bind_mousewheel(canvas, widget):
    """Enable vertical scrolling with mouse wheel / trackpad."""

    def _on_mousewheel(event):
        if event.num == 5:
            canvas.yview_scroll(1, "units")
        elif event.num == 4:
            canvas.yview_scroll(-1, "units")
        elif event.delta:
            step = int(-event.delta / 120)
            if step == 0:
                step = -1 if event.delta > 0 else 1
            canvas.yview_scroll(step, "units")

    widget.bind_all("<MouseWheel>", _on_mousewheel)
    widget.bind_all("<Button-4>", _on_mousewheel)
    widget.bind_all("<Button-5>", _on_mousewheel)


def _create_scrollable_frame(parent):
    """Return a canvas and inner frame; scrollbar only when content overflows."""
    container = ttk.Frame(parent)
    container.grid(row=0, column=0, sticky="nsew")
    container.columnconfigure(0, weight=1)
    container.rowconfigure(0, weight=1)

    canvas = tk.Canvas(container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)

    canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    def _sync_scrollbar() -> None:
        canvas.update_idletasks()
        bbox = canvas.bbox("all")
        view_h = canvas.winfo_height()
        if not bbox or view_h <= 1:
            scrollbar.grid_remove()
            canvas.yview_moveto(0)
            return
        content_h = bbox[3] - bbox[1]
        if content_h <= view_h + 2:
            scrollbar.grid_remove()
            canvas.yview_moveto(0)
        else:
            scrollbar.grid(row=0, column=1, sticky="ns")

    def _on_frame_configure(_event):
        canvas.configure(scrollregion=canvas.bbox("all"))
        _sync_scrollbar()

    def _on_canvas_configure(event):
        canvas.itemconfig(canvas_window, width=event.width)
        _sync_scrollbar()

    scrollable_frame.bind("<Configure>", _on_frame_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    _bind_mousewheel(canvas, canvas)

    return canvas, scrollable_frame


def _configure_assets_chart(ax, df, title):
    """Build an assets-over-time chart with readable dollar axis labels."""
    assets = df['Assets_End']
    ax.plot(df['Year'], assets, label='Portfolio Value', linewidth=2)
    ax.set_xlabel('Year')
    ax.set_ylabel('Portfolio Value (U.S. Dollars)')
    ax.set_title(title)
    ax.yaxis.set_major_formatter(FuncFormatter(_dollar_axis_formatter))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=8, prune=None))
    ax.grid(True, alpha=0.3)

    start_value = assets.iloc[0]
    end_value = assets.iloc[-1]
    ax.annotate(
        f"Start: {_format_dollar_amount(start_value)}\n"
        f"End: {_format_dollar_amount(end_value)}",
        xy=(0.02, 0.98),
        xycoords='axes fraction',
        va='top',
        ha='left',
        fontsize=9,
        bbox={'boxstyle': 'round,pad=0.4', 'facecolor': 'white', 'alpha': 0.85},
    )
    ax.legend(loc='upper right')


def _configure_fan_chart(ax, percentile_df, title, deterministic_df=None):
    """Build a Monte Carlo fan chart with 10th/50th/90th percentile bands."""
    years = percentile_df['Year']
    p10 = percentile_df['P10']
    p50 = percentile_df['P50']
    p90 = percentile_df['P90']

    ax.fill_between(years, p10, p90, alpha=0.25, color='#4c78a8', label='10th–90th percentile')
    ax.plot(years, p50, color='#1f4e79', linewidth=2, label='Median (50th)')
    if deterministic_df is not None:
        ax.plot(
            deterministic_df['Year'],
            deterministic_df['Assets_End'],
            linestyle='--',
            color='#d95f02',
            linewidth=2,
            label='Deterministic',
        )

    ax.set_xlabel('Year')
    ax.set_ylabel('Portfolio Value (U.S. Dollars)')
    ax.set_title(title)
    ax.yaxis.set_major_formatter(FuncFormatter(_dollar_axis_formatter))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=8, prune=None))
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper right')


class ResultsWindow(tk.Toplevel):
    """Separate window for simulation results (table + graph)."""

    def __init__(
        self,
        parent,
        output: SimulationOutput,
        *,
        params: dict | None = None,
        risk_tolerance: str = "moderate",
        holdings: list[HoldingSnapshot] | None = None,
        on_apply_what_if=None,
    ):
        super().__init__(parent)
        self.title("Simulation Results")
        self.geometry("1100x750")
        self.minsize(900, 600)
        self._figures = []
        self._on_apply_what_if = on_apply_what_if

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        if params is not None:
            self._add_adviser_tab(
                notebook,
                params=params,
                output=output,
                risk_tolerance=risk_tolerance,
                holdings=holdings,
            )

        tab_keys = sorted(
            set(output.deterministic.keys()) | set(output.monte_carlo.keys()),
            key=lambda name: (name != "joint", name),
        )
        for key in tab_keys:
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=key.capitalize())
            tab.columnconfigure(0, weight=1)

            row = 0
            mc_result = output.monte_carlo.get(key)
            det_df = output.deterministic.get(key)

            if mc_result is not None:
                summary = ttk.LabelFrame(tab, text="Monte Carlo Summary")
                summary.grid(row=row, column=0, sticky="ew", padx=5, pady=5)
                row += 1
                ttk.Label(
                    summary,
                    text=mc_result.summary_text(),
                    justify="left",
                ).grid(row=0, column=0, sticky="w", padx=8, pady=6)
                ttk.Label(
                    summary,
                    text=(
                        f"Returns: {mc_result.return_model}  |  "
                        f"Inflation: {mc_result.inflation_model}  |  "
                        f"Sampling: {mc_result.sampling_model}"
                    ),
                    foreground="#555",
                ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))
                if mc_result.longevity_model or mc_result.healthcare_model:
                    ttk.Label(
                        summary,
                        text=(
                            f"Longevity: {mc_result.longevity_model or 'n/a'}  |  "
                            f"Healthcare: {mc_result.healthcare_model or 'n/a'}"
                        ),
                        foreground="#555",
                    ).grid(row=2, column=0, sticky="w", padx=8, pady=(0, 6))
                if mc_result.errors:
                    ttk.Label(
                        summary,
                        text="Warnings: " + "; ".join(mc_result.errors),
                        foreground="#8a5a00",
                        wraplength=980,
                    ).grid(row=3, column=0, sticky="w", padx=8, pady=(0, 6))

                graph_frame = ttk.Frame(tab)
                graph_frame.grid(row=row, column=0, sticky="nsew", padx=5, pady=5)
                tab.rowconfigure(row, weight=2)
                row += 1

                fig, ax = plt.subplots(figsize=(9, 4))
                title = f"{key.capitalize()} Monte Carlo"
                if output.mode == "both":
                    title += " vs Deterministic"
                _configure_fan_chart(
                    ax,
                    mc_result.percentile_paths,
                    title,
                    deterministic_df=det_df if output.mode == "both" else None,
                )
                fig.tight_layout()
                self._figures.append(fig)
                canvas = FigureCanvasTkAgg(fig, master=graph_frame)
                canvas.draw()
                canvas.get_tk_widget().pack(fill='both', expand=True)

                mc_tree_frame = ttk.LabelFrame(tab, text="Monte Carlo Percentile Paths")
                mc_tree_frame.grid(row=row, column=0, sticky="nsew", padx=5, pady=5)
                tab.rowconfigure(row, weight=1)
                row += 1
                self._add_table(
                    mc_tree_frame,
                    mc_result.percentile_paths,
                    numeric_cols={"P10", "P50", "P90"},
                )
                ttk.Button(
                    tab,
                    text="Export Monte Carlo to CSV",
                    command=lambda d=mc_result.percentile_paths.copy(), k=key: self._export_csv(
                        d, f"{k}_monte_carlo"
                    ),
                ).grid(row=row, column=0, pady=5)
                row += 1

            if det_df is not None:
                det_tree_frame = ttk.LabelFrame(
                    tab,
                    text="Deterministic Projection" if mc_result is not None else "Projection Table",
                )
                det_tree_frame.grid(row=row, column=0, sticky="nsew", padx=5, pady=5)
                tab.rowconfigure(row, weight=2)
                row += 1
                self._add_table(det_tree_frame, det_df)

                if mc_result is None:
                    graph_frame = ttk.Frame(tab)
                    graph_frame.grid(row=row, column=0, sticky="nsew", padx=5, pady=5)
                    tab.rowconfigure(row, weight=2)
                    row += 1
                    fig, ax = plt.subplots(figsize=(9, 4))
                    _configure_assets_chart(ax, det_df, f'{key.capitalize()} Projection')
                    fig.tight_layout()
                    self._figures.append(fig)
                    canvas = FigureCanvasTkAgg(fig, master=graph_frame)
                    canvas.draw()
                    canvas.get_tk_widget().pack(fill='both', expand=True)

                ttk.Button(
                    tab,
                    text="Export Deterministic to CSV",
                    command=lambda d=det_df.copy(), k=key: self._export_csv(d, f"{k}_deterministic"),
                ).grid(row=row, column=0, pady=5)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _add_adviser_tab(
        self,
        notebook: ttk.Notebook,
        *,
        params: dict,
        output: SimulationOutput,
        risk_tolerance: str,
        holdings: list[HoldingSnapshot] | None,
    ) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Adviser")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        self.config(cursor="watch")
        self.update_idletasks()
        try:
            report = analyze(
                params,
                output,
                holdings=holdings,
                risk_tolerance=risk_tolerance,  # type: ignore[arg-type]
            )
        except Exception as exc:
            report = None
            error_text = str(exc)
        finally:
            self.config(cursor="")

        if report is None:
            ttk.Label(tab, text=f"Adviser analysis failed: {error_text}", foreground="#a00").grid(
                row=0, column=0, sticky="w", padx=12, pady=12
            )
            return

        goal_frame = ttk.LabelFrame(tab, text="Goal Status")
        goal_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        goal_frame.columnconfigure(0, weight=1)
        ttk.Label(goal_frame, text=report.goal_status, wraplength=980, justify="left").grid(
            row=0, column=0, sticky="w", padx=10, pady=8
        )
        if report.holdings_summary:
            ttk.Label(
                goal_frame,
                text=report.holdings_summary,
                foreground="#555",
                wraplength=980,
            ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

        rec_frame = ttk.LabelFrame(tab, text="Recommendations")
        rec_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        rec_frame.columnconfigure(0, weight=1)
        rec_frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(rec_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(rec_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        scrollbar.grid(row=0, column=1, sticky="ns")

        if not report.recommendations:
            ttk.Label(
                inner,
                text="No specific recommendations — your plan metrics look healthy.",
                wraplength=900,
            ).pack(anchor="w", padx=8, pady=8)
        else:
            for index, rec in enumerate(report.recommendations[:3], start=1):
                block = ttk.Frame(inner)
                block.pack(fill="x", padx=8, pady=8)
                ttk.Label(
                    block,
                    text=f"{index}. {rec.title}",
                    font=("", 11, "bold"),
                    wraplength=900,
                ).pack(anchor="w")
                ttk.Label(block, text=rec.detail, wraplength=900, justify="left").pack(
                    anchor="w", pady=(4, 0)
                )
                if rec.what_if and self._on_apply_what_if:
                    ttk.Button(
                        block,
                        text="Apply what-if",
                        command=lambda changes=rec.what_if: self._on_apply_what_if(changes),
                    ).pack(anchor="w", pady=(6, 0))

        checklist_frame = ttk.LabelFrame(tab, text="Action Checklist")
        checklist_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=6)
        for item in report.checklist:
            ttk.Label(checklist_frame, text=f"• {item}", wraplength=980).pack(
                anchor="w", padx=10, pady=2
            )

        ttk.Label(
            tab,
            text=report.disclaimer,
            foreground="#8a5a00",
            wraplength=980,
            justify="left",
        ).grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 12))

    def _add_table(self, parent, df, *, numeric_cols: set[str] | None = None):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        columns = list(df.columns)
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=8)
        numeric_cols = numeric_cols or set()
        for col in columns:
            tree.heading(col, text=col)
            anchor = 'e' if col in numeric_cols or col.endswith('_End') or col == 'Age' else 'w'
            width = 120 if col == 'Formula_Notes' else 100
            tree.column(col, width=width, anchor=anchor)
        for _, row in df.iterrows():
            tree.insert("", "end", values=[_format_cell(v) for v in row])

        y_scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        x_scrollbar = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        x_scrollbar.grid(row=1, column=0, sticky="ew")

    def _export_csv(self, df, key):
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"{key}_projection.csv",
            parent=self,
        )
        if filename:
            df.to_csv(filename, index=False)
            messagebox.showinfo("Export Complete", f"Saved to {filename}", parent=self)

    def _on_close(self):
        for fig in self._figures:
            plt.close(fig)
        self._figures.clear()
        self.destroy()


def _format_balance(amount: float, currency: str = "USD") -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def _count_unassigned_line_items(rows: list[dict]) -> int:
    return sum(
        1
        for row in rows
        if row["include_var"].get() and row["owner_var"].get() == "unassigned"
    )


def _line_item_owner_totals(rows: list[dict], mode: str) -> dict[str, float]:
    totals = {"self": 0.0, "wife": 0.0}
    assigned = False
    included_annual = 0.0
    for row in rows:
        if not row["include_var"].get():
            continue
        item = row["item"]
        included_annual += item.annual_amount
        owner = row["owner_var"].get()
        if owner in totals:
            totals[owner] += item.annual_amount
            assigned = True
    if mode == "joint" and not assigned:
        half = included_annual / 2
        return {"self": half, "wife": half}
    return totals


class LiveDataReviewDialog(tk.Toplevel):
    """Modal review of live Plaid balances before updating form fields."""

    def __init__(
        self,
        parent,
        snapshot: RefreshSnapshot,
        data_service: DataService,
        *,
        mode: str = "refresh",
    ):
        super().__init__(parent)
        self.mode = mode
        mapping_only = mode == "manage"
        self.title("Manage Account Mappings" if mapping_only else "Review Live Data")
        self.geometry("820x520" if mapping_only else "820x480")
        self.minsize(700, 360)
        self.transient(parent)
        self.grab_set()

        self.snapshot = snapshot
        self.data_service = data_service
        self.applied = False
        self.apply_assets = False
        self._rows: list[dict] = []

        if mapping_only:
            if snapshot.as_of:
                header_text = (
                    "Assign each account to self, wife, or exclude. "
                    f"Balances shown are from the last refresh ({snapshot.as_of[:19]}). "
                    "Use Refresh Live Data for current amounts."
                )
            else:
                header_text = (
                    "Assign each account to self, wife, or exclude. "
                    "Balances are not cached yet — refresh live data after mapping "
                    "to load current amounts."
                )
        else:
            header_text = (
                "Confirm balances and owner mapping before updating Initial Assets."
            )
        header = ttk.Label(self, text=header_text, wraplength=760)
        header.pack(padx=12, pady=(12, 8), anchor="w")

        if mapping_only:
            inst_frame = ttk.LabelFrame(self, text="Connected institutions")
            inst_frame.pack(fill="x", padx=12, pady=(0, 8))
            for inst in serialize_connected_institutions(data_service, snapshot):
                row = ttk.Frame(inst_frame)
                row.pack(fill="x", padx=8, pady=4)
                meta = f"{inst['account_count']} account(s)"
                if inst.get("connected_at"):
                    meta += f" · linked {inst['connected_at'][:10]}"
                ttk.Label(row, text=f"{inst['name']}  ({meta})").pack(side="left")
                ttk.Button(
                    row,
                    text="Disconnect",
                    command=lambda iid=inst["item_id"]: self._disconnect_institution(iid),
                ).pack(side="right")
            ttk.Label(
                inst_frame,
                text=(
                    "Disconnect revokes Plaid access and removes local tokens. "
                    "On a Trial plan, disconnected slots may not free up for new links."
                ),
                wraplength=740,
            ).pack(padx=8, pady=(0, 6), anchor="w")

        columns = ("institution", "account", "balance", "owner", "include")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=4)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        for col, label, width in (
            ("institution", "Institution", 140),
            ("account", "Account", 180),
            ("balance", "Balance", 110),
            ("owner", "Owner", 100),
            ("include", "Include?", 70),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w" if col != "balance" else "e")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        for account in snapshot.accounts:
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    account.institution,
                    account.name,
                    _format_balance(account.balance, account.currency),
                    account.owner,
                    "yes" if account.include_in_assets else "no",
                ),
            )
            self._rows.append(
                {
                    "iid": iid,
                    "account_id": account.account_id,
                    "item_id": account.item_id,
                    "balance": account.balance,
                    "owner_var": tk.StringVar(value=account.owner),
                    "include_var": tk.BooleanVar(value=account.include_in_assets),
                }
            )

        edit_frame = ttk.LabelFrame(self, text="Edit selected account")
        edit_frame.pack(fill="x", padx=12, pady=8)
        ttk.Label(edit_frame, text="Owner:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.owner_combo = ttk.Combobox(
            edit_frame,
            values=sorted(VALID_OWNERS),
            state="readonly",
            width=16,
        )
        self.owner_combo.grid(row=0, column=1, padx=8, pady=6, sticky="w")
        self.include_edit_var = tk.BooleanVar(value=True)
        self.include_check = ttk.Checkbutton(
            edit_frame, text="Include in assets", variable=self.include_edit_var
        )
        self.include_check.grid(row=0, column=2, padx=8, pady=6, sticky="w")
        ttk.Button(edit_frame, text="Update Row", command=self._update_selected_row).grid(
            row=0, column=3, padx=8, pady=6
        )

        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        totals = snapshot.owner_totals
        self.totals_label = ttk.Label(
            self,
            text=(
                f"Owner totals (mapped):  self {_format_balance(totals.get('self', 0))}   "
                f"wife {_format_balance(totals.get('wife', 0))}"
            ),
        )
        self.totals_label.pack(padx=12, pady=4, anchor="w")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=12)
        if mapping_only:
            self.apply_assets_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                btn_frame,
                text="Also update Initial Assets from mapped totals",
                variable=self.apply_assets_var,
            ).pack(side="left", padx=(0, 12))
            ttk.Button(btn_frame, text="Save Mappings", command=self._apply_all).pack(
                side="left", padx=6
            )
        else:
            ttk.Button(btn_frame, text="Apply All", command=self._apply_all).pack(
                side="left", padx=6
            )
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _find_row(self, iid: str) -> dict | None:
        for row in self._rows:
            if row["iid"] == iid:
                return row
        return None

    def _disconnect_institution(self, item_id: str) -> None:
        if not messagebox.askyesno(
            "Disconnect institution",
            "Revoke Plaid access for this institution and remove its local token?\n\n"
            "On a Trial plan, the slot may not free up for a new institution.",
            parent=self,
        ):
            return
        try:
            result = disconnect_plaid_institution(self.data_service, item_id)
            if not result.get("ok"):
                messagebox.showerror("Disconnect Error", result.get("message", "Failed."), parent=self)
                return
            for row in list(self._rows):
                if row.get("item_id") == item_id:
                    self.tree.delete(row["iid"])
                    self._rows.remove(row)
            if not list_item_ids():
                messagebox.showinfo("Disconnected", result["message"], parent=self)
                self.destroy()
                return
            self._refresh_totals_label()
            messagebox.showinfo("Disconnected", result["message"], parent=self)
        except Exception as exc:
            messagebox.showerror("Disconnect Error", str(exc), parent=self)

    def _on_select_row(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._find_row(selection[0])
        if not row:
            return
        self.owner_combo.set(row["owner_var"].get())
        self.include_edit_var.set(row["include_var"].get())

    def _update_selected_row(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No selection", "Select an account row to edit.", parent=self)
            return
        row = self._find_row(selection[0])
        if not row:
            return
        owner = self.owner_combo.get()
        include = self.include_edit_var.get()
        row["owner_var"].set(owner)
        row["include_var"].set(include)
        values = list(self.tree.item(selection[0], "values"))
        values[3] = owner
        values[4] = "yes" if include else "no"
        self.tree.item(selection[0], values=values)
        self._refresh_totals_label()

    def _refresh_totals_label(self) -> None:
        totals = {"self": 0.0, "wife": 0.0}
        for row in self._rows:
            owner = row["owner_var"].get()
            if row["include_var"].get() and owner in totals:
                totals[owner] += row["balance"]
        self.totals_label.config(
            text=(
                f"Owner totals (review):  self {_format_balance(totals['self'])}   "
                f"wife {_format_balance(totals['wife'])}"
            )
        )

    def _apply_all(self) -> None:
        try:
            updates = [
                (
                    row["account_id"],
                    row["owner_var"].get(),
                    row["include_var"].get(),
                )
                for row in self._rows
            ]
            self.data_service.apply_account_mapping_review(updates)
            self.applied = True
            self.apply_assets = (
                self.mode != "manage" or getattr(self, "apply_assets_var", None).get()
            )
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Apply Error", str(exc), parent=self)

    def get_owner_totals(self) -> dict[str, float]:
        totals = {"self": 0.0, "wife": 0.0}
        for row in self._rows:
            owner = row["owner_var"].get()
            if row["include_var"].get() and owner in totals:
                totals[owner] += row["balance"]
        return totals


class ExpenseReviewDialog(tk.Toplevel):
    """Modal review of detected expenses before updating Base Expenses."""

    def __init__(self, parent, snapshot: ExpenseSnapshot, mode: str):
        super().__init__(parent)
        self.title("Review Expenses")
        self.geometry("900x520")
        self.minsize(760, 400)
        self.transient(parent)
        self.grab_set()

        self.snapshot = snapshot
        self.mode = mode
        self.applied = False
        self._rows: list[dict] = []

        analysis = snapshot.analysis
        header = ttk.Label(
            self,
            text=(
                f"Analyzed {analysis.transactions_used} transactions over "
                f"{analysis.months_analyzed:.1f} months. "
                f"Suggested annual household: {_format_balance(analysis.annual_household)}"
            ),
            wraplength=840,
        )
        header.pack(padx=12, pady=(12, 8), anchor="w")

        columns = ("include", "merchant", "owner", "category", "type", "monthly", "annual", "count")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=4)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=12)
        for col, label, width in (
            ("include", "Include?", 70),
            ("merchant", "Merchant", 180),
            ("owner", "Owner", 80),
            ("category", "Category", 100),
            ("type", "Type", 90),
            ("monthly", "Monthly", 100),
            ("annual", "Annual", 110),
            ("count", "Txns", 60),
        ):
            self.tree.heading(col, text=label)
            anchor = "e" if col in ("monthly", "annual", "count") else "w"
            self.tree.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.tree.tag_configure("unassigned", foreground="#8a5a00")

        for item in analysis.items:
            tags = ("unassigned",) if item.owner == "unassigned" else ()
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    "yes" if item.included else "no",
                    item.merchant,
                    item.owner,
                    item.category,
                    item.expense_type,
                    _format_balance(item.monthly_amount),
                    _format_balance(item.annual_amount),
                    item.occurrence_count,
                ),
                tags=tags,
            )
            self._rows.append(
                {
                    "iid": iid,
                    "item": item,
                    "include_var": tk.BooleanVar(value=item.included),
                    "owner_var": tk.StringVar(value=item.owner),
                }
            )

        self.tree.bind("<Double-1>", self._toggle_include)

        edit_frame = ttk.LabelFrame(self, text="Edit selected line")
        edit_frame.pack(fill="x", padx=12, pady=8)
        ttk.Label(edit_frame, text="Owner:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.owner_combo = ttk.Combobox(
            edit_frame,
            values=OWNER_ATTRIBUTION_CHOICES,
            state="readonly",
            width=16,
        )
        self.owner_combo.grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ttk.Label(edit_frame, text="Type:").grid(row=0, column=2, padx=8, pady=6, sticky="w")
        self.type_combo = ttk.Combobox(
            edit_frame,
            values=("recurring", "variable"),
            state="readonly",
            width=12,
        )
        self.type_combo.grid(row=0, column=3, padx=8, pady=6, sticky="w")
        ttk.Button(edit_frame, text="Update Row", command=self._update_selected_row).grid(
            row=0, column=4, padx=8, pady=6
        )
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        self.total_label = ttk.Label(self, text=self._totals_text())
        self.total_label.pack(padx=12, pady=4, anchor="w")

        self._unassigned_label = ttk.Label(self, text="", foreground="#8a5a00", wraplength=840)
        self._unassigned_label.pack(padx=12, pady=(0, 4), anchor="w")
        self._refresh_unassigned_label()

        if snapshot.errors:
            ttk.Label(
                self,
                text="Warnings: " + "; ".join(snapshot.errors),
                foreground="#8a5a00",
                wraplength=840,
            ).pack(padx=12, pady=4, anchor="w")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=12)
        ttk.Button(btn_frame, text="Apply All", command=self._apply_all).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _find_row(self, iid: str) -> dict | None:
        for row in self._rows:
            if row["iid"] == iid:
                return row
        return None

    def _on_select_row(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._find_row(selection[0])
        if row:
            self.owner_combo.set(row["owner_var"].get())
            self.type_combo.set(row["item"].expense_type or "variable")

    def _update_selected_row(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No selection", "Select a line to edit.", parent=self)
            return
        row = self._find_row(selection[0])
        if not row:
            return
        owner = self.owner_combo.get()
        if owner not in OWNER_ATTRIBUTION_CHOICES:
            messagebox.showwarning(
                "Invalid owner",
                f"Owner must be one of: {', '.join(OWNER_ATTRIBUTION_CHOICES)}",
                parent=self,
            )
            return
        expense_type = self.type_combo.get()
        if expense_type not in {"recurring", "variable"}:
            messagebox.showwarning(
                "Invalid type",
                "Type must be recurring or variable (one-off).",
                parent=self,
            )
            return
        row["owner_var"].set(owner)
        row["item"].owner = owner
        row["item"].expense_type = expense_type
        if expense_type == "recurring":
            row["include_var"].set(True)
            row["item"].included = True
        values = list(self.tree.item(selection[0], "values"))
        values[0] = "yes" if row["include_var"].get() else "no"
        values[2] = owner
        values[4] = expense_type
        tags = ("unassigned",) if owner == "unassigned" else ()
        self.tree.item(selection[0], values=values, tags=tags)
        self._refresh_total_label()

    def _category_summary(self) -> str:
        by_category: dict[str, float] = {}
        for row in self._rows:
            if not row["include_var"].get():
                continue
            category = row["item"].category
            by_category[category] = by_category.get(category, 0.0) + row["item"].annual_amount
        if not by_category:
            return ""
        parts = [
            f"{category} {_format_balance(amount)}"
            for category, amount in sorted(by_category.items(), key=lambda pair: -pair[1])[:4]
        ]
        return "Categories: " + "  |  ".join(parts)

    def _totals_text(self) -> str:
        totals = _line_item_owner_totals(self._rows, self.mode)
        summary = self._category_summary()
        text = (
            f"Selected annual — self {_format_balance(totals['self'])}  "
            f"wife {_format_balance(totals['wife'])}"
        )
        if summary:
            text += f"\n{summary}"
        return text

    def _refresh_total_label(self) -> None:
        self.total_label.config(text=self._totals_text())
        self._refresh_unassigned_label()

    def _refresh_unassigned_label(self) -> None:
        count = _count_unassigned_line_items(self._rows)
        if count:
            self._unassigned_label.config(
                text=(
                    f"{count} included line(s) still unassigned — "
                    "assign owners or they will be excluded from per-person totals."
                )
            )
        else:
            self._unassigned_label.config(text="")

    def _toggle_include(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        for row in self._rows:
            if row["iid"] != selection[0]:
                continue
            row["include_var"].set(not row["include_var"].get())
            row["item"].included = row["include_var"].get()
            values = list(self.tree.item(selection[0], "values"))
            values[0] = "yes" if row["include_var"].get() else "no"
            self.tree.item(selection[0], values=values)
            self._refresh_total_label()
            break

    def _sync_items_from_rows(self) -> None:
        for row in self._rows:
            row["item"].included = row["include_var"].get()
            row["item"].owner = row["owner_var"].get()

    def _apply_all(self) -> None:
        self._sync_items_from_rows()
        self.applied = True
        self.destroy()

    def get_owner_expense_totals(self) -> dict[str, float]:
        self._sync_items_from_rows()
        return self.snapshot.analysis.owner_annual_totals(self.mode)


class CsvImportReviewDialog(tk.Toplevel):
    """Review CSV balance import before updating Initial Assets."""

    def __init__(self, parent, rows: list[CsvBalanceRow]):
        super().__init__(parent)
        self.title("Review CSV Import")
        self.geometry("720x420")
        self.minsize(600, 320)
        self.transient(parent)
        self.grab_set()

        self.applied = False
        self._rows: list[dict] = []

        ttk.Label(
            self,
            text="Confirm account balances from CSV before updating Initial Assets.",
            wraplength=660,
        ).pack(padx=12, pady=(12, 8), anchor="w")

        columns = ("account", "balance", "owner")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=4)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        for col, label, width in (
            ("account", "Account", 240),
            ("balance", "Balance", 120),
            ("owner", "Owner", 100),
        ):
            self.tree.heading(col, text=label)
            anchor = "e" if col == "balance" else "w"
            self.tree.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        for row in rows:
            iid = self.tree.insert(
                "",
                "end",
                values=(row.account_name, _format_balance(row.balance), row.owner),
            )
            self._rows.append(
                {
                    "iid": iid,
                    "account_name": row.account_name,
                    "balance": row.balance,
                    "owner_var": tk.StringVar(value=row.owner),
                }
            )

        edit_frame = ttk.LabelFrame(self, text="Edit selected row")
        edit_frame.pack(fill="x", padx=12, pady=8)
        ttk.Label(edit_frame, text="Owner:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.owner_combo = ttk.Combobox(
            edit_frame,
            values=sorted(VALID_OWNERS),
            state="readonly",
            width=16,
        )
        self.owner_combo.grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ttk.Button(edit_frame, text="Update Row", command=self._update_selected_row).grid(
            row=0, column=2, padx=8, pady=6
        )
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        self.totals_label = ttk.Label(self, text=self._totals_text())
        self.totals_label.pack(padx=12, pady=4, anchor="w")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=12)
        ttk.Button(btn_frame, text="Apply All", command=self._apply_all).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _find_row(self, iid: str) -> dict | None:
        for row in self._rows:
            if row["iid"] == iid:
                return row
        return None

    def _on_select_row(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._find_row(selection[0])
        if row:
            self.owner_combo.set(row["owner_var"].get())

    def _update_selected_row(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No selection", "Select a row to edit.", parent=self)
            return
        row = self._find_row(selection[0])
        if not row:
            return
        owner = self.owner_combo.get()
        row["owner_var"].set(owner)
        values = list(self.tree.item(selection[0], "values"))
        values[2] = owner
        self.tree.item(selection[0], values=values)
        self.totals_label.config(text=self._totals_text())

    def _totals_text(self) -> str:
        totals = self.get_owner_totals()
        return (
            f"Owner totals:  self {_format_balance(totals['self'])}   "
            f"wife {_format_balance(totals['wife'])}"
        )

    def _apply_all(self) -> None:
        self.applied = True
        self.destroy()

    def get_owner_totals(self) -> dict[str, float]:
        totals = {"self": 0.0, "wife": 0.0}
        for row in self._rows:
            owner = row["owner_var"].get()
            if owner in totals:
                totals[owner] += row["balance"]
        return totals


class ContributionReviewDialog(tk.Toplevel):
    """Modal review of detected investment deposits before updating contributions."""

    def __init__(self, parent, snapshot: ContributionSnapshot, mode: str):
        super().__init__(parent)
        self.title("Review Contributions")
        self.geometry("900x520")
        self.minsize(760, 400)
        self.transient(parent)
        self.grab_set()

        self.snapshot = snapshot
        self.mode = mode
        self.applied = False
        self._rows: list[dict] = []

        analysis = snapshot.analysis
        header = ttk.Label(
            self,
            text=(
                f"Detected {analysis.transactions_used} contribution deposits over "
                f"{analysis.months_analyzed:.1f} months. "
                f"Estimated annual household: {_format_balance(analysis.annual_household)}"
            ),
            wraplength=840,
        )
        header.pack(padx=12, pady=(12, 8), anchor="w")

        columns = ("include", "merchant", "owner", "type", "monthly", "annual", "count")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=4)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=12)
        for col, label, width in (
            ("include", "Include?", 70),
            ("merchant", "Merchant", 200),
            ("owner", "Owner", 80),
            ("type", "Type", 110),
            ("monthly", "Monthly", 100),
            ("annual", "Annual", 110),
            ("count", "Txns", 60),
        ):
            self.tree.heading(col, text=label)
            anchor = "e" if col in ("monthly", "annual", "count") else "w"
            self.tree.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.tree.tag_configure("unassigned", foreground="#8a5a00")

        for item in analysis.items:
            tags = ("unassigned",) if item.owner == "unassigned" else ()
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    "yes" if item.included else "no",
                    item.merchant,
                    item.owner,
                    item.source_type,
                    _format_balance(item.monthly_amount),
                    _format_balance(item.annual_amount),
                    item.occurrence_count,
                ),
                tags=tags,
            )
            self._rows.append(
                {
                    "iid": iid,
                    "item": item,
                    "include_var": tk.BooleanVar(value=item.included),
                    "owner_var": tk.StringVar(value=item.owner),
                }
            )

        self.tree.bind("<Double-1>", self._toggle_include)

        edit_frame = ttk.LabelFrame(self, text="Edit selected line")
        edit_frame.pack(fill="x", padx=12, pady=8)
        ttk.Label(edit_frame, text="Owner:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.owner_combo = ttk.Combobox(
            edit_frame,
            values=OWNER_ATTRIBUTION_CHOICES,
            state="readonly",
            width=16,
        )
        self.owner_combo.grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ttk.Button(edit_frame, text="Update Row", command=self._update_selected_row).grid(
            row=0, column=2, padx=8, pady=6
        )
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        self.total_label = ttk.Label(self, text=self._totals_text())
        self.total_label.pack(padx=12, pady=4, anchor="w")

        self._unassigned_label = ttk.Label(self, text="", foreground="#8a5a00", wraplength=840)
        self._unassigned_label.pack(padx=12, pady=(0, 4), anchor="w")
        self._refresh_unassigned_label()

        if snapshot.errors:
            ttk.Label(
                self,
                text="Warnings: " + "; ".join(snapshot.errors),
                foreground="#8a5a00",
                wraplength=840,
            ).pack(padx=12, pady=4, anchor="w")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=12)
        ttk.Button(btn_frame, text="Apply All", command=self._apply_all).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _find_row(self, iid: str) -> dict | None:
        for row in self._rows:
            if row["iid"] == iid:
                return row
        return None

    def _on_select_row(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._find_row(selection[0])
        if row:
            self.owner_combo.set(row["owner_var"].get())

    def _update_selected_row(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No selection", "Select a line to edit.", parent=self)
            return
        row = self._find_row(selection[0])
        if not row:
            return
        owner = self.owner_combo.get()
        if owner not in OWNER_ATTRIBUTION_CHOICES:
            messagebox.showwarning(
                "Invalid owner",
                f"Owner must be one of: {', '.join(OWNER_ATTRIBUTION_CHOICES)}",
                parent=self,
            )
            return
        row["owner_var"].set(owner)
        row["item"].owner = owner
        values = list(self.tree.item(selection[0], "values"))
        values[2] = owner
        tags = ("unassigned",) if owner == "unassigned" else ()
        self.tree.item(selection[0], values=values, tags=tags)
        self._refresh_total_label()

    def _totals_text(self) -> str:
        totals = _line_item_owner_totals(self._rows, self.mode)
        return (
            f"Selected annual — self {_format_balance(totals['self'])}  "
            f"wife {_format_balance(totals['wife'])}"
        )

    def _refresh_total_label(self) -> None:
        self.total_label.config(text=self._totals_text())
        self._refresh_unassigned_label()

    def _refresh_unassigned_label(self) -> None:
        count = _count_unassigned_line_items(self._rows)
        if count:
            self._unassigned_label.config(
                text=(
                    f"{count} included line(s) still unassigned — "
                    "assign owners or they will be excluded from per-person totals."
                )
            )
        else:
            self._unassigned_label.config(text="")

    def _toggle_include(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        for row in self._rows:
            if row["iid"] != selection[0]:
                continue
            row["include_var"].set(not row["include_var"].get())
            row["item"].included = row["include_var"].get()
            values = list(self.tree.item(selection[0], "values"))
            values[0] = "yes" if row["include_var"].get() else "no"
            self.tree.item(selection[0], values=values)
            self._refresh_total_label()
            break

    def _sync_items_from_rows(self) -> None:
        for row in self._rows:
            row["item"].included = row["include_var"].get()
            row["item"].owner = row["owner_var"].get()

    def _apply_all(self) -> None:
        self._sync_items_from_rows()
        self.applied = True
        self.destroy()

    def get_owner_contribution_totals(self) -> dict[str, float]:
        self._sync_items_from_rows()
        return self.snapshot.analysis.owner_annual_totals(self.mode)


def _format_suggestion_value(field: ModelField, value: float) -> str:
    if field.field_type == "rate_pct":
        rate = value if abs(value) <= 1 else value / 100
        return f"{rate * 100:.2f}%"
    if field.field_type == "money":
        return f"{_format_balance(value)}/yr"
    if field.field_type in ("age", "year", "int"):
        return str(int(round(value)))
    return str(value)


class AssumptionResearchReviewDialog(tk.Toplevel):
    """Review an LLM-researched assumption before applying it to a field."""

    def __init__(self, parent, suggestion: AssumptionSuggestion, field: ModelField):
        super().__init__(parent)
        self.title("Review Assumption")
        self.geometry("720x520")
        self.minsize(640, 420)
        self.transient(parent)
        self.grab_set()

        self.suggestion = suggestion
        self.applied = False

        suggested = _format_suggestion_value(field, suggestion.suggested_annual_usd)
        ttk.Label(
            self,
            text=f"{field.label}: suggested {suggested}",
            font=("", 12, "bold"),
        ).pack(padx=12, pady=(12, 6), anchor="w")
        ttk.Label(
            self,
            text=f"Confidence: {suggestion.confidence}  |  {suggestion.provider} / {suggestion.model}",
            foreground="#555",
        ).pack(padx=12, pady=(0, 8), anchor="w")

        text = tk.Text(self, height=16, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=4)
        text.insert(
            "1.0",
            (
                f"Method: {suggestion.projection_method}\n\n"
                f"{suggestion.explanation}\n\n"
                f"Sources:\n"
                + "\n".join(f"  • {source}" for source in suggestion.sources)
            ),
        )
        text.config(state="disabled")

        ttk.Label(
            self,
            text="Educational research only — not licensed financial or medical advice.",
            foreground="#8a5a00",
            wraplength=680,
        ).pack(padx=12, pady=4, anchor="w")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=12)
        ttk.Button(btn_frame, text="Apply", command=self._apply).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _apply(self) -> None:
        self.applied = True
        self.destroy()


class LLMSettingsDialog(tk.Toplevel):
    """Configure LLM provider, model, and API key (stored in Keychain)."""

    PROVIDER_LABELS = {
        "none": "None (manual only)",
        "openai": "OpenAI (ChatGPT)",
        "anthropic": "Anthropic (Claude)",
        "xai": "xAI (Grok)",
    }

    def __init__(self, parent, current: LLMSettings):
        super().__init__(parent)
        self.title("LLM Settings")
        self.geometry("560x380")
        self.minsize(500, 340)
        self.transient(parent)
        self.grab_set()
        self.saved_settings = current

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Provider:").grid(row=0, column=0, sticky="w", pady=6)
        self.provider_var = tk.StringVar(value=current.provider)
        provider_combo = ttk.Combobox(
            frame,
            textvariable=self.provider_var,
            values=list(self.PROVIDER_LABELS.keys()),
            state="readonly",
            width=28,
        )
        provider_combo.grid(row=0, column=1, sticky="ew", pady=6)
        provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        ttk.Label(frame, text="Model:").grid(row=1, column=0, sticky="w", pady=6)
        model_row = ttk.Frame(frame)
        model_row.grid(row=1, column=1, sticky="ew", pady=6)
        model_row.columnconfigure(0, weight=1)
        self.model_var = tk.StringVar(value=current.model)
        self.model_combo = ttk.Combobox(model_row, textvariable=self.model_var, width=34)
        self.model_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(model_row, text="Refresh Models", command=self._refresh_models).grid(
            row=0, column=1, padx=(6, 0)
        )

        self.catalog_status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.catalog_status_var, foreground="#555").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        ttk.Label(frame, text="API Key:").grid(row=3, column=0, sticky="w", pady=6)
        self.api_key_entry = ttk.Entry(frame, show="•")
        self.api_key_entry.grid(row=3, column=1, sticky="ew", pady=6)

        self.key_status_var = tk.StringVar(value=self._key_status_text(current.provider))
        ttk.Label(frame, textvariable=self.key_status_var, foreground="#555").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        ttk.Label(
            frame,
            text="Models are fetched from your provider API and cached for 24 hours.",
            foreground="#666",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 4))
        ttk.Label(
            frame,
            text="Keys are stored in macOS Keychain. Used only for assumption research — not Plaid data.",
            foreground="#666",
            wraplength=500,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 8))

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=7, column=0, columnspan=2, pady=8)
        ttk.Button(btn_row, text="Save", command=self._save).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Remove Key", command=self._remove_key).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._load_models_for_provider(current.provider, preferred_model=current.model)

    def _key_status_text(self, provider: str) -> str:
        if provider == "none":
            return "LLM research disabled."
        if has_api_key(provider):
            return f"Keychain: key stored for {self.PROVIDER_LABELS.get(provider, provider)}"
        return "Keychain: no key stored yet"

    def _catalog_status_text(self, snapshot) -> str:
        if snapshot.provider == "none":
            return ""
        count = len(snapshot.models)
        as_of = snapshot.as_of[:10] if snapshot.as_of else "unknown"
        source = snapshot.source
        recommended = snapshot.recommended
        text = f"Models: {count} ({source}, {as_of})"
        if recommended:
            text += f"  |  recommended: {recommended}"
        if snapshot.errors:
            text += f"  |  {snapshot.errors[0]}"
        return text

    def _apply_model_snapshot(self, snapshot, *, preferred_model: str | None = None) -> None:
        model_ids = snapshot.model_ids()
        self.model_combo["values"] = model_ids
        chosen = preferred_model or self.model_var.get().strip()
        if chosen and chosen in model_ids:
            self.model_var.set(chosen)
        elif snapshot.recommended:
            self.model_var.set(snapshot.recommended)
        elif model_ids:
            self.model_var.set(model_ids[0])
        else:
            self.model_var.set("")
        self.catalog_status_var.set(self._catalog_status_text(snapshot))

    def _load_models_for_provider(self, provider: str, *, preferred_model: str | None = None) -> None:
        if provider == "none":
            self.model_combo["values"] = ()
            self.model_var.set("")
            self.catalog_status_var.set("")
            return
        snapshot = load_model_catalog(provider)
        self._apply_model_snapshot(snapshot, preferred_model=preferred_model)

    def _refresh_models(self) -> None:
        provider = self.provider_var.get().strip().lower()
        if provider == "none":
            return
        api_key = self.api_key_entry.get().strip() or None
        if api_key:
            store_api_key(provider, api_key)
            self.api_key_entry.delete(0, tk.END)
            self.key_status_var.set(self._key_status_text(provider))
        try:
            snapshot = refresh_model_catalog(provider, api_key=api_key)
            self._apply_model_snapshot(snapshot)
            if snapshot.source == "live":
                messagebox.showinfo(
                    "Models Updated",
                    f"Loaded {len(snapshot.models)} models from {self.PROVIDER_LABELS.get(provider, provider)}.",
                    parent=self,
                )
        except Exception as exc:
            messagebox.showerror("Refresh Error", str(exc), parent=self)

    def _on_provider_change(self, _event=None) -> None:
        provider = self.provider_var.get()
        self.key_status_var.set(self._key_status_text(provider))
        if provider == "none":
            self._load_models_for_provider("none")
            return
        self._load_models_for_provider(provider)

    def _save(self) -> None:
        provider = self.provider_var.get().strip().lower()
        model = self.model_var.get().strip()
        api_key = self.api_key_entry.get().strip()

        try:
            if provider != "none" and api_key:
                store_api_key(provider, api_key)
                self.api_key_entry.delete(0, tk.END)
                refresh_model_catalog(provider)
            settings = LLMSettings(
                provider=provider,
                model=model or default_model(provider),
            )
            save_llm_settings(settings)
            self.saved_settings = settings
            self.key_status_var.set(self._key_status_text(provider))
            messagebox.showinfo("Saved", "LLM settings updated.", parent=self)
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc), parent=self)

    def _remove_key(self) -> None:
        provider = self.provider_var.get().strip().lower()
        if provider == "none":
            return
        delete_api_key(provider)
        self.key_status_var.set(self._key_status_text(provider))
        messagebox.showinfo("Removed", f"API key removed for {provider}.", parent=self)


class RetirementGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Retirement Financial Modeling")
        self.root.geometry("900x620")
        self.root.minsize(700, 450)
        self.results_window = None
        self._data_service: DataService | None = None
        self._link_in_progress = False
        self._plaid_ready = get_settings().plaid_configured

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        _canvas, content = _create_scrollable_frame(root)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.columnconfigure(2, weight=1)

        self.mode_var = tk.StringVar(value="joint")
        ttk.Label(content, text="Mode:").grid(row=0, column=0, sticky="w", padx=10)
        ttk.Radiobutton(content, text="Joint", variable=self.mode_var, value="joint").grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(content, text="Separate", variable=self.mode_var, value="separate").grid(row=0, column=2, sticky="w")

        self.llm_settings = load_llm_settings()
        self.field_source_labels: dict[tuple[str | None, str], tk.StringVar] = {}
        self.shared_entries: dict[str, FieldWidget] = {}
        self.person_entries: dict[str, dict[str, FieldWidget]] = {}

        shared_frame = ttk.LabelFrame(content, text="Shared Parameters")
        shared_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=5)
        shared_frame.columnconfigure(1, weight=1)

        ttk.Label(
            shared_frame,
            text="Manual fields may show Research. Plaid/Market fields use Refresh buttons below.",
            foreground="#666",
            wraplength=820,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 4))

        llm_row = ttk.Frame(shared_frame)
        llm_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))
        ttk.Label(llm_row, text="State (LLM research):").pack(side="left")
        state_field = FIELDS_BY_KEY["state_of_residence"]
        self.assumption_state_entry = ttk.Combobox(
            llm_row,
            values=list(field_choice_values(state_field) or ("CA",)),
            state="readonly",
            width=6,
        )
        self.assumption_state_entry.set("CA")
        self.assumption_state_entry.pack(side="left", padx=(6, 12))
        self.llm_status_var = tk.StringVar(value=self._llm_status_text())
        ttk.Label(llm_row, textvariable=self.llm_status_var, foreground="#555").pack(side="left", padx=(0, 8))
        ttk.Button(llm_row, text="LLM Settings…", command=self.open_llm_settings).pack(side="left")

        shared_row = 2
        for field in SHARED_FIELDS:
            shared_row = _field_row_widgets(
                shared_frame,
                field,
                shared_row,
                person=None,
                entries=self.shared_entries,
                source_labels=self.field_source_labels,
                on_research=self.research_field,
            )

        self.person_frames = {}
        for col, person in enumerate(["self", "wife"]):
            frame = ttk.LabelFrame(content, text=person.capitalize() + " Parameters")
            frame.grid(row=2, column=col, sticky="nsew", padx=10, pady=5)
            self.person_frames[person] = frame
            entries: dict[str, FieldWidget] = {}
            person_row = 0
            for field in PERSON_FIELDS:
                person_row = _field_row_widgets(
                    frame,
                    field,
                    person_row,
                    person=person,
                    entries=entries,
                    source_labels=self.field_source_labels,
                    on_research=self.research_field,
                )
            self.person_entries[person] = entries

        market_frame = ttk.LabelFrame(content, text="Live Market Data")
        market_frame.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=5)
        market_frame.columnconfigure(0, weight=1)

        self.market_status_var = tk.StringVar(value="Refresh to load TSLA price, CPI inflation, and portfolio return.")
        ttk.Label(market_frame, textvariable=self.market_status_var, wraplength=820).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4)
        )
        self.market_refresh_btn = ttk.Button(
            market_frame,
            text="Refresh Market Data",
            command=self.refresh_market_data,
        )
        self.market_refresh_btn.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        live_frame = ttk.LabelFrame(content, text="Live Data (Plaid)")
        live_frame.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=5)
        live_frame.columnconfigure(0, weight=1)

        self.live_status_var = tk.StringVar(value=self._live_status_text())
        ttk.Label(live_frame, textvariable=self.live_status_var, wraplength=820).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 4)
        )

        btn_row = ttk.Frame(live_frame)
        btn_row.grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
        self.connect_btn = ttk.Button(btn_row, text="Connect Accounts", command=self.connect_accounts)
        self.connect_btn.pack(side="left", padx=(0, 6))
        self.manage_accounts_btn = ttk.Button(
            btn_row, text="Manage Accounts", command=self.manage_account_mappings
        )
        self.manage_accounts_btn.pack(side="left", padx=(0, 6))
        self.refresh_btn = ttk.Button(btn_row, text="Refresh Live Data", command=self.refresh_live_data)
        self.refresh_btn.pack(side="left", padx=(0, 6))
        self.expense_btn = ttk.Button(btn_row, text="Refresh Expenses", command=self.refresh_expenses)
        self.expense_btn.pack(side="left", padx=(0, 6))
        self.disconnect_btn = ttk.Button(btn_row, text="Disconnect All", command=self.disconnect_all)
        self.disconnect_btn.pack(side="left", padx=(0, 6))
        self.contrib_btn = ttk.Button(
            btn_row, text="Refresh Contributions", command=self.refresh_contributions
        )
        self.contrib_btn.pack(side="left", padx=(0, 6))
        self.import_csv_btn = ttk.Button(btn_row, text="Import CSV", command=self.import_csv_balances)
        self.import_csv_btn.pack(side="left", padx=(0, 6))
        self.clear_cache_btn = ttk.Button(btn_row, text="Clear Cache", command=self.clear_cached_data)
        self.clear_cache_btn.pack(side="left")

        if not self._plaid_ready:
            for btn in (
                self.connect_btn,
                self.manage_accounts_btn,
                self.refresh_btn,
                self.expense_btn,
                self.disconnect_btn,
                self.contrib_btn,
            ):
                btn.state(["disabled"])
            self.live_status_var.set(
                "Plaid not configured — copy .env.example to .env and add your Plaid keys."
            )

        ensure_active_policy()
        policy_summary = active_policy_summary()

        sim_frame = ttk.LabelFrame(content, text="Simulation")
        sim_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=10, pady=5)
        sim_frame.columnconfigure(1, weight=1)

        self.policy_status_var = tk.StringVar(value=f"Tax tables: {policy_summary['label']}")
        ttk.Label(
            sim_frame,
            textvariable=self.policy_status_var,
            foreground="#444",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2))

        self.sim_mode_var = tk.StringVar(value="deterministic")
        self.precision_var = tk.StringVar(value="standard")
        ttk.Label(sim_frame, text="Mode:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        mode_row = ttk.Frame(sim_frame)
        mode_row.grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Radiobutton(
            mode_row, text="Deterministic", variable=self.sim_mode_var, value="deterministic"
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            mode_row, text="Monte Carlo", variable=self.sim_mode_var, value="monte_carlo"
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(mode_row, text="Both", variable=self.sim_mode_var, value="both").pack(
            side="left"
        )

        ttk.Label(sim_frame, text="Tax Precision:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        precision_row = ttk.Frame(sim_frame)
        precision_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Radiobutton(
            precision_row,
            text="Standard (fast)",
            variable=self.precision_var,
            value="standard",
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            precision_row,
            text="CPA Precision",
            variable=self.precision_var,
            value="cpa",
        ).pack(side="left")
        self.cpa_mc_hint_var = tk.StringVar(
            value=(
                "CPA mode: itemized vs standard, AMT, strict state catalog. "
                "With Monte Carlo, use ~1,000 paths for reasonable runtime."
            )
        )
        ttk.Label(
            sim_frame,
            textvariable=self.cpa_mc_hint_var,
            foreground="#666",
            wraplength=720,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        ttk.Label(sim_frame, text="Monte Carlo Paths:").grid(row=4, column=0, sticky="w", padx=8)
        self.mc_paths_entry = ttk.Entry(sim_frame, width=12)
        self.mc_paths_entry.insert(0, "1000")
        self.mc_paths_entry.grid(row=4, column=1, sticky="w", padx=8, pady=(0, 8))

        ttk.Label(sim_frame, text="Random Seed (optional):").grid(row=5, column=0, sticky="w", padx=8)
        self.mc_seed_entry = ttk.Entry(sim_frame, width=12)
        self.mc_seed_entry.grid(row=5, column=1, sticky="w", padx=8, pady=(0, 8))

        ttk.Label(sim_frame, text="MC Stock Allocation %:").grid(row=6, column=0, sticky="w", padx=8)
        self.mc_stock_entry = ttk.Entry(sim_frame, width=12)
        self.mc_stock_entry.insert(0, "70")
        self.mc_stock_entry.grid(row=6, column=1, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(
            sim_frame,
            text="Return bootstrap blends S&P 500 (^GSPC) and bonds (AGG) by this weight.",
            foreground="#666",
        ).grid(row=7, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

        ttk.Label(sim_frame, text="Risk Tolerance:").grid(row=8, column=0, sticky="w", padx=8)
        self.risk_tolerance_var = tk.StringVar(value="moderate")
        risk_combo = ttk.Combobox(
            sim_frame,
            textvariable=self.risk_tolerance_var,
            values=["conservative", "moderate", "aggressive"],
            state="readonly",
            width=14,
        )
        risk_combo.grid(row=8, column=1, sticky="w", padx=8, pady=(0, 8))
        ttk.Label(
            sim_frame,
            text="Used by the Adviser tab for success-rate targets and concentration bands.",
            foreground="#666",
        ).grid(row=9, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

        action_row = ttk.Frame(content)
        action_row.grid(row=6, column=0, columnspan=3, pady=15)
        ttk.Button(action_row, text="Run Simulation", command=self.run_simulation).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            action_row,
            text="Validate Tax Return",
            command=self.validate_tax_return,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            action_row,
            text="Export Assumptions CSV",
            command=self.export_assumptions_csv,
        ).pack(side="left")

        ttk.Label(
            content,
            text="Results open in a separate window after you run the simulation.",
            foreground="gray",
        ).grid(row=7, column=0, columnspan=3, pady=(0, 10))

        self._load_cached_market_data()

    def _close_results_window(self):
        if self.results_window and self.results_window.winfo_exists():
            self.results_window._on_close()

    def _get_data_service(self) -> DataService:
        if self._data_service is None:
            self._data_service = DataService()
        return self._data_service

    def _set_field_entry_value(self, field: ModelField, value: float, *, person: str | None = None) -> None:
        entry = self._entry_for_field(field, person) if field.scope == "person" else self.shared_entries.get(field.key)
        if entry is None:
            return
        _widget_set_text(entry, format_field_value(field, value))

    def _set_field_source(self, person: str | None, field_key: str, label: str) -> None:
        source_var = self.field_source_labels.get((person, field_key))
        if source_var is not None:
            source_var.set(label)

    def _apply_market_snapshot(self, snapshot: MarketSnapshot) -> None:
        for field in fields_with_data_source("market", scope="shared"):
            attr = field.market_snapshot_attribute()
            if not attr:
                continue
            market_field = getattr(snapshot, attr, None)
            if market_field is None:
                continue
            self._set_field_entry_value(field, market_field.value)
            self._set_field_source(None, field.key, market_field.display_label())

    def _market_status_text(self, snapshot: MarketSnapshot) -> str:
        parts = ["Market data updated."]
        if snapshot.is_stale():
            parts.append("Warning: cached data is older than 24 hours.")
        if snapshot.errors:
            parts.append("Issues: " + "; ".join(snapshot.errors))
        return " ".join(parts)

    def _load_cached_market_data(self) -> None:
        try:
            snapshot = self._get_data_service().load_cached_market_snapshot()
        except Exception:
            return
        if snapshot is None:
            return
        self._apply_market_snapshot(snapshot)
        self.market_status_var.set(self._market_status_text(snapshot))

    def refresh_market_data(self) -> None:
        self.market_refresh_btn.state(["disabled"])
        self.market_status_var.set("Fetching market data…")
        try:
            snapshot = self._get_data_service().refresh_market_data()
            self._apply_market_snapshot(snapshot)
            self.market_status_var.set(self._market_status_text(snapshot))
            if snapshot.errors and not (
                snapshot.tsla_price or snapshot.inflation_rate or snapshot.investment_return_mean
            ):
                messagebox.showerror(
                    "Market Data Error",
                    "\n".join(snapshot.errors),
                    parent=self.root,
                )
            elif snapshot.errors:
                messagebox.showwarning(
                    "Market Data",
                    self._market_status_text(snapshot),
                    parent=self.root,
                )
        except Exception as exc:
            messagebox.showerror("Market Data Error", str(exc), parent=self.root)
            self.market_status_var.set("Market data refresh failed.")
        finally:
            self.market_refresh_btn.state(["!disabled"])

    def _live_status_text(self) -> str:
        if not self._plaid_ready:
            return "Plaid not configured."
        items = list_item_ids()
        if not items:
            return "No institutions connected — click Connect Accounts to link via Plaid."
        cached = self._get_data_service().load_cached_snapshot()
        expense_cached = self._get_data_service().load_cached_expense_snapshot()
        if cached and cached.as_of:
            status = (
                f"Last refresh: {cached.as_of}  |  "
                f"{len(items)} institution(s)  |  "
                f"self {_format_balance(cached.owner_totals.get('self', 0))}  "
                f"wife {_format_balance(cached.owner_totals.get('wife', 0))}"
            )
            if expense_cached and expense_cached.as_of:
                status += (
                    f"  |  expenses {_format_balance(expense_cached.analysis.annual_household)}"
                    f" ({expense_cached.as_of[:10]})"
                )
            return status
        if expense_cached and expense_cached.as_of:
            return (
                f"{len(items)} institution(s) connected  |  "
                f"expenses {_format_balance(expense_cached.analysis.annual_household)}"
                f" ({expense_cached.as_of[:10]})"
            )
        return f"{len(items)} institution(s) connected — click Refresh Live Data to fetch balances."

    def _update_live_status(self) -> None:
        self.live_status_var.set(self._live_status_text())

    def _set_live_buttons_busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        if not self._plaid_ready:
            state = ["disabled"]
        for btn in (
            self.connect_btn,
            self.manage_accounts_btn,
            self.refresh_btn,
            self.expense_btn,
            self.disconnect_btn,
            self.contrib_btn,
        ):
            btn.state(state)

    def _clear_link_in_progress(self) -> None:
        self._link_in_progress = False
        if self._plaid_ready:
            self.connect_btn.state(["!disabled"])

    def _show_link_instructions(self, url: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update_idletasks()
        messagebox.showinfo(
            "Plaid Link",
            f"Chrome should now show:\n{url}\n\n"
            "The URL is copied to your clipboard.\n\n"
            "Plaid Link should open automatically; use the button if it does not.\n"
            "When you see 'Connected', return here and click Manage Accounts "
            "(or Refresh Live Data for current balances).",
            parent=self.root,
        )

    def _poll_link_server_and_open_chrome(self, attempt: int = 0) -> None:
        max_attempts = 75
        if attempt >= max_attempts:
            self._clear_link_in_progress()
            self.live_status_var.set("Plaid Link server did not start — try Connect Accounts again.")
            messagebox.showerror(
                "Connect Error",
                "The Plaid Link server did not become ready.\n"
                "Close any old Chrome tabs and click Connect Accounts again.",
                parent=self.root,
            )
            self._update_live_status()
            return

        info_path = link_server_info_path()
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                url = info["url"]
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        open_in_chrome(url)
                        self.live_status_var.set(f"Plaid Link open in Chrome ({url})")
                        self.root.after(500, lambda: self._show_link_instructions(url))
                        self.root.after(60_000, self._clear_link_in_progress)
                        return
            except Exception:
                pass

        self.root.after(200, lambda: self._poll_link_server_and_open_chrome(attempt + 1))

    def connect_accounts(self) -> None:
        """Launch Plaid Link in a separate process (avoids macOS Tkinter crashes)."""
        if self._link_in_progress:
            return
        self._link_in_progress = True
        self.connect_btn.state(["disabled"])
        self.live_status_var.set("Starting Plaid Link server…")

        try:
            stop_stale_servers()
            subprocess.Popen(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "setup_plaid.py"),
                    "link",
                    "--no-browser",
                ],
                cwd=str(PROJECT_ROOT),
                start_new_session=True,
            )
            self._poll_link_server_and_open_chrome()
        except Exception as exc:
            self._clear_link_in_progress()
            messagebox.showerror("Connect Error", str(exc), parent=self.root)
        finally:
            self._update_live_status()

    def manage_account_mappings(self) -> None:
        if not self._plaid_ready:
            messagebox.showinfo(
                "Plaid Not Configured",
                "Add Plaid keys to .env before managing connected accounts.",
                parent=self.root,
            )
            return
        if not list_item_ids():
            messagebox.showwarning(
                "No accounts",
                "Connect an institution first (Connect Accounts).",
                parent=self.root,
            )
            return
        try:
            service = self._get_data_service()
            snapshot = service.snapshot_for_account_mapping()
            dialog = LiveDataReviewDialog(
                self.root, snapshot, service, mode="manage"
            )
            self.root.wait_window(dialog)
            if not dialog.applied:
                return
            totals = dialog.get_owner_totals()
            unassigned = sum(
                1 for row in dialog._rows if row["owner_var"].get() == "unassigned"
            )
            if dialog.apply_assets:
                self._apply_owner_totals(totals)
            self._update_live_status()
            message = "Account mappings saved."
            if unassigned:
                message += f"\n\n{unassigned} account(s) still unassigned (excluded from totals)."
            if dialog.apply_assets:
                message += (
                    f"\n\nInitial Assets updated:\n"
                    f"  self: {_format_balance(totals['self'])}\n"
                    f"  wife: {_format_balance(totals['wife'])}"
                )
            messagebox.showinfo("Mappings Saved", message, parent=self.root)
        except Exception as exc:
            messagebox.showerror("Manage Accounts", str(exc), parent=self.root)

    def refresh_live_data(self) -> None:
        if not list_item_ids():
            messagebox.showwarning(
                "No accounts",
                "Connect an institution first (Connect Accounts).",
                parent=self.root,
            )
            return
        try:
            service = self._get_data_service()
            snapshot = service.refresh_live_data()
            if snapshot.errors and not snapshot.accounts:
                messagebox.showerror("Refresh Error", "\n".join(snapshot.errors), parent=self.root)
                return
            dialog = LiveDataReviewDialog(self.root, snapshot, service, mode="refresh")
            self.root.wait_window(dialog)
            if not dialog.applied:
                self._update_live_status()
                return
            totals = dialog.get_owner_totals()
            if dialog.apply_assets:
                self._apply_owner_totals(totals)
            self._update_live_status()
            messagebox.showinfo(
                "Applied",
                f"Initial Assets updated:\n"
                f"  self: {_format_balance(totals['self'])}\n"
                f"  wife: {_format_balance(totals['wife'])}",
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("Refresh Error", str(exc), parent=self.root)

    def _apply_owner_totals(self, totals: dict[str, float]) -> None:
        asset_field = next(
            (field for field in fields_with_data_source("plaid", scope="person") if field.key == "initial_assets"),
            None,
        )
        if asset_field is None:
            return
        for person in ("self", "wife"):
            value = totals.get(person, 0.0)
            self._set_field_entry_value(asset_field, value, person=person)
            self._set_field_source(person, asset_field.key, "Plaid balances")

    def _expense_source_label(self, snapshot: ExpenseSnapshot) -> str:
        as_of = snapshot.as_of[:10] if snapshot.as_of else "unknown date"
        return f"Plaid transactions ({as_of})"

    def _apply_owner_expenses(
        self,
        totals: dict[str, float],
        *,
        snapshot: ExpenseSnapshot | None = None,
    ) -> None:
        expense_field = field_with_model_role("base_expense")
        if expense_field is None:
            return
        source = self._expense_source_label(snapshot) if snapshot else "Plaid transactions"
        for person in ("self", "wife"):
            value = totals.get(person, 0.0)
            if value:
                self._set_field_entry_value(expense_field, value, person=person)
                self._set_field_source(person, expense_field.key, source)
            else:
                self._set_field_source(person, expense_field.key, "Manual")

    def refresh_expenses(self) -> None:
        self._set_live_buttons_busy(True)
        self.live_status_var.set("Syncing transactions and analyzing expenses…")
        try:
            service = self._get_data_service()
            snapshot = service.refresh_expenses()
            analysis = snapshot.analysis
            if snapshot.errors and analysis.transactions_used == 0:
                messagebox.showerror(
                    "Expense Error",
                    "\n".join(snapshot.errors),
                    parent=self.root,
                )
                return
            if analysis.transactions_used == 0:
                messagebox.showwarning(
                    "No transactions",
                    "No expense transactions found yet. "
                    "Sandbox data may take a moment after connecting — try again shortly.",
                    parent=self.root,
                )
                return
            dialog = ExpenseReviewDialog(self.root, snapshot, self.mode_var.get())
            self.root.wait_window(dialog)
            if not dialog.applied:
                return
            totals = dialog.get_owner_expense_totals()
            service.save_expense_snapshot(snapshot)
            self._apply_owner_expenses(totals, snapshot=snapshot)
            message = (
                f"Base Expenses updated:\n"
                f"  self: {_format_balance(totals['self'])}\n"
                f"  wife: {_format_balance(totals['wife'])}"
            )
            unassigned = _count_unassigned_line_items(dialog._rows)
            if unassigned:
                message += (
                    f"\n\n{unassigned} included line(s) still unassigned "
                    "(excluded from per-person totals)."
                )
            messagebox.showinfo("Applied", message, parent=self.root)
            if snapshot.errors:
                messagebox.showwarning(
                    "Expense Warnings",
                    "Applied with warnings:\n" + "\n".join(snapshot.errors),
                    parent=self.root,
                )
        except Exception as exc:
            messagebox.showerror("Expense Error", str(exc), parent=self.root)
        finally:
            self._set_live_buttons_busy(False)
            self._update_live_status()

    def disconnect_all(self) -> None:
        item_ids = list_item_ids()
        if not item_ids:
            messagebox.showinfo("Disconnect", "No connected institutions.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Disconnect All",
            f"Revoke Plaid access for {len(item_ids)} institution(s) and remove tokens from Keychain?",
            parent=self.root,
        ):
            return
        try:
            service = self._get_data_service()
            for item_id in item_ids:
                service.disconnect_item(item_id)
            self._update_live_status()
            messagebox.showinfo("Disconnected", "All institutions disconnected.", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Disconnect Error", str(exc), parent=self.root)

    def _contribution_source_label(self, snapshot: ContributionSnapshot) -> str:
        as_of = snapshot.as_of[:10] if snapshot.as_of else "unknown date"
        return f"Plaid deposits ({as_of})"

    def _apply_owner_contributions(
        self,
        totals: dict[str, float],
        *,
        snapshot: ContributionSnapshot | None = None,
    ) -> None:
        field = FIELDS_BY_KEY.get("annual_contributions")
        if field is None:
            return
        source = self._contribution_source_label(snapshot) if snapshot else "Plaid deposits"
        for person in ("self", "wife"):
            value = totals.get(person, 0.0)
            if value:
                self._set_field_entry_value(field, value, person=person)
                self._set_field_source(person, field.key, source)

    def refresh_contributions(self) -> None:
        self._set_live_buttons_busy(True)
        self.live_status_var.set("Syncing transactions and estimating contributions…")
        try:
            service = self._get_data_service()
            snapshot = service.refresh_contributions()
            analysis = snapshot.analysis
            if analysis.transactions_used == 0:
                messagebox.showwarning(
                    "No contributions detected",
                    "No investment deposit transactions found in the cached history. "
                    "Try Refresh Expenses first to sync transactions.",
                    parent=self.root,
                )
                return
            dialog = ContributionReviewDialog(self.root, snapshot, self.mode_var.get())
            self.root.wait_window(dialog)
            if not dialog.applied:
                return
            totals = dialog.get_owner_contribution_totals()
            service.save_contribution_snapshot(snapshot)
            self._apply_owner_contributions(totals, snapshot=snapshot)
            message = (
                f"Annual Contributions updated:\n"
                f"  self: {_format_balance(totals['self'])}\n"
                f"  wife: {_format_balance(totals['wife'])}"
            )
            unassigned = _count_unassigned_line_items(dialog._rows)
            if unassigned:
                message += (
                    f"\n\n{unassigned} included line(s) still unassigned "
                    "(excluded from per-person totals)."
                )
            messagebox.showinfo("Applied", message, parent=self.root)
            if snapshot.errors:
                messagebox.showwarning(
                    "Contribution Warnings",
                    "Applied with warnings:\n" + "\n".join(snapshot.errors),
                    parent=self.root,
                )
        except Exception as exc:
            messagebox.showerror("Contribution Error", str(exc), parent=self.root)
        finally:
            self._set_live_buttons_busy(False)
            self._update_live_status()

    def import_csv_balances(self) -> None:
        filename = filedialog.askopenfilename(
            title="Import account balances CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self.root,
        )
        if not filename:
            return
        try:
            service = self._get_data_service()
            rows = service.parse_balance_csv_file(filename)
            dialog = CsvImportReviewDialog(self.root, rows)
            self.root.wait_window(dialog)
            if not dialog.applied:
                return
            totals = dialog.get_owner_totals()
            self._apply_owner_totals(totals)
            for person in ("self", "wife"):
                self._set_field_source(person, "initial_assets", "CSV import")
            messagebox.showinfo(
                "Applied",
                f"Initial Assets updated from CSV:\n"
                f"  self: {_format_balance(totals['self'])}\n"
                f"  wife: {_format_balance(totals['wife'])}",
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("CSV Import Error", str(exc), parent=self.root)

    def clear_cached_data(self) -> None:
        if not messagebox.askyesno(
            "Clear Cache",
            "Delete encrypted vault cache (balances, transactions, market data)?\n\n"
            "Plaid tokens and account mappings are kept.",
            parent=self.root,
        ):
            return
        try:
            self._get_data_service().clear_cached_data()
            self._update_live_status()
            self.market_status_var.set("Cache cleared — refresh market and live data to reload.")
            messagebox.showinfo(
                "Cache Cleared",
                "Encrypted cache removed. Use Refresh buttons to reload data.",
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("Clear Cache Error", str(exc), parent=self.root)

    def export_assumptions_csv(self) -> None:
        try:
            params = self._collect_simulation_params()
            df = build_assumptions_dataframe(
                params,
                sources=self._build_data_sources(),
                risk_tolerance=self.risk_tolerance_var.get(),
            )
            filename = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")],
                initialfile=f"assumptions_{date.today().isoformat()}.csv",
                parent=self.root,
            )
            if not filename:
                return
            df.to_csv(filename, index=False)
            messagebox.showinfo("Export Complete", f"Saved to {filename}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc), parent=self.root)

    def _parse_optional_float(self, entry):
        value = entry.get().strip()
        return float(value) if value else None

    def _parse_optional_int(self, entry):
        value = entry.get().strip()
        return int(value) if value else None

    def _llm_status_text(self) -> str:
        if not self.llm_settings.configured:
            return "LLM: manual only"
        key_ok = has_api_key(self.llm_settings.provider)
        key_note = "key stored" if key_ok else "no API key"
        return f"LLM: {self.llm_settings.provider} ({key_note})"

    def open_llm_settings(self) -> None:
        dialog = LLMSettingsDialog(self.root, self.llm_settings)
        self.root.wait_window(dialog)
        self.llm_settings = dialog.saved_settings
        self.llm_status_var.set(self._llm_status_text())

    def _entry_for_field(self, field: ModelField, person: str | None) -> FieldWidget:
        if field.scope == "shared":
            return self.shared_entries[field.key]
        if person is None:
            raise ValueError(f"Person is required for field {field.key}")
        return self.person_entries[person][field.key]

    def _apply_suggestion_to_field(
        self,
        field: ModelField,
        person: str | None,
        suggestion: AssumptionSuggestion,
    ) -> None:
        entry = self._entry_for_field(field, person)
        value = suggestion.suggested_annual_usd
        if field.field_type == "rate_pct":
            rate_decimal = value if abs(value) <= 1 else value / 100
            _widget_set_text(entry, format_field_value(field, rate_decimal))
        elif field.field_type in ("age", "year", "int"):
            _widget_set_text(entry, str(int(round(value))))
        elif field.field_type == "money":
            _widget_set_text(
                entry,
                str(int(value) if value == int(value) else round(value, 2)),
            )
        else:
            _widget_set_text(entry, str(value))
        self._set_field_source(person, field.key, suggestion.source_label())

    def research_field(self, field: ModelField, person: str | None = None) -> None:
        if not field.supports_llm_research():
            return
        if not self.llm_settings.configured:
            messagebox.showinfo(
                "LLM Not Configured",
                "Open LLM Settings to choose a provider (OpenAI, Anthropic, or xAI) and save an API key.",
                parent=self.root,
            )
            return
        if not has_api_key(self.llm_settings.provider):
            messagebox.showwarning(
                "API Key Required",
                f"Add an API key for {self.llm_settings.provider} in LLM Settings first.",
                parent=self.root,
            )
            return

        context_person = person
        current_age = None
        retirement_age = None
        if person:
            person_entries = self.person_entries[person]
            current_age = int(_widget_get_text(person_entries["current_age"]))
            retirement_age = int(_widget_get_text(person_entries["retirement_age"]))
        elif "self" in self.person_entries:
            person_entries = self.person_entries["self"]
            current_age = int(_widget_get_text(person_entries["current_age"]))
            retirement_age = int(_widget_get_text(person_entries["retirement_age"]))

        context = AssumptionResearchContext(
            field_key=field.key,
            person=context_person,
            current_age=current_age,
            retirement_age=retirement_age,
            state=_widget_get_text(self.assumption_state_entry) or None,
            planning_year=date.today().year,
            fred_api_key=get_settings().fred_api_key,
        )

        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            suggestion, _from_cache = research_assumption(
                context,
                provider=self.llm_settings.provider,
                model=self.llm_settings.model,
            )
        except Exception as exc:
            messagebox.showerror("Research Error", str(exc), parent=self.root)
            return
        finally:
            self.root.config(cursor="")

        dialog = AssumptionResearchReviewDialog(self.root, suggestion, field)
        self.root.wait_window(dialog)
        if not dialog.applied:
            return
        self._apply_suggestion_to_field(field, person, suggestion)

    def _build_data_sources(self) -> dict[str, str]:
        sources = {}
        for (person, field_key), var in self.field_source_labels.items():
            value = var.get().strip()
            if not value or value == "Manual":
                continue
            field = FIELDS_BY_KEY.get(field_key)
            label = field.label if field else field_key.replace("_", " ").title()
            if person:
                sources[f"{person.title()} {label}"] = value
            else:
                sources[label] = value
        return sources

    def _collect_simulation_params(self) -> dict:
        params: dict = {
            "mode": self.mode_var.get(),
            "persons": {},
            "current_year": date.today().year,
            "data_sources": self._build_data_sources(),
        }
        for field in SHARED_FIELDS:
            params[field.key] = parse_field_value(
                field, _widget_get_text(self.shared_entries[field.key])
            )
        params["planning_precision"] = self.precision_var.get()
        stock_alloc = self.mc_stock_entry.get().strip() or "70"
        params["mc_stock_allocation_pct"] = float(stock_alloc)
        for person in ("self", "wife"):
            params["persons"][person] = {
                field.key: parse_field_value(
                    field, _widget_get_text(self.person_entries[person][field.key])
                )
                for field in PERSON_FIELDS
            }
        return params

    def _parse_optional_seed(self) -> int | None:
        value = self.mc_seed_entry.get().strip()
        if not value:
            return None
        return int(value)

    def _fetch_holdings_for_adviser(self) -> list[HoldingSnapshot]:
        if not self._plaid_ready:
            return []
        try:
            return self._get_data_service().get_portfolio_holdings()
        except Exception:
            return []

    def _apply_what_if(self, changes: list[WhatIfChange]) -> None:
        for change in changes:
            field = FIELDS_BY_KEY.get(change.field_key)
            if field is None:
                continue
            entry = self._entry_for_field(field, change.person)
            _widget_set_text(entry, format_field_value(field, change.new_value))
            self._set_field_source(change.person, change.field_key, "Adviser what-if")
        messagebox.showinfo(
            "What-if Applied",
            "Suggested values were copied to the input form.\n"
            "Run Simulation again to see the updated projection.",
            parent=self.root,
        )

    def validate_tax_return(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Select tax return JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            result = validate_tax_return_file(path, auto_activate_policy=True)
            self.policy_status_var.set(f"Tax tables: {result.policy_label}")
            title = "Tax Return Validation — PASS" if result.passed else "Tax Return Validation — FAIL"
            messagebox.showinfo(title, result.summary_text(), parent=self.root)
        except Exception as exc:
            messagebox.showerror("Tax Return Validation Error", str(exc), parent=self.root)

    def run_simulation(self):
        try:
            sim_mode = self.sim_mode_var.get()
            num_paths = int(self.mc_paths_entry.get().strip() or "1000")
            if num_paths <= 0:
                raise ValueError("Monte Carlo paths must be positive")
            if self.precision_var.get() == "cpa" and sim_mode in {"monte_carlo", "both"} and num_paths > 1500:
                if not messagebox.askyesno(
                    "CPA Precision + Monte Carlo",
                    "CPA Precision runs slower per path. Consider 1,000 paths or fewer.\n\n"
                    f"Continue with {num_paths:,} paths?",
                    parent=self.root,
                ):
                    return

            params = self._collect_simulation_params()
            param_warnings = collect_param_warnings(params)
            if param_warnings:
                messagebox.showwarning(
                    "Planning Approximations",
                    "\n\n".join(param_warnings),
                    parent=self.root,
                )

            self.root.config(cursor="watch")
            self.root.update_idletasks()
            output = run_simulation(
                params,
                simulation_mode=sim_mode,
                num_paths=num_paths,
                seed=self._parse_optional_seed(),
            )
            holdings = self._fetch_holdings_for_adviser()
            self._close_results_window()
            self.results_window = ResultsWindow(
                self.root,
                output,
                params=params,
                risk_tolerance=self.risk_tolerance_var.get(),
                holdings=holdings or None,
                on_apply_what_if=self._apply_what_if,
            )
            self.results_window.focus()

        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid input: {e}")
        except Exception as e:
            messagebox.showerror("Simulation Error", str(e))
        finally:
            self.root.config(cursor="")


if __name__ == "__main__":
    root = tk.Tk()
    app = RetirementGUI(root)
    root.mainloop()