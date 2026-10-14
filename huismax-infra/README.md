# huismax-infra

huismax AI Agent 公司的基础设施：Windows 桌面 ⇄ `huismax` 服务器 ⇄ Google Drive ⇄ Discord。

```
 Discord (#claude-dev, @bot, !to grok ...)          Windows 桌面 Claude
            │                                               │  ssh huismax hx ...
            ▼                                               ▼
   huismax-discord ──► huismax-hub (SQLite: tasks / events / agents) ◄── Muse / Physical AI (POST /events)
                                 ▲   │
                                 │   ▼
                         huismax-worker ──► claude -p (cwd ~/huismax)
                                                   │
                       ~/huismax/shared_drive ◄── huismax-drive (rclone) ──► Google Drive「huismax」
```

## 部署（在 Windows 上，用 PowerShell 执行一次）

```powershell
git clone https://github.com/huismaxx/companion; cd companion\huismax-infra
powershell -ExecutionPolicy Bypass -File windows\deploy.ps1 -Server <服务器IP或Tailscale IP> -User <用户名>
```

如果 `~/.ssh/config` 里已经有 `Host huismax`，`-Server` 和 `-User` 两个参数可以省略。脚本依次做这些事：

1. **ssh**：生成 ed25519 密钥，写入 `Host huismax`（`ServerAliveInterval 60`），安装公钥，然后测试免密登录
2. **server**：上传 `server/` 并运行 `bootstrap.sh`，安装 rclone、fuse3、venv、Claude Code，建立 `~/huismax` 目录，启动 systemd 服务
3. **drive**：在本机运行 `rclone authorize "drive"`（浏览器登录 Google），把 token 写到服务器的 `gdrive` remote（根目录就是 Drive 里的 `huismax` 文件夹），然后挂载到 `~/huismax/shared_drive`
4. **discord**：打开 Developer Portal，粘贴 Bot Token，邀请 Bot 进入 `huismax` 服务器，Bot 会自己建频道
5. **desktop**：创建 `%USERPROFILE%\huismax\CLAUDE.md` 和 `hx.cmd`

想单独重跑某一步，用 `-Step ssh|server|drive|discord|desktop|status`。

## 使用

| 入口 | 做法 |
|---|---|
| Discord `#claude-dev` | 直接发消息，就会交给服务器 Claude 执行，结果自动回到原频道（超长输出作为 `.md` 附件发送） |
| Discord 任意频道 | `@huismax Claude 内容`，或 `!to grok 调研…`、`!tasks`、`!task 12` |
| Windows Claude / PowerShell | `hx add "…" --to claude --wait`、`hx ls --to desktop -s todo` |
| 服务器 | `hx ls`，`journalctl -u huismax-worker -f` |
| Muse / Physical AI | `POST /events {source, kind, payload}`，`POST /agents/<name>/heartbeat` |
