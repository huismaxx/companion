#!/usr/bin/env bash
# huismax server bootstrap. Idempotent: safe to re-run.
#   bash bootstrap.sh            install / update everything
#   bash bootstrap.sh drive [FILE]    rclone Google Drive token JSON (file or stdin): configure + mount
#   bash bootstrap.sh discord [FILE]  Discord bot token (file or stdin): write .env, start bot
#   bash bootstrap.sh status     show service + hub status
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HX="$HOME/huismax"
DRIVE_FOLDER_ID="${DRIVE_FOLDER_ID:-1fOCxiMCISWHUrnWAQRCFWhKyl7LFd6tB}"   # Google Drive: My Drive/huismax
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
ME="$(id -un)"
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

log() { printf '\033[1;36m[huismax]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[huismax]\033[0m %s\n' "$*"; }

unit() {  # unit NAME BODY
  echo "$2" | $SUDO tee "/etc/systemd/system/$1.service" >/dev/null
}

install_units() {
  local path="PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
  unit huismax-hub "[Unit]
Description=huismax Hub (shared tasks/events)
After=network-online.target
[Service]
User=$ME
Environment=$path
ExecStart=/usr/bin/python3 $HX/hub/hub.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target"

  unit huismax-worker "[Unit]
Description=huismax server Claude worker
After=huismax-hub.service
Requires=huismax-hub.service
[Service]
User=$ME
WorkingDirectory=$HX
Environment=$path HOME=$HOME
ExecStart=/usr/bin/python3 $HX/hub/worker.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target"

  unit huismax-discord "[Unit]
Description=huismax Discord bridge
After=huismax-hub.service network-online.target
Wants=huismax-hub.service
[Service]
User=$ME
WorkingDirectory=$HX/discord_bridge
Environment=$path
ExecStart=$HX/discord_bridge/.venv/bin/python bot.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target"

  unit huismax-drive "[Unit]
Description=huismax Google Drive mount (rclone)
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
User=$ME
ExecStartPre=-/bin/fusermount3 -uz $HX/shared_drive
ExecStart=/usr/bin/rclone mount gdrive: $HX/shared_drive --vfs-cache-mode writes --dir-cache-time 30s --poll-interval 15s --vfs-cache-max-age 24h --log-level INFO --log-file $HX/logs/drive.log
ExecStop=/bin/fusermount3 -uz $HX/shared_drive
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target"
  $SUDO systemctl daemon-reload
}

link_existing() {  # link_existing TARGET CANDIDATE...
  local target="$1"; shift
  [ -e "$target" ] && return 0
  for c in "$@"; do
    if [ -d "$c" ]; then ln -s "$c" "$target"; log "linked $target -> $c"; return 0; fi
  done
  mkdir -p "$target"
}

cmd_install() {
  log "packages"
  if command -v apt-get >/dev/null; then
    $SUDO apt-get update -qq
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fuse3 python3-venv python3-pip sqlite3 curl unzip git >/dev/null
  fi
  if ! rclone version 2>/dev/null | head -1 | grep -qE 'v1\.(6[0-9]|[7-9][0-9])'; then
    log "installing current rclone"
    curl -fsSL https://rclone.org/install.sh | $SUDO bash >/dev/null || true
  fi
  [ -x /usr/bin/rclone ] || $SUDO ln -sf "$(command -v rclone)" /usr/bin/rclone
  grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null || echo user_allow_other | $SUDO tee -a /etc/fuse.conf >/dev/null

  log "workspace $HX"
  mkdir -p "$HX"/{shared_drive,discord_bridge,hub,projects,systems,data,logs}
  link_existing "$HX/projects/who-is-max" "$HOME/who-is-max" "$HOME/projects/who-is-max" "$HOME/WhoIsMax"
  link_existing "$HX/systems/erpnext" "$HOME/frappe-bench" "/home/frappe/frappe-bench" "/opt/erpnext" "$HOME/erpnext"
  link_existing "$HX/systems/finance" "$HOME/finance" "$HOME/projects/finance" "/opt/finance"
  cp "$KIT/CLAUDE.md" "$HX/CLAUDE.md"
  cp "$KIT"/hub/{hub.py,worker.py,hx} "$HX/hub/"
  chmod +x "$HX/hub/"{hub.py,worker.py,hx}
  $SUDO ln -sf "$HX/hub/hx" /usr/local/bin/hx
  cp "$KIT"/discord_bridge/{bot.py,requirements.txt,.env.example} "$HX/discord_bridge/"
  [ -f "$HX/discord_bridge/.env" ] || cp "$HX/discord_bridge/.env.example" "$HX/discord_bridge/.env"
  chmod 600 "$HX/discord_bridge/.env"

  log "python venv for discord bridge"
  [ -d "$HX/discord_bridge/.venv" ] || python3 -m venv "$HX/discord_bridge/.venv"
  "$HX/discord_bridge/.venv/bin/pip" install -q -U pip -r "$HX/discord_bridge/requirements.txt"

  if ! command -v claude >/dev/null; then
    log "installing Claude Code on server"
    curl -fsSL https://claude.ai/install.sh | bash || warn "Claude Code install failed"
  fi

  log "systemd services"
  install_units
  $SUDO systemctl enable --now huismax-hub huismax-worker >/dev/null
  rclone listremotes 2>/dev/null | grep -q '^gdrive:' && $SUDO systemctl enable --now huismax-drive >/dev/null
  grep -q '^DISCORD_BOT_TOKEN=.\+' "$HX/discord_bridge/.env" && $SUDO systemctl enable --now huismax-discord >/dev/null
  $SUDO systemctl restart huismax-hub huismax-worker
  sleep 2
  cmd_status
}

cmd_drive() {
  local token; token="$(cat "${1:-/dev/stdin}" | tr -d '\r\n')"; [ -n "${1:-}" ] && rm -f "$1"
  echo "$token" | grep -q access_token || { warn "no token JSON on stdin"; exit 1; }
  rclone config delete gdrive 2>/dev/null || true
  rclone config create gdrive drive scope=drive root_folder_id="$DRIVE_FOLDER_ID" token="$token" \
    config_refresh_token=false --non-interactive >/dev/null
  rclone lsd gdrive: >/dev/null && log "Google Drive reachable: $(rclone lsd gdrive: | awk '{print $NF}' | xargs)"
  $SUDO systemctl enable --now huismax-drive >/dev/null
  $SUDO systemctl restart huismax-drive
  sleep 3
  mountpoint -q "$HX/shared_drive" && log "mounted at $HX/shared_drive" || warn "mount not up: journalctl -u huismax-drive"
}

cmd_discord() {
  local token; token="$(cat "${1:-/dev/stdin}" | tr -d '\r\n ')"; [ -n "${1:-}" ] && rm -f "$1"
  [ -n "$token" ] || { warn "no token on stdin"; exit 1; }
  local env="$HX/discord_bridge/.env"
  grep -v '^DISCORD_BOT_TOKEN=' "$env" > "$env.tmp" || true
  echo "DISCORD_BOT_TOKEN=$token" >> "$env.tmp"
  mv "$env.tmp" "$env"; chmod 600 "$env"
  $SUDO systemctl enable --now huismax-discord >/dev/null
  $SUDO systemctl restart huismax-discord
  sleep 6
  $SUDO journalctl -u huismax-discord -n 5 --no-pager | tail -5
}

cmd_status() {
  for s in hub worker drive discord; do
    printf '  %-18s %s\n' "huismax-$s" "$(systemctl is-active huismax-$s 2>/dev/null || true)"
  done
  printf '  %-18s %s\n' "hub" "$(hx health 2>&1 || true)"
  printf '  %-18s %s\n' "drive mount" "$(mountpoint -q "$HX/shared_drive" && echo mounted || echo 'not mounted')"
  printf '  %-18s %s\n' "claude" "$(command -v claude || echo missing)"
}

case "${1:-install}" in
  install) cmd_install ;;
  drive) cmd_drive "${2:-}" ;;
  discord) cmd_discord "${2:-}" ;;
  status) cmd_status ;;
  *) echo "usage: $0 [install|drive|discord|status]"; exit 1 ;;
esac
