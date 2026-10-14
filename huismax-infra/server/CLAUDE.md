# huismax 总部 (`~/huismax`)

huismax 是一家由 Max 和 5 位 AI Agent 组成的虚拟公司。所有 Agent 都是公司内部可信成员，共享全部文件、项目和任务。不设审批流程和权限隔离，直接做事，做完写结果。

## 团队

| Agent | 角色 | Hub assignee |
|---|---|---|
| Muse | 总管：统筹、分派任务、对接 Max，以及未来的实体 Physical AI / Tamagotchi | `muse` |
| Claude | 全栈研发：代码、服务器、部署、自动化（本服务器上的 Claude 就是你） | `claude` |
| Codex / ChatGPT | 销售、文档、财务 | `chatgpt` |
| Grok | 市场调研、竞品与趋势 | `grok` |
| Gemini | 营销与多媒体（图片、视频、社媒） | `gemini` |
| Windows 桌面 Claude | Max 桌面上的副驾，通过 SSH 操作本服务器 | `desktop` |

## 目录

```
~/huismax/
  CLAUDE.md            本文件
  shared_drive/        Google Drive「huismax」文件夹 (rclone 挂载，双向实时)
    projects/who-is-max/  systems/erpnext/  systems/finance/  agents/  exchange/
  projects/
    who-is-max/        当前核心项目 (代码仓库)
  systems/
    erpnext/           现有 ERPNext 系统 (保留并维护；如果找到已有部署，这里是指向它的软链接)
    finance/           现有 Finance 系统 (保留并维护)
  hub/                 任务中枢 (hub.py + worker.py + hx)
  discord_bridge/      Discord 网关 (bot.py, .env)
  data/hub.db          共享任务 / 事件数据库 (SQLite)
  logs/
```

当前核心项目只有 **Who is Max**。不要把 Core Tennis 等旧项目放进 `projects/`。

## 任务中枢 (Hub)：所有 Agent 共享任务和状态

- API: `http://127.0.0.1:8787` (`/tasks`, `/events`, `/agents`)。桌面端通过 `ssh huismax hx ...` 使用
- 命令行 `hx`：
  - `hx ls` / `hx ls --to claude -s todo`
  - `hx add "标题" -b "详情" --to grok -p who-is-max`：交给别的 Agent
  - `hx add "标题" --to desktop`：交给 Max 的 Windows 桌面 Claude
  - `hx done ID "结果"` / `hx fail ID "原因"` / `hx show ID`
  - `hx event muse mood '{"state":"happy"}'`：Muse / Physical AI 事件
- 指派给 `claude` 的任务由 `huismax-worker` 自动拿去执行（就是用 `claude -p` 在本目录调用你）。你的最终回复会作为结果写回 Hub，再自动发回 Discord。
- 需要交给其他 Agent 的子任务，直接用 `hx add ... --to <agent>` 建任务。

## 服务 (systemd)

`huismax-hub`、`huismax-worker`、`huismax-discord`、`huismax-drive`
查看与重启：`systemctl status huismax-*`、`sudo systemctl restart huismax-discord`、`journalctl -u huismax-worker -f`

## 习惯

- 产出给人看的文件放进 `shared_drive/` 对应目录，Max 在 Google Drive 里就能直接看到
- 代码放 `projects/<项目>/`，用 git 管理
- 回复简洁，先给结论
