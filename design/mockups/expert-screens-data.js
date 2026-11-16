/** Expert mode sections — mirrors gui/expert/sections.py EXPERT_NAV */
const EXPERT_SCREENS = [
  {
    num: 1, id: "setup", file: "expert-01-setup-liquid-glass.html",
    title: "Setup", headline: "Household setup",
    subtitle: "Mode and tax jurisdiction for the whole plan",
    groups: [
      {
        title: "Household",
        open: true,
        fields: [
          { label: "Household mode", type: "radio", options: ["Joint", "Separate"], on: 0 },
          { label: "State of residence", type: "select", value: "CA", options: ["CA", "TX", "FL"] },
        ],
        caption: "Tax tables in use — 2024 federal + CA state",
      },
    ],
  },
  {
    num: 2, id: "assumptions", file: "expert-02-assumptions-liquid-glass.html",
    title: "Assumptions", headline: "Assumptions",
    subtitle: "All planning fields — grouped by topic",
    showPersonFilter: true,
    showSearch: true,
    personOn: 0,
    groups: [
      {
        title: "Timeline & longevity",
        open: true,
        fields: [
          { label: "Current age", type: "narrow", value: "58" },
          { label: "Birth year", type: "year", value: "1968" },
          { label: "Life expectancy", type: "narrow", value: "95" },
          { label: "Marriage year", type: "year", value: "1992" },
        ],
      },
      {
        title: "Market assumptions",
        open: false,
        fields: [
          { label: "Inflation rate", type: "medium", value: "2.5%" },
          { label: "Investment return", type: "medium", value: "6.5%" },
        ],
      },
      {
        title: "Your income & savings",
        open: false,
        fields: [
          { label: "Retirement age", type: "narrow", value: "62" },
          { label: "Annual earned income", type: "dollar", value: "185,000" },
          { label: "Annual contributions", type: "dollar", value: "36,000" },
        ],
      },
      {
        title: "Taxes & benefits",
        open: false,
        fields: [
          { label: "Social Security start age", type: "narrow", value: "67" },
          { label: "Annual Social Security", type: "dollar", value: "42,000" },
        ],
      },
    ],
    moreNote: "+ 8 more groups when scrolling (assets, spending, healthcare, spouse fields…)",
  },
  {
    num: 3, id: "llm", file: "expert-03-ai-assistant-liquid-glass.html",
    title: "AI Assistant", headline: "AI Assistant",
    subtitle: "Batch-estimate empty fields with AI",
    layout: "llm",
    llmStatus: "API key configured · model gpt-4o",
    checks: [
      "Self · Social Security benefit",
      "Self · Pre-Medicare health insurance",
      "Wife · Annual earned income",
      "Shared · Inflation rate",
      "Self · Medigap monthly premium",
    ],
    checksOn: [0, 1, 3],
  },
  {
    num: 4, id: "live_data", file: "expert-04-live-data-liquid-glass.html",
    title: "Live Data", headline: "Live data",
    subtitle: "Connect accounts and refresh assumptions from real data",
    layout: "live_data",
    cards: [
      { title: "Institutions", desc: "Connect bank and investment accounts, then map them to Self or Wife.", actions: ["Connect Accounts", "Manage Accounts"] },
      { title: "Portfolio balances", desc: "Refresh live balances from Plaid or import a CSV export.", actions: ["Refresh Balances", "Import CSV"] },
      { title: "Base expenses", desc: "Analyze Plaid transactions to suggest per-person base living expenses.", actions: ["Refresh Expenses"] },
      { title: "Contributions", desc: "Find contribution deposits and update annual contribution assumptions.", actions: ["Refresh Contributions"] },
      { title: "Market assumptions", desc: "Refresh FRED/market inputs used for inflation and return assumptions.", actions: ["Refresh Market"] },
    ],
  },
  {
    num: 5, id: "simulate", file: "expert-05-simulate-liquid-glass.html",
    title: "Simulate", headline: "Simulation",
    subtitle: "Run options and Monte Carlo settings",
    layout: "simulate",
    simModeOn: 2,
    precisionOn: 0,
    fields: [
      { label: "MC paths", type: "narrow", value: "1000" },
      { label: "Stock allocation %", type: "narrow", value: "70" },
      { label: "Risk tolerance", type: "select", value: "moderate", options: ["conservative", "moderate", "aggressive"] },
    ],
    actions: ["Validate Tax Return", "Export Assumptions CSV"],
  },
  {
    num: 6, id: "settings", file: "expert-06-settings-liquid-glass.html",
    title: "Settings", headline: "Settings",
    subtitle: "Integrations and app configuration",
    layout: "settings",
    items: [
      { label: "LLM Settings…", type: "button" },
      { label: "Plaid: configured (.env)", type: "status", ok: true },
      { label: "Presentation mode: Expert", type: "status" },
    ],
  },
];