#!/usr/bin/env python3
"""huismax Hub — shared task board + event log for all huismax agents.

Stdlib only (sqlite3 + http.server). One process, one SQLite file.

  GET  /health
  GET  /tasks?status=todo&assignee=claude&project=who-is-max&notify_pending=discord&limit=50
  POST /tasks                {title, body?, assignee?, created_by?, project?, meta?}
  GET  /tasks/<id>
  PATCH /tasks/<id>          {status?, result?, assignee?, notified?, ...}
  POST /tasks/claim          {assignee}  -> oldest todo task for assignee, marked doing (204 if none)
  GET  /events?source=&since_id=&limit=
  POST /events               {source, kind, payload}   # Muse / Physical AI / Tamagotchi entry point
  GET  /agents
  POST /agents/<name>/heartbeat {kind?, info?}
"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = os.environ.get("HUB_DB", os.path.expanduser("~/huismax/data/hub.db"))
HOST = os.environ.get("HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("HUB_PORT", "8787"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT DEFAULT '',
  assignee TEXT DEFAULT 'claude',
  created_by TEXT DEFAULT 'max',
  project TEXT DEFAULT '',
  status TEXT DEFAULT 'todo',          -- todo | doing | done | failed
  result TEXT DEFAULT '',
  meta TEXT DEFAULT '{}',
  notified INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, assignee);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, kind TEXT, payload TEXT DEFAULT '{}', created_at TEXT
);
CREATE TABLE IF NOT EXISTS agents (
  name TEXT PRIMARY KEY, kind TEXT DEFAULT '', info TEXT DEFAULT '{}', last_seen TEXT
);
"""
TASK_FIELDS = ("title", "body", "assignee", "created_by", "project", "status", "result", "meta", "notified")
LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("meta", "payload", "info"):
        if k in d:
            try:
                d[k] = json.loads(d[k] or "{}")
            except ValueError:
                pass
    return d


def enc(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


class Handler(BaseHTTPRequestHandler):
    server_version = "huismax-hub/1"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, obj=None):
        data = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def route(self, method):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip("/") or "/"
        try:
            for pattern, fn in ROUTES.get(method, []):
                m = re.fullmatch(pattern, path)
                if m:
                    return fn(self, q, *m.groups())
            self.send(404, {"error": "not found"})
        except (ValueError, KeyError) as e:
            self.send(400, {"error": str(e)})
        except Exception as e:  # keep the hub alive whatever happens
            self.send(500, {"error": repr(e)})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")


def health(h, q):
    with db() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, count(*) n FROM tasks GROUP BY status")}
    h.send(200, {"ok": True, "time": now(), "tasks": counts})


def list_tasks(h, q):
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    for k in ("status", "assignee", "project", "created_by"):
        if q.get(k):
            sql += f" AND {k} = ?"
            args.append(q[k])
    if q.get("since_id"):
        sql += " AND id > ?"
        args.append(int(q["since_id"]))
    if q.get("notify_pending"):
        sql += " AND notified = 0 AND status IN ('done','failed') AND json_extract(meta, '$.notify') = ?"
        args.append(q["notify_pending"])
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(q.get("limit", 50)))
    with db() as c:
        h.send(200, [row(r) for r in c.execute(sql, args)])


def create_task(h, q):
    b = h.body()
    if not b.get("title"):
        raise ValueError("title required")
    t = now()
    with LOCK, db() as c:
        cur = c.execute(
            "INSERT INTO tasks(title, body, assignee, created_by, project, meta, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (b["title"], b.get("body", ""), b.get("assignee", "claude"), b.get("created_by", "max"),
             b.get("project", ""), enc(b.get("meta", {})), t, t))
        h.send(201, row(c.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()))


def get_task(h, q, tid):
    with db() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
    h.send(200, row(r)) if r else h.send(404, {"error": "no such task"})


def patch_task(h, q, tid):
    b = {k: v for k, v in h.body().items() if k in TASK_FIELDS}
    if not b:
        raise ValueError("nothing to update")
    b["updated_at"] = now()
    with LOCK, db() as c:
        c.execute(f"UPDATE tasks SET {', '.join(k + '=?' for k in b)} WHERE id=?",
                  [enc(v) for v in b.values()] + [int(tid)])
        r = c.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
    h.send(200, row(r)) if r else h.send(404, {"error": "no such task"})


def claim_task(h, q):
    who = h.body().get("assignee") or "claude"
    with LOCK, db() as c:
        r = c.execute("SELECT * FROM tasks WHERE status='todo' AND assignee=? ORDER BY id LIMIT 1", (who,)).fetchone()
        if not r:
            return h.send(204)
        c.execute("UPDATE tasks SET status='doing', updated_at=? WHERE id=?", (now(), r["id"]))
        h.send(200, row(c.execute("SELECT * FROM tasks WHERE id=?", (r["id"],)).fetchone()))


def list_events(h, q):
    sql, args = "SELECT * FROM events WHERE id > ?", [int(q.get("since_id", 0))]
    if q.get("source"):
        sql += " AND source = ?"
        args.append(q["source"])
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(q.get("limit", 50)))
    with db() as c:
        h.send(200, [row(r) for r in c.execute(sql, args)])


def create_event(h, q):
    b = h.body()
    with LOCK, db() as c:
        cur = c.execute("INSERT INTO events(source, kind, payload, created_at) VALUES (?,?,?,?)",
                        (b.get("source", "unknown"), b.get("kind", "event"), enc(b.get("payload", {})), now()))
        h.send(201, {"id": cur.lastrowid})


def list_agents(h, q):
    with db() as c:
        h.send(200, [row(r) for r in c.execute("SELECT * FROM agents ORDER BY name")])


def heartbeat(h, q, name):
    b = h.body()
    with LOCK, db() as c:
        c.execute("INSERT INTO agents(name, kind, info, last_seen) VALUES (?,?,?,?)"
                  " ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, info=excluded.info, last_seen=excluded.last_seen",
                  (name, b.get("kind", ""), enc(b.get("info", {})), now()))
    h.send(200, {"ok": True})


ROUTES = {
    "GET": [(r"/health", health), (r"/tasks", list_tasks), (r"/tasks/(\d+)", get_task),
            (r"/events", list_events), (r"/agents", list_agents)],
    "POST": [(r"/tasks", create_task), (r"/tasks/claim", claim_task), (r"/events", create_event),
             (r"/agents/([\w.-]+)/heartbeat", heartbeat)],
    "PATCH": [(r"/tasks/(\d+)", patch_task)],
}


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)
        c.execute("PRAGMA journal_mode=WAL")
    print(f"huismax hub on http://{HOST}:{PORT}  db={DB_PATH}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
