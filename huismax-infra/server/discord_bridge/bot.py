#!/usr/bin/env python3
"""huismax Discord bridge.

- On start: makes sure the company channels exist in the huismax server.
- Message in #claude-dev (or ALLOWED_CHANNEL_IDS), or any message that @mentions the bot
  -> Hub task for `claude`; the server worker runs it; the result is posted back here.
- !to <agent> <text>   -> Hub task for another agent (muse/chatgpt/grok/gemini/desktop)
- !tasks [agent]       -> recent tasks      !task <id> -> one task
Long results are split under Discord's 2000-char limit, or attached as a .md file.
"""
import asyncio
import io
import json
import os
import urllib.request

import discord
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
HUB = os.environ.get("HUB_URL", "http://127.0.0.1:8787")
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_CHANNEL_ID", "").replace(" ", "").split(",") if x}
DEV_CHANNEL = os.environ.get("DEV_CHANNEL_NAME", "claude-dev")
AGENTS = ("claude", "muse", "chatgpt", "codex", "grok", "gemini", "desktop")
CHANNELS = {
    "huismax": ["general", "announcements", "tasks"],
    "agents": ["muse", "claude-dev", "chatgpt-sales-finance", "grok-research", "gemini-marketing", "desktop"],
    "projects": ["who-is-max", "erpnext", "finance"],
    "lab": ["physical-ai"],
}
LIMIT = 1900

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def hub(method, path, data=None):
    req = urllib.request.Request(HUB + path, method=method,
                                 data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


async def ahub(*a):
    return await asyncio.to_thread(hub, *a)


async def send_long(channel, text, filename="result.md", reference=None):
    text = text or "(empty)"
    if len(text) <= LIMIT:
        return await channel.send(text, reference=reference)
    if len(text) <= LIMIT * 3:
        chunks, rest = [], text
        while rest:
            cut = rest.rfind("\n", 0, LIMIT)
            cut = cut if cut > LIMIT // 2 else LIMIT
            chunks.append(rest[:cut])
            rest = rest[cut:].lstrip("\n")
        for i, c in enumerate(chunks):
            await channel.send(c, reference=reference if i == 0 else None)
        return
    await channel.send(text[:600] + "\n…（完整输出见附件）", reference=reference,
                       file=discord.File(io.BytesIO(text.encode()), filename=filename))


async def ensure_channels(guild):
    for cat_name, names in CHANNELS.items():
        cat = discord.utils.get(guild.categories, name=cat_name)
        try:
            if cat is None:
                cat = await guild.create_category(cat_name)
            for n in names:
                if not discord.utils.get(guild.text_channels, name=n):
                    await guild.create_text_channel(n, category=cat)
        except discord.Forbidden:
            print(f"[{guild.name}] no Manage Channels permission; skipping channel setup", flush=True)
            return


def task_line(t):
    return f"`#{t['id']}` **{t['status']}** → {t['assignee']} · {t['title'][:80]}"


@client.event
async def on_ready():
    print(f"logged in as {client.user} in {[g.name for g in client.guilds]}", flush=True)
    for g in client.guilds:
        await ensure_channels(g)
    client.loop.create_task(deliver_results())


async def deliver_results():
    while not client.is_closed():
        try:
            await ahub("POST", "/agents/discord/heartbeat", {"kind": "bridge"})
            for t in await ahub("GET", "/tasks?notify_pending=discord&limit=20"):
                meta = t.get("meta") or {}
                ch = client.get_channel(int(meta.get("channel_id", 0)))
                if ch:
                    head = f"{'✅' if t['status'] == 'done' else '❌'} 任务 #{t['id']} {t['status']}\n"
                    ref = None
                    if meta.get("message_id"):
                        ref = discord.MessageReference(message_id=int(meta["message_id"]), channel_id=ch.id,
                                                       fail_if_not_exists=False)
                    await send_long(ch, head + (t["result"] or ""), f"task-{t['id']}.md", ref)
                await ahub("PATCH", f"/tasks/{t['id']}", {"notified": 1})
        except Exception as e:
            print(f"deliver error: {e!r}", flush=True)
        await asyncio.sleep(5)


async def create_task(msg, assignee, text):
    t = await ahub("POST", "/tasks", {
        "title": text.splitlines()[0][:120], "body": text, "assignee": assignee,
        "created_by": f"discord:{msg.author.name}",
        "project": msg.channel.name if msg.channel.name in CHANNELS["projects"] else "",
        "meta": {"notify": "discord", "channel_id": msg.channel.id, "message_id": msg.id}})
    await msg.add_reaction("⏳")
    await msg.channel.send(f"已登记 {task_line(t)}", reference=msg)


@client.event
async def on_message(msg):
    if msg.author.bot or not msg.guild:
        return
    content = msg.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    try:
        if content.startswith("!tasks"):
            parts = content.split()
            q = f"&assignee={parts[1]}" if len(parts) > 1 else ""
            ts = await ahub("GET", f"/tasks?limit=15{q}")
            return await send_long(msg.channel, "\n".join(task_line(t) for t in reversed(ts)) or "暂无任务")
        if content.startswith("!task "):
            t = await ahub("GET", f"/tasks/{int(content.split()[1])}")
            return await send_long(msg.channel, f"{task_line(t)}\n\n{t['body']}\n\n**结果**\n{t['result'] or '-'}")
        if content.startswith("!to "):
            _, agent, *rest = content.split(maxsplit=2)
            if agent.lower() not in AGENTS or not rest:
                return await msg.channel.send(f"用法: `!to <{'|'.join(AGENTS)}> 内容`")
            return await create_task(msg, agent.lower(), rest[0])
        in_dev = msg.channel.id in ALLOWED or msg.channel.name == DEV_CHANNEL
        if (in_dev or client.user in msg.mentions) and content:
            await create_task(msg, "claude", content)
    except Exception as e:
        await msg.channel.send(f"⚠️ Hub 出错: {e!r}")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN missing in discord_bridge/.env")
    client.run(TOKEN)
