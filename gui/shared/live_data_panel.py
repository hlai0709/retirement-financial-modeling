"""Shared Live Data card UI for expert sidebar and guided wizard step."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk

from gui.shared.app_context import AppContext

StatusCallback = Callable[[str], None]

PLAID_ACTIONS = frozenset(
    {
        "Connect Accounts",
        "Manage Accounts",
        "Refresh Balances",
        "Refresh Expenses",
        "Refresh Contributions",
    }
)


@dataclass(frozen=True)
class LiveDataCardSpec:
    card_id: str
    title: str
    description: str
    action_labels: tuple[str, ...]
    requires_plaid: bool = True


def live_data_card_specs(*, compact: bool = False) -> tuple[LiveDataCardSpec, ...]:
    """Card metadata for regression tests and layout."""
    short = compact
    return (
        LiveDataCardSpec(
            "accounts",
            "Institutions",
            "Link accounts via Plaid."
            if short
            else "Connect bank and investment accounts, then map them to Self or Wife.",
            ("Connect Accounts", "Manage Accounts"),
        ),
        LiveDataCardSpec(
            "balances",
            "Portfolio balances",
            "Import current asset totals into Initial Assets."
            if short
            else "Refresh live balances from Plaid or import a CSV export.",
            ("Refresh Balances", "Import CSV"),
            requires_plaid=False,
        ),
        LiveDataCardSpec(
            "expenses",
            "Base expenses",
            "Estimate annual spending from recent transactions."
            if short
            else "Analyze Plaid transactions to suggest per-person base living expenses.",
            ("Refresh Expenses",),
        ),
        LiveDataCardSpec(
            "contributions",
            "Contributions",
            "Detect recurring investment deposits."
            if short
            else "Find contribution deposits and update annual contribution assumptions.",
            ("Refresh Contributions",),
        ),
        LiveDataCardSpec(
            "market",
            "Market assumptions",
            "Update inflation, returns, and index levels."
            if short
            else "Refresh FRED/market inputs used for inflation and return assumptions.",
            ("Refresh Market",),
            requires_plaid=False,
        ),
    )


class LiveDataPanel:
    """Card-based Plaid and market data actions."""

    def __init__(self, parent: ttk.Frame, ctx: AppContext, *, compact: bool = False) -> None:
        self.parent = parent
        self.ctx = ctx
        self.compact = compact
        self._overall_status = tk.StringVar(value=ctx.live_status_text())
        self._card_status: dict[str, tk.StringVar] = {}
        self._build()

    def refresh_status(self) -> None:
        self._overall_status.set(self.ctx.live_status_text())

    def _build(self) -> None:
        wrap = 560 if self.compact else 760
        ttk.Label(self.parent, textvariable=self._overall_status, wraplength=wrap).pack(
            anchor="w", padx=8, pady=(8, 12)
        )

        if not self.ctx.plaid_ready:
            ttk.Label(
                self.parent,
                text="Plaid is not configured (.env). Market refresh and CSV import still work.",
                foreground="#666",
                wraplength=wrap,
            ).pack(anchor="w", padx=8, pady=(0, 8))

        grid = ttk.Frame(self.parent)
        grid.pack(fill="both", expand=True, padx=8, pady=4)
        columns = 1 if self.compact else 2
        for col in range(columns):
            grid.columnconfigure(col, weight=1, uniform="live_cards")

        specs = live_data_card_specs(compact=self.compact)
        for index, spec in enumerate(specs):
            row = index // columns
            col = index % columns
            self._build_card(grid, spec, row=row, column=col)

    def _build_card(self, parent: ttk.Frame, spec: LiveDataCardSpec, *, row: int, column: int) -> None:
        card = ttk.LabelFrame(parent, text=spec.title)
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text=spec.description, wraplength=320 if self.compact else 360).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 6)
        )

        btn_row = ttk.Frame(card)
        btn_row.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))
        handlers = self._action_handlers(spec.card_id)
        for label in spec.action_labels:
            handler = handlers.get(label)
            if handler is None:
                continue
            btn = ttk.Button(btn_row, text=label, command=handler)
            btn.pack(side="left", padx=(0, 8), pady=2)
            if not self.ctx.plaid_ready and label in PLAID_ACTIONS:
                btn.state(["disabled"])

        status_var = tk.StringVar(value="")
        self._card_status[spec.card_id] = status_var
        ttk.Label(card, textvariable=status_var, foreground="#555", wraplength=320).grid(
            row=2, column=0, sticky="w", padx=10, pady=(0, 8)
        )

    def _action_handlers(self, card_id: str) -> dict[str, Callable[[], None]]:
        if card_id == "accounts":
            return {
                "Connect Accounts": lambda: self._run(self.ctx.connect_accounts),
                "Manage Accounts": lambda: self._run(self.ctx.manage_account_mappings),
            }
        if card_id == "balances":
            return {
                "Refresh Balances": lambda: self._run(self.ctx.refresh_live_data),
                "Import CSV": lambda: self._run(self.ctx.import_csv_balances),
            }
        if card_id == "expenses":
            return {"Refresh Expenses": lambda: self._run(self.ctx.refresh_expenses)}
        if card_id == "contributions":
            return {"Refresh Contributions": lambda: self._run(self.ctx.refresh_contributions)}
        if card_id == "market":
            return {"Refresh Market": lambda: self._run(self.ctx.refresh_market_data, card_id="market")}
        return {}

    def _run(self, action: Callable[..., None], *, card_id: str | None = None) -> None:
        def on_status(text: str) -> None:
            self._overall_status.set(text)
            if card_id and card_id in self._card_status:
                self._card_status[card_id].set(text)
            elif not card_id:
                self.refresh_status()

        action(on_status=on_status)


def build_live_data_panel(
    parent: ttk.Frame,
    ctx: AppContext,
    *,
    compact: bool = False,
    title: str | None = "Live Data (Plaid)",
) -> LiveDataPanel:
    """Create a labeled frame containing the shared live data card panel."""
    frame = ttk.LabelFrame(parent, text=title) if title else ttk.Frame(parent)
    if title:
        frame.pack(fill="both", expand=True, padx=8, pady=8)
    else:
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
    panel = LiveDataPanel(frame, ctx, compact=compact)
    return panel