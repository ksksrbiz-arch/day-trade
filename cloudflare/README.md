# Cloudflare scheduler for the paper-trading backend

A tiny Cloudflare Worker that uses **free Cron Triggers** to keep the Render
backend warm, run the nightly ML research sweep, and push a daily digest.

## What it does
| Cron (UTC) | Task | Effect |
|---|---|---|
| `*/10 * * * *` | keep-warm | `GET /health` + topology so the free Render dyno never idles out (which kills the daemons) |
| `30 1 * * *` | research | `GET /api/research/run` — triggers the deep ML sweep on Render after the US close |
| `0 13 * * 1-5` | digest | `GET /api/digest` → forwards the summary to your Slack/Discord webhook |

Heavy compute stays on Render; Cloudflare only schedules and forwards.

## Deploy (one time)
```bash
npm i -g wrangler          # if not installed
cd cloudflare
wrangler login             # opens browser, authorizes your Cloudflare account
wrangler secret put DIGEST_WEBHOOK_URL   # optional: paste a Slack/Discord webhook
wrangler deploy
```
That's it — the crons run on Cloudflare's schedule with no server to manage.

## Test manually
After deploy, hit the worker URL with a task param:
```
https://day-trade-scheduler.<your-subdomain>.workers.dev/?task=digest
https://day-trade-scheduler.<your-subdomain>.workers.dev/?task=research
https://day-trade-scheduler.<your-subdomain>.workers.dev/?task=keepwarm
```

## Notes
- No secrets are required except the optional webhook. `BACKEND_URL` is in
  `wrangler.toml`; change it if the backend URL changes.
- The keep-warm ping is what defeats Render free-tier spin-down (idle after ~15
  min). If you move Render to a paid always-on plan you can drop that cron.
