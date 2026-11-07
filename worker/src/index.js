// Cloudflare Worker: receives Telegram messages instantly (webhook), keeps them in a
// small queue, and asks GitHub to run the tracker right away instead of waiting for the
// next 5-minute check. The GitHub run collects queued commands from /pending and
// confirms them with /ack.
//
// Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WEBHOOK_SECRET,
//                                QUEUE_SECRET, GITHUB_TOKEN
// Vars (wrangler.toml):          GITHUB_REPO, GITHUB_WORKFLOW
import { DurableObject } from "cloudflare:workers";

export class CommandQueue extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(
      "CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, body TEXT NOT NULL, added INTEGER NOT NULL)"
    );
  }

  push(update) {
    this.sql.exec(
      "INSERT OR IGNORE INTO updates (id, body, added) VALUES (?, ?, ?)",
      update.update_id, JSON.stringify(update), Date.now()
    );
    // Forget anything older than 2 days that was never collected.
    this.sql.exec("DELETE FROM updates WHERE added < ?", Date.now() - 2 * 86400 * 1000);
  }

  pending() {
    return this.sql.exec("SELECT body FROM updates ORDER BY id").toArray().map((r) => JSON.parse(r.body));
  }

  ack(maxId) {
    this.sql.exec("DELETE FROM updates WHERE id <= ?", maxId);
  }
}

function queue(env) {
  return env.QUEUE.get(env.QUEUE.idFromName("main"));
}

async function telegram(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!r.ok) console.log(`telegram ${method} failed: ${r.status}`);
}

async function startTrackerRun(env) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "avito-tracker-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
  if (r.status !== 204) console.log(`GitHub dispatch failed: ${r.status} ${await r.text()}`);
}

function authorized(request, env) {
  return request.headers.get("Authorization") === `Bearer ${env.QUEUE_SECRET}`;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Telegram -> Worker
    if (url.pathname === "/telegram" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      const msg = update.message;
      const fromOwner = msg && String(msg.chat?.id) === String(env.TELEGRAM_CHAT_ID);
      if (fromOwner && typeof msg.text === "string" && msg.text.trim().startsWith("/")) {
        await queue(env).push(update);
        ctx.waitUntil(Promise.all([
          // Instant "seen it" reaction so you know the command arrived.
          telegram(env, "setMessageReaction", {
            chat_id: msg.chat.id,
            message_id: msg.message_id,
            reaction: [{ type: "emoji", emoji: "👀" }],
          }),
          startTrackerRun(env),
        ]));
      }
      return new Response("ok"); // always 200, so Telegram doesn't retry
    }

    // GitHub run -> Worker: collect and confirm queued commands
    if (url.pathname === "/pending" && request.method === "GET") {
      if (!authorized(request, env)) return new Response("forbidden", { status: 403 });
      return Response.json(await queue(env).pending());
    }
    if (url.pathname === "/ack" && request.method === "POST") {
      if (!authorized(request, env)) return new Response("forbidden", { status: 403 });
      const { max_update_id } = await request.json();
      if (!Number.isInteger(max_update_id)) return new Response("bad request", { status: 400 });
      await queue(env).ack(max_update_id);
      return new Response("ok");
    }

    return new Response("Avito tracker worker", { status: 404 });
  },
};
