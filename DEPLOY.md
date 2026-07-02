# Deploying the platform

Two pieces:

- **Frontend (brain 3D graph)** — Next.js in `brain/`, already on **Vercel**
  (`https://day-trade-nu.vercel.app/brain`).
- **Backend (this repo root)** — FastAPI telemetry/API + intelligence daemons.
  Long-running + stateful, so it needs a **container host** (Render / Railway /
  Fly.io), **not** Vercel serverless.

The two connect via one env var: the brain reads `NEXT_PUBLIC_TELEMETRY_BASE`
and calls `<that>/api/telemetry/...`. CORS on the backend is already open (`*`).

---

## 1. Deploy the backend (Render — easiest, has HTTPS + a free tier)

1. Push is already done (repo `ksksrbiz-arch/day-trade`).
2. Render → **New + → Blueprint** → pick the `day-trade` repo. It reads
   `render.yaml` and provisions one Docker web service with a 1 GB data disk.
   (Or **New + → Web Service → Docker** and point at the repo root Dockerfile.)
3. In the service's **Environment** tab, add your secrets (from your local
   `.env` — never committed). For the brain graph alone you need **none**; add
   these only for full trading/LLM features:
   ```
   ALPACA_KEY, ALPACA_SECRET            # paper trading
   GROQ_KEY                             # context enricher
   CF_ACCOUNT_ID, CF_API_TOKEN          # Cloudflare Workers AI (council/embeddings)
   COHERE_KEY, TIINGO_TOKEN, ...        # optional extras (see .env.example)
   ```
   Leave `RUN_DAEMONS=1` to generate live mesh activity; set `0` for API-only.
4. Deploy. Health check hits `/api/telemetry/topology`. When green you'll have a
   URL like `https://day-trade-backend.onrender.com`.

Verify: open `https://<your-backend>/api/telemetry/topology` — you should get JSON.

> Railway/Fly alternative: they auto-detect the `Dockerfile` (or use the
> `Procfile` for a buildpack build). Set the same env vars and expose `$PORT`.

---

## 2. Point the Vercel brain at the backend

Vercel → project **Settings → Environment Variables**:

```
NEXT_PUBLIC_TELEMETRY_BASE = https://<your-backend-host>
```

(must be **https** — Vercel is https, so an http backend is blocked as mixed
content). **Redeploy** the Vercel project so the var is baked in. Reload
`/brain` — the graph now streams live topology + pulses from your backend.

---

## 3. Notes / caveats

- **Paper only.** No real-money path exists; the broker is Alpaca paper.
- **State:** the Render disk persists the SQLite mesh/reasoning/alerts stores at
  `/app/data`. Without a disk they reset on redeploy (fine for the viz).
- **The 2D control dashboard** (`dashboard/static/index.html`) is served by the
  same backend at `/` — visit `https://<your-backend>/` for the full terminal.
- **Cost:** the daemons run continuously; on a free tier that may sleep. For
  always-on live data use a paid instance or set `RUN_DAEMONS=0` and drive
  updates on demand.
