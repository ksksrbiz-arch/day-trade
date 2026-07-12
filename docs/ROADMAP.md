# Roadmap — toward an "intelligence terminal"

A staged plan to give the platform **Bloomberg-terminal-class UX and analytics
breadth**, built on top of the intelligence engine that already exists. This is
a companion to `ARCHITECTURE.md`: same philosophy — **additive, reversible,
behaviour-preserving** — layered feature-by-feature, never a big-bang rewrite.

---

## Status

The **first sprint and then some is shipped** — a live-quote SSE spine, a
keyboard command palette (`AAPL GP <GO>` function codes), a savable tiled
**Launchpad**, live-quote chips in panel headers, and eight brain-wired panels
under the **Terminal** tab:

| Phase | Feature | Status |
|---|---|---|
| 0 | Streaming spine (`/api/quotes/stream` SSE + on-demand subscribe) | ✅ shipped |
| 1 | Command line + **Launchpad** (tiled/resizable/savable) | ✅ shipped |
| 2 | Security Master (DES/FA + House View) | ✅ shipped |
| 3 | Advanced Chart (GP) | ✅ shipped |
| 4 | Options (OMON) | ✅ shipped |
| 5 | Screener (EQS) + **Market Monitor (MOST)** | ✅ shipped |
| 6 | Calendar (ECO) | ✅ shipped |
| 7 | Tape & Depth (TAS) | ✅ shipped |
| 8 | Portfolio (PORT) | ✅ shipped |
| 9 | Cross-asset (FX / rates / commodities) | ⬜ deferred (data-gated) |

Remaining: Phase 9 (cross-asset, blocked on data sources) and continued live-data
validation on Render during market hours. The phase detail below is retained as
the design record.

---

## 1. The honest framing

Bloomberg is three things bundled: (a) **licensed real-time cross-asset data**,
(b) a **keyboard-first terminal UX** (function codes, tiled Launchpad), and
(c) **deep analytics** (FA, PORT, OMON, ECO, screening).

We cannot buy (a) — Bloomberg's edge is exclusive feeds costing ~$24k/user/yr.
What we *can* do, and where the leverage is:

- **(b) and (c) are pure software** — a keyboard-driven terminal shell and
  analytics screens are ours to build on the data we already reach (Alpaca,
  Alpha Vantage fundamentals, RSS/news, crypto APIs).
- **We already have something Bloomberg doesn't:** the confluence brain — mesh
  intelligence, council LLM, RL voice, calibration, attribution. Bloomberg shows
  you data; this terminal shows you data **plus a reasoned house view** on every
  security. That is the product wedge, not an afterthought.

So the north star is: **a single-operator intelligence terminal** — terminal
breadth and speed, with our brain wired into every panel.

---

## 2. What already exists (build on, don't rebuild)

| Bloomberg capability | Function | Current state in repo | Reuse |
|---|---|---|---|
| Company description | `DES` | `fundamentals.fetch_overview` | ✅ have data |
| Financial analysis | `FA` | `fundamentals.py` (scored overview) | ✅ partial |
| Price graph | `GP` | `/api/bars` + canvas chart | 🟡 basic |
| News (ticker-filtered) | `N`, `CN` | `news.py`, `newshub.py`, `newsfeeds.py`, `wsb.py` | ✅ strong |
| Watchlist / monitor | `W`, `MOST` | `watchlist.py`, `/api/watchlist`, `/api/scanner` | 🟡 static |
| Options | `OMON` | `options.py` (`pick_contract`, `OptionsBroker`) | 🟡 exec only |
| Screening | `EQS` | `scanner.py`, `factors.py`, `xsection.py` | 🟡 no UI |
| Portfolio analytics | `PORT` | `attribution.py`, `/api/portfolio_history`, `risk.py` | 🟡 partial |
| Order flow / depth | `MBO`/`TAS` | `ofi.py` (order-flow imbalance) | 🟡 signal only |
| Command line | `<GO>` | cmdk palette in `index.html` | 🟡 not ticker-first |
| Alerts | `ALRT` | `alerts.py`, `/api/alerts`, `/api/alerts/ack` | ✅ have |
| **House view / conviction** | *(none in BBG)* | `alpha.py` confluence, `mesh_*`, `council.py`, `cortex.py`, `tnet.py`, RL | ✅ **our edge** |

Gaps that block a "terminal" feel: **no live streaming to the client** (the SPA
polls ~30 endpoints every 20s; `/api/telemetry/stream` SSE exists but is unused
for quotes), **no tiled/savable workspace**, **no single-security deep-dive
screen**, **no option chain / greeks UI**, **no calendars**.

---

## 3. Design principles (non-negotiable)

1. **Keep the safety wall.** Paper-only execution path stays intact; every new
   analytics surface is read-only unless it routes through the existing
   `policy → safety → sizing → broker` chain.
2. **Additive & reversible**, per `ARCHITECTURE.md`. New routers, new panels; no
   rewrite of working algorithms.
3. **Bandwidth-conscious.** We just cut outbound bandwidth ~10x (gzip + visibility
   -gated polling). Streaming must be *delta-based*, not full-payload re-pushes,
   or it undoes that win. This is a hard design constraint, not a nicety.
4. **Brain-in-every-panel.** Any security view shows the confluence/mesh read for
   that name — the differentiator ships in phase 2, not "later".
5. **Honest data labels.** IEX/delayed/derived data is labelled as such in the UI.
   No pretending we have consolidated tape.

---

## 4. Phased roadmap

Effort: **S** ≈ days · **M** ≈ 1–2 wks · **L** ≈ 3+ wks. Impact is toward the
"terminal feel" / user value.

### Phase 0 — Real-time streaming spine · **M · foundation**
The backbone everything else rides on. Without it, "terminal" is just prettier polling.
- Server: subscribe to Alpaca's websocket (`StockDataStream`) for quotes/trades on
  a watchlist set; fan out to clients over a **single SSE/WebSocket delta channel**
  (extend the existing `/api/telemetry/stream` pattern).
- Client: a small live-quote store; panels subscribe to symbols, not poll.
- Win: sub-second quotes **and** it structurally replaces the 20s poll for live data
  (bandwidth stays low because we push diffs).
- Reuse: `marketdata.py`, existing SSE generator in `dashboard/app.py`.

### Phase 1 — Terminal shell: command line + Launchpad · **M · high**
The two things that *make* it feel like a terminal.
- **Function-code command line** (evolve cmdk): `AAPL` → security view; `AAPL GP`,
  `AAPL FA`, `AAPL DES`, `AAPL OMON`, `SPY N` mnemonics; history & autocomplete.
- **Launchpad**: tiled, resizable, **savable** panel layouts (localStorage → later
  server-persisted). Panels are the existing cards, made draggable.
- Reuse: `cmdkCommands()` already maps commands→actions; extend the grammar.

### Phase 2 — Security master (the deep-dive screen) · **L · highest**
One keystroke to everything about a name — this is where our edge shows.
- Tabbed single-security view: **DES** (fundamentals overview), **GP** (advanced
  multi-study chart), **FA** (financial history + ratios), **News**, and — the
  wedge — **House View**: the live confluence score, mesh consensus, council take,
  RL vote, calibration, and attribution for that symbol, in one panel.
- Reuse: `fundamentals.py`, `alpha.analyze`, `mesh_consensus`, `council.py`,
  `tnet.py`, `sigtrack.py` — mostly wiring existing outputs into one screen.

### Phase 3 — Advanced charting (GP) · **M · high**
- Multi-pane chart: candles + volume + overlays (MA/BB/VWAP) + studies (RSI/MACD)
  from `ta.py`; drawing tools; multiple timeframes; overlay our signal markers.
- Reuse: `ta.py` already computes the studies; this is front-end rendering.

### Phase 4 — Options terminal (OMON / OVML) · **M · med-high**
- Full option chain by expiry with greeks + IV; a simple **vol surface** heatmap;
  P/L diagram for a proposed structure.
- Reuse: `options.py` (`pick_contract`); add a chain/greeks data adapter.

### Phase 5 — Market monitors & screening (EQS / MOST) · **M · med**
- Movers/heatmap, sector RRG, breadth; interactive screener UI over `factors.py` /
  `xsection.py` with savable screens.
- Reuse: `scanner.py`, `factors.py`, `xsection.py`, `/api/scanner`.

### Phase 6 — Calendars & events (ECO / EVTS) · **S–M · med**
- Earnings calendar + economic calendar; event-anchored alerts ("flag me 1d before
  MSFT earnings"). New lightweight data adapter (no calendar source exists today).
- Reuse: `alerts.py` for the alerting half.

### Phase 7 — Order flow & tape (TAS / MBO) · **M · med**
- Time & sales tape and a depth/imbalance panel driven by the Phase-0 stream and
  `ofi.py`; large-print / sweep highlighting.
- Reuse: `ofi.py` (order-flow imbalance already computed).

### Phase 8 — Portfolio analytics & export (PORT) · **M · med**
- PORT-style attribution, exposure, scenario/VaR, drawdown; CSV/Excel export API
  and a read-only data endpoint for spreadsheets.
- Reuse: `attribution.py`, `risk.py`, `beta.py`, `metrics.py`, `portfolio_history`.

### Phase 9 — Cross-asset breadth · **L · lower (data-gated)**
- FX / rates / commodities panels where free/cheap sources exist; expand the
  existing crypto path (`freecryptoapi.py`, `/api/crypto_bars`). Scoped by what
  data we can actually license.

---

## 5. Sequencing & first sprint

**Dependency order:** Phase 0 (stream) → 1 (shell) → 2 (security master) unlock
the "terminal" identity and must come first. 3–8 are largely parallel panels that
plug into that shell. 9 is data-gated and last.

**Proposed first sprint (highest leverage, ~2 weeks):**
1. Phase 0 streaming spine (quotes over one delta channel).
2. Phase 1 command line + a minimal 2×2 Launchpad.
3. Phase 2 security master as the first "app" you open with `TICKER <GO>`,
   with the House View panel wired in.

That trio alone reframes the product from "a dashboard" into "a terminal with a
brain." Everything after is additive panels.

---

## 6. Risks & constraints

- **Data licensing** is the ceiling. Alpaca IEX is limited/delayed; consolidated
  tape, deep options greeks, and cross-asset feeds cost money. Label data honestly
  and scope Phases 4/7/9 to what we can actually source.
- **Bandwidth regression.** Streaming must push deltas; a naive full-state stream
  would erase the recent ~10x bandwidth cut. Enforce delta-only in Phase 0 design.
- **Memory/plan headroom.** The RL + TensorFlow daemon already flirts with OOM on
  Render's starter plan; a persistent websocket fan-out adds load. Budget a plan
  bump or move streaming to a lightweight process before Phase 0 ships.
- **Scope discipline.** Bloomberg has thousands of functions. This roadmap targets
  the ~10 that a discretionary single operator actually uses daily; resist the
  long tail.
