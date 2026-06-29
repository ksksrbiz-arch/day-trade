# Architecture Blueprint — paper-trader

A senior-engineer plan to evolve the platform toward clean architecture **without
changing product behaviour**. The system is live (350 passing tests, autonomy
controller running, paper trades executing, two web frontends). So this is a
*staged, reversible* migration — not a big-bang rewrite. Each phase keeps the
suite green and the services running.

---

## 1. Where we are today

`trader/` is a flat package of **68 modules** plus `trader/agents/` (10 modules),
with two presentation apps (`dashboard/` FastAPI + single-page UI, `brain/`
Next.js 3D viz). It grew organically and works well, but everything imports
everything via `from . import x`, so the dependency graph is a hairball:
domain logic, data adapters, ML, HTTP, and persistence all live at one level.

Concretely, the flat package mixes five very different concerns:

| Concern | Example modules |
|---|---|
| **Domain / strategy** (pure logic) | `alpha`, `ta`, `quant`, `fundamentals`, `risk`, `strategy`, `xsection`, `labels` |
| **Intelligence** (learned/derived) | `cortex`, `tnet`, `backprop`, `mesh*`, `reasoning`, `predict/*`, `market_brain`, `council`, `shadow`, `edge`, `attribution`, `sigtrack` |
| **Execution** | `broker`, `simbroker`, `exits`, `options`, `scalper`, `run`, `resilience`, `policy`, `safety` |
| **Infrastructure / adapters** | `clearstreet`, `freecryptoapi`, `massive`, `history`, `marketdata`, `newsfeeds`, `news`, `wsb`, `omni`, `pieces_ltm`, `cloudflare`, `config` |
| **Interface / presentation** | `dashboard/app.py`, `dashboard/static/index.html`, `brain/` |

The pain points a refactor should remove:
1. **No dependency direction** — domain code can import HTTP clients and vice-versa.
2. **Hidden coupling** — `alpha.analyze()` reaches into `cortex`, `tnet`, `reasoning`, `voices`, `backprop` directly.
3. **Adapters not behind ports** — broker/data/LLM vendors are imported by name, so swapping or testing them means monkeypatching concrete modules.
4. **`dashboard/app.py` is a god-file** — ~60 endpoints, each lazily importing trader internals (presentation knows the whole domain).

---

## 2. Target: clean architecture (dependency rule = inward only)

```
            ┌─────────────────────────────────────────────┐
            │            interfaces (drivers)              │  dashboard API, brain, CLI (run.py)
            └───────────────┬─────────────────────────────┘
                            │ depends on
            ┌───────────────▼─────────────────────────────┐
            │              application                     │  use-cases / orchestration
            │  (confluence pipeline, trade cycle,          │  (agents/runtime, autonomy sweep,
            │   autonomy controller, mesh orchestration)   │   analyze→size→execute)
            └───────────────┬─────────────────────────────┘
                            │ depends on
            ┌───────────────▼─────────────────────────────┐
            │                domain                        │  PURE, no I/O
            │  signals (ta/quant/fundamental), risk,       │
            │  confluence blender, conviction model,       │
            │  strategy rules, value objects               │
            └───────────────▲─────────────────────────────┘
                            │ implemented by (ports ◄── adapters)
            ┌───────────────┴─────────────────────────────┐
            │            infrastructure                    │  brokers, market data, LLMs,
            │  (adapters behind domain-defined ports)      │  feeds, sqlite stores, Pieces LTM
            └──────────────────────────────────────────────┘
```

**The one rule:** dependencies point inward. `domain` imports nothing from the
other layers. `infrastructure` implements *ports* (interfaces) declared by the
domain/application. `interfaces` (FastAPI, Next.js, CLI) depend on application
use-cases, never on infrastructure directly.

### Proposed package layout (Python side)

```
trader/
  domain/                # PURE: no network, no sqlite, no framework imports
    signals/             # ta.py, quant.py, fundamentals.py, xsection.py, labels.py
    confluence.py        # the blender (was alpha.confluence) — pure function
    conviction.py        # Conviction value object
    risk.py              # sizing, trailing-stop math (pure)
    strategy.py          # entry/exit rules (pure)
    ports.py             # Protocols: BrokerPort, MarketDataPort, LLMPort, MemoryPort, InsightBus

  application/           # use-cases: orchestrate domain + ports (no vendor code)
    analyze.py           # build method scores -> confluence (was alpha.analyze)
    trade_cycle.py       # watch -> decide -> size -> route (was run.py loop)
    autonomy.py          # guarded self-tuning controller
    mesh/                # insight-mesh orchestration (consensus, anomaly, sla, ...)
    intelligence/        # cortex/tnet/backprop training+serving orchestration

  infrastructure/        # adapters implementing domain ports
    brokers/             # alpaca (broker.py), sim (simbroker.py)
    marketdata/          # marketdata.py, history.py, massive.py, freecryptoapi.py
    feeds/               # news.py, newsfeeds.py, newshub.py, wsb.py
    llm/                 # council.py, omni.py, cloudflare.py
    memory/              # pieces_ltm.py, mesh store (sqlite), reasoning store
    config.py

  interfaces/
    api/                 # FastAPI routers (split dashboard/app.py by concern)
    cli/                 # run.py, exits.py entrypoints

  agents/                # already a cohesive bounded context — keep, tidy internals
```

Ports (in `domain/ports.py`) are `typing.Protocol` classes, e.g.:

```python
class BrokerPort(Protocol):
    def submit(self, intent: OrderIntent) -> str | None: ...
    def positions(self) -> list[Position]: ...
    def close(self, symbol: str) -> bool: ...

class InsightBus(Protocol):
    def publish(self, layer: str, kind: str, text: str, **kw) -> bool: ...
    def recent(self, n: int = 30, **kw) -> list[Insight]: ...
```

`AlpacaBroker` and `SimBroker` both satisfy `BrokerPort`; the trade-cycle
use-case takes a `BrokerPort`, so paper/live/sim is a constructor swap and tests
inject a fake — no monkeypatching concrete modules.

---

## 3. Migration strategy — staged, behaviour-preserving

The non-negotiable: **the suite stays green and the daemons keep running after
every step.** We achieve that with *re-export shims* (the "strangler fig"
pattern): move code into the new package, leave the old module path as a thin
`from trader.new.path import *`. Nothing that imports the old name breaks; we
migrate call-sites gradually, then delete the shim.

**Phase 0 (done in this commit — zero risk, additive):**
introduce `trader/intelligence/` as a *facade namespace* that re-exports the
mesh-intelligence family under clean names, proving the layering pattern without
moving a single file:

```python
from trader.intelligence import consensus, anomaly, themes, priority   # new clean import
# old imports (from trader import mesh_consensus) still work unchanged
```

**Phase 1 — extract the domain core (pure):** move `ta/quant/fundamentals/
xsection/risk/strategy` and the `confluence` blender into `trader/domain/`,
leaving shims. These are already nearly pure; the only work is severing
`alpha.analyze`'s direct reach into intelligence modules by passing scores in.

**Phase 2 — define ports + wrap adapters:** add `domain/ports.py`; make
`AlpacaBroker`/`SimBroker` declare they implement `BrokerPort`; route the
trade-cycle through the port. Same for `MarketDataPort`, `LLMPort`, `InsightBus`.

**Phase 3 — split the API god-file:** break `dashboard/app.py` into routers
(`api/mesh.py`, `api/intelligence.py`, `api/trading.py`, `api/agents.py`) mounted
on the FastAPI app. Pure mechanical move; endpoints unchanged.

**Phase 4 — collapse shims:** once call-sites use the new paths, delete the
old-name shims. Each deletion is its own green-tested commit.

Every phase is independently revertable and shippable.

---

## 4. Why this is better (the architectural wins)

- **Separation of concerns:** pure decision logic (domain) is isolated from I/O,
  so it is trivially testable and reasoned about. Today `alpha` mixes both.
- **Reduced coupling:** the confluence pipeline depends on *ports*, not on
  `alpaca`, `cloudflare`, `sqlite`. Vendors become replaceable details.
- **Modularity / bounded contexts:** `agents/`, `intelligence/mesh/`, `execution/`
  become cohesive units with explicit public surfaces, not 68 peers.
- **Scalability:** new signal sources, brokers, or LLMs are added by implementing
  a port — open/closed. The mesh already demonstrated this (11 modules added with
  zero edits to the blender).
- **Testability:** ports + fakes remove the monkeypatch-the-module pattern the
  current hermetic tests rely on; tests target behaviour, not import paths.
- **Maintainability:** the dependency rule makes "where does this go?" obvious and
  prevents the graph from re-tangling as the system grows.

---

## 5. What this refactor deliberately does NOT do

- It does **not** rewrite working algorithms — the NumPy ML, transformer, mesh,
  and risk logic are sound; they get *relocated and decoupled*, not reimplemented.
- It does **not** big-bang. A live trading system with autonomy enabled is the
  worst place for a flag-day rewrite. Strangler-fig shims keep prod alive.
- It does **not** change any API response, dashboard view, or trade behaviour —
  that is the acceptance criterion for every phase (diff the `/api/*` payloads).
```
