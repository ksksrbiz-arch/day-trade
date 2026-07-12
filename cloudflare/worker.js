/**
 * Cloudflare Worker -- the always-on scheduler + watchdog for the paper-trading
 * backend on Render. Cloudflare's free Cron Triggers do the three things the
 * single Render dyno can't do reliably on its own:
 *
 *   1) keep-warm      ping /health every ~10 min so the free dyno never idles
 *                     out (which would silently kill the daemons).
 *   2) nightly research  after the US close, trigger the deep ML sweep on Render
 *                     (heavy compute stays on Render; Cloudflare just schedules).
 *   3) daily digest   fetch /api/digest pre-market and forward it to a
 *                     Slack/Discord webhook so you get a push summary + alerts.
 *
 * Config (wrangler.toml [vars] / secrets):
 *   BACKEND_URL         Render base (default the known URL)
 *   DIGEST_WEBHOOK_URL  optional Slack/Discord incoming webhook for the digest
 *
 * Manual trigger for testing:  GET https://<worker>/?task=digest|research|keepwarm
 */

const DEFAULT_BACKEND = "https://day-trade-backend.onrender.com";

async function keepWarm(base) {
  // hit a couple of cheap endpoints so the dyno + daemons stay live
  const r = await fetch(base + "/health").catch(() => null);
  await fetch(base + "/api/telemetry/topology").catch(() => null);
  return { task: "keepwarm", ok: !!(r && r.ok) };
}

async function research(base) {
  const r = await fetch(base + "/api/research/run").catch(() => null);
  const body = r ? await r.json().catch(() => ({})) : {};
  return { task: "research", triggered: !!(r && r.ok), body };
}

async function digest(base, env) {
  const r = await fetch(base + "/api/review").catch(() => null);
  if (!r || !r.ok) return { task: "digest", ok: false };
  const d = await r.json().catch(() => ({}));
  const md = d.markdown || "(no digest)";
  const hook = env && env.DIGEST_WEBHOOK_URL;
  if (hook) {
    // Slack uses {text}; Discord uses {content}. Send both keys -- each ignores the other.
    await fetch(hook, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: md, content: md.slice(0, 1900) }),
    }).catch(() => null);
  }
  return { task: "digest", ok: true, forwarded: !!hook };
}

// map cron expressions (from wrangler.toml) to tasks
function taskForCron(cron) {
  if (cron === "30 1 * * *") return "research";      // 01:30 UTC nightly (after US close)
  if (cron === "0 13 * * 1-5") return "digest";      // 13:00 UTC weekdays (pre-market)
  return "keepwarm";                                  // everything else (the */10 ping)
}

async function handle(task, base, env) {
  if (task === "research") return research(base);
  if (task === "digest") return digest(base, env);
  return keepWarm(base);
}

export default {
  async scheduled(event, env, ctx) {
    const base = (env && env.BACKEND_URL) || DEFAULT_BACKEND;
    ctx.waitUntil(handle(taskForCron(event.cron), base, env));
  },
  async fetch(req, env) {
    const base = (env && env.BACKEND_URL) || DEFAULT_BACKEND;
    const task = new URL(req.url).searchParams.get("task") || "keepwarm";
    const out = await handle(task, base, env);
    return new Response(JSON.stringify(out, null, 2), 