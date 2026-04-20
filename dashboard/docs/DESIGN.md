# LEVIATHAN Dashboard — Design System Spec

> Path-B v2 W3 (Day 23-27) target. Frontend source of truth for color palette, typography, components, interaction patterns.
> Follow-up: `/design-consultation` at W3 kickoff.

---

## 1. Visual Principles

- **Dark mode default** — operator monitors late hours; light mode reduces glare in 10+ page dashboard workflow
- **Dense data over whitespace** — this is a trading dashboard, not a marketing page. Information hierarchy > breathability
- **Numeric precision over rounding** — always show 2 decimal places for USD, 4 for %, 6 for crypto qty
- **Green = good, red = bad, yellow = attention, gray = inactive** — no creative color coding
- **Latency visible** — every live number has a timestamp + age indicator (e.g. "3s ago")

## 2. Color Palette (OKLCH for perceptual uniformity)

| Token | Value (dark) | Value (light) | Usage |
|-------|-------------|---------------|-------|
| `--bg-0` | `oklch(0.14 0 0)` | `oklch(0.99 0 0)` | Base |
| `--bg-1` | `oklch(0.18 0 0)` | `oklch(0.97 0 0)` | Cards |
| `--bg-2` | `oklch(0.22 0 0)` | `oklch(0.94 0 0)` | Hover / active |
| `--fg-0` | `oklch(0.96 0 0)` | `oklch(0.15 0 0)` | Primary text |
| `--fg-1` | `oklch(0.70 0 0)` | `oklch(0.40 0 0)` | Secondary text |
| `--fg-2` | `oklch(0.50 0 0)` | `oklch(0.60 0 0)` | Disabled / hint |
| `--ok` | `oklch(0.72 0.14 155)` | `oklch(0.50 0.14 155)` | Profit, verified, healthy |
| `--warn` | `oklch(0.78 0.13 85)` | `oklch(0.60 0.14 85)` | Pending, caution |
| `--bad` | `oklch(0.63 0.22 25)` | `oklch(0.50 0.22 25)` | Loss, error, halted |
| `--accent` | `oklch(0.65 0.20 260)` | `oklch(0.50 0.20 260)` | Links, primary action |

## 3. Typography

- **Primary**: Inter v4 (variable font) — UI text, headings
- **Monospace**: JetBrains Mono v2.3 — numbers, code, timestamps, order IDs
- Scale: 11 / 12 / 14 / 16 / 20 / 24 / 32 px (no intermediate values)
- Line height: 1.35 body, 1.15 headings

## 4. Component Patterns

### Card
- `padding: 16px`, `border-radius: 8px`, `background: --bg-1`, `border: 1px solid --bg-2`
- Header: 14px uppercase + label color (`--fg-1`)
- Body: numeric primary 24px mono, secondary 12px

### Table
- Dense rows (32px height), hover highlights
- Right-align numbers, left-align text
- Zebra stripes via `:nth-child(even)` `--bg-2/30%`

### Badge (status)
- `STATUS` uppercase 10px mono, `padding: 2px 6px`, `border-radius: 4px`
- `verified` → `--ok` background with `--bg-0` text
- `pending` → `--warn`
- `diverged` → `--bad`
- `halted` → `--bad` + pulse animation

### Chart (Recharts or LightweightCharts)
- Line color = `--accent`
- Shaded area for min/max band = `--accent/20%`
- Gridlines `--bg-2` thin
- Tooltip: card pattern, monospace numbers

## 5. 8 Pages Spec (W3 Day 23-27)

| Path | Primary content | Refresh |
|------|----------------|---------|
| `/pnl` | Verified PnL (exchange-income) + variance decomposition (6 cols) + divergence badge | 30s |
| `/positions` | Hedge-pair aggregation, binance_leg + bitget_leg + net_unrealized | 5s |
| `/trace/{trace_id}` | Signal → validate → order → fill → PnL timeline | static per trace |
| `/strategy-health` | Per-strategy: last_signal, WR 24h, budget_remaining, is_halted | 30s |
| `/divergence` | Live gauge `leviathan_pnl_divergence_usd` + 24h history chart | 10s |
| `/daily-report` | UTC 00:05 CSV viewer, variance decomposition deep dive | static per day |
| `/rejections` | 16 ReasonCode count + top 10 recent rejections log | 10s |
| `/reconciliation` | Engine-vs-exchange reconciliation result + unmatched events | 60s |

## 6. Accessibility

- **WCAG AA minimum** — 4.5:1 contrast for text, 3:1 for UI
- Keyboard navigation on every interactive element
- `prefers-reduced-motion` honored (halt pulse, chart entrance animations)
- Focus rings visible (2px `--accent` outline)

## 7. Tech Stack (W3 implementation)

- Next.js 14 App Router (existing)
- Tailwind CSS 4 (design tokens mapped to CSS variables)
- Recharts 2.13 (line charts) + LightweightCharts 5 (candlesticks if needed)
- Zustand for client state, React Query for server state
- Chrome DevTools MCP for E2E

## 8. Navigation / Layout

- Left sidebar fixed 240px: 8 page links + `Mode: paper/live` badge top
- Top bar 48px: engine status (green dot), last Binance sync timestamp, Telegram alert counter
- Right-side notification drawer (collapsible): recent divergence events, rejections top 3

---

> *This doc expanded via `/design-consultation` at W3 kickoff. Placeholder pending actual design iteration.*
