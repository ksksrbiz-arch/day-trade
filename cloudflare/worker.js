/**
 * Cloudflare Worker -- always-on scheduler + watchdog for the paper-trading
 * backend on Render. Uses a SINGLE cron trigger (every 10 min) and dispatches
 * the heavier jobs by time-of-day inside the Worker, so it only consumes one of
 * the account's 5 free cron-trigger slots.
 *
 *   every tick          keep-warm: ping /health so the free Render dyno never
 *                       idles out (which would silently kill the daemons)
 *   ~01:00 UTC daily    nightly research: trigger the deep ML sweep on Render
 *   13:00 UTC weekdays  daily digest: fetch /api/review and forward to a webhook
 *
 * Config (wrangler.toml [vars] / secrets):
 *   BACKEND_URL         Render base (default the known URL)
 *   DIGEST_WEBHOOK_URL  optional Slack/Discord incoming webhook for the digest
 *
 * Manual test:  GET https://<worker>/?task=digest|research|keepwarm
 */

const DEFAULT_BACKEND = "https://day-trade-backend.onrender.com";

async function keepWarm(base) {
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
    await fetch(hook, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: md, content: md.slice(0, 1900) }),
    }).catch(() => null);
  }
  return { task: "digest", ok: true, forwarded: !!hook };
}

async function handle(task, base, env) {
  if (task === "research") return research(base);
  if (task === "digest") return digest(base, env);
  return keepWarm(base);
}

export default {
  // single */10 cron -> keep-warm every tick, plus time-gated heavier jobs
  async scheduled(event, env, ctx) {
    const base = (env && env.BACKEND_URL) || DEFAULT_BACKEND;
    const now = new Date();
    const h = now.getUTCHours();
    const m = now.getUTCMinutes();
    const dow = now.getUTCDay(); // 0=Sun..6=Sat
    const jobs = [keepWarm(base)];
    if (h === 1 && m < 10) jobs.push(research(base)); // ~01:00 UTC, after US close
    if (dow >= 1 && dow <= 5 && h === 13 && m < 10) jobs.push(digest(base, env)); // weekdays 13:00 UTC
    ctx.waitUntil(Promise.all(jobs));
  },
  async fetch(req, env) {
    const base = (env && env.BACKEND_URL) || DEFAULT_BACKEND;
    const task = new URL(req.url).searchParams.get("task") || "keepwarm";
    const out = await handle(task, base, env);
    return new Response(JSON.stringify(out, null, 2), {
      headers: { "content-type": "application/json" },
    });
  },
};
