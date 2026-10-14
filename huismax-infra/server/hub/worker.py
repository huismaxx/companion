#!/usr/bin/env python3
"""Server Claude worker: takes tasks assigned to `claude` from the Hub and runs them with `claude -p` in ~/huismax."""
import json
import os
import subprocess
import time
import urllib.request

HUB = os.environ.get("HUB_URL", "http://127.0.0.1:8787")
AGENT = os.environ.get("WORKER_AGENT", "claude")
WORKDIR = os.path.expanduser(os.environ.get("WORKER_DIR", "~/huismax"))
TIMEOUT = int(os.environ.get("WORKER_TIMEOUT", "1800"))
TOOLS = os.environ.get("CLAUDE_ALLOWED_TOOLS", "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch")


def call(method, path, data=None):
    req = urllib.request.Request(HUB + path, method=method,
                                 data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def prompt_for(t):
    return (f"[huismax Hub task #{t['id']} from {t['created_by']}"
            f"{', project ' + t['project'] if t['project'] else ''}]\n"
            f"{t['title']}\n\n{t['body']}".strip())


def run(t):
    cmd = ["claude", "-p", prompt_for(t), "--allowedTools", TOOLS]
    try:
        p = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=TIMEOUT,
                           env={**os.environ, "HX_AGENT": AGENT})
        out = (p.stdout or "").strip() or (p.stderr or "").strip() or "(no output)"
        return ("done" if p.returncode == 0 else "failed"), out
    except subprocess.TimeoutExpired:
        return "failed", f"timeout after {TIMEOUT}s"
    except FileNotFoundError:
        return "failed", "`claude` CLI not found on server PATH"


def main():
    print(f"worker {AGENT} polling {HUB}, cwd={WORKDIR}", flush=True)
    while True:
        try:
            call("POST", f"/agents/{AGENT}/heartbeat", {"kind": "server-claude"})
            t = call("POST", "/tasks/claim", {"assignee": AGENT})
            if not t:
                time.sleep(3)
                continue
            print(f"run #{t['id']}: {t['title'][:80]}", flush=True)
            status, out = run(t)
            call("PATCH", f"/tasks/{t['id']}", {"status": status, "result": out})
            print(f"#{t['id']} -> {status}", flush=True)
        except Exception as e:
            print(f"worker error: {e!r}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
