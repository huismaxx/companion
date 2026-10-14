# huismax one-shot deploy from Windows (PowerShell 5.1+).
#   powershell -ExecutionPolicy Bypass -File deploy.ps1                         # Host huismax already in ~/.ssh/config
#   powershell -ExecutionPolicy Bypass -File deploy.ps1 -Server 100.x.y.z -User max
#   ... -Step drive | discord | desktop | status                                 # re-run one step
param(
  [string]$Server = "",
  [string]$User = "",
  [ValidateSet("all", "ssh", "server", "drive", "discord", "desktop", "status")]
  [string]$Step = "all"
)
$ErrorActionPreference = "Continue"   # native stderr must not throw in PS 5.1; we check $LASTEXITCODE
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$Kit = Split-Path -Parent $PSScriptRoot          # huismax-infra/
$SshDir = Join-Path $env:USERPROFILE ".ssh"
$Key = Join-Path $SshDir "id_ed25519"
$SshConfig = Join-Path $SshDir "config"
$Desk = Join-Path $env:USERPROFILE "huismax"

function Say($m) { Write-Host "[huismax] $m" -ForegroundColor Cyan }
function Ask($m) { Write-Host "`n>>> $m" -ForegroundColor Yellow }

function Step-Ssh {
  New-Item -ItemType Directory -Force $SshDir | Out-Null
  if (-not (Test-Path "$Key.pub")) {
    Say "generating $Key"
    cmd /c "ssh-keygen -t ed25519 -q -f `"$Key`" -N `"`""
  }
  $hasAlias = (Test-Path $SshConfig) -and (Select-String -Path $SshConfig -Pattern '^\s*Host\s+huismax\s*$' -Quiet)
  if (-not $hasAlias) {
    if (-not $Server -or -not $User) { throw "No 'Host huismax' in ~/.ssh/config: pass -Server <ip> -User <name>" }
    Say "adding Host huismax -> $User@$Server"
    Add-Content -Path $SshConfig -Encoding ascii -Value "`nHost huismax`n  HostName $Server`n  User $User`n  IdentityFile ~/.ssh/id_ed25519`n  ServerAliveInterval 60`n  ServerAliveCountMax 3`n"
  }
  & ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new huismax "true" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Ask "Enter the server password once to install the SSH key (Windows has no ssh-copy-id):"
    Get-Content "$Key.pub" | & ssh huismax "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
  }
  & ssh -o BatchMode=yes huismax "uname -a; which claude || echo 'claude: not installed yet'"
  if ($LASTEXITCODE -ne 0) { throw "passwordless SSH to huismax still failing" }
}

function Step-Server {
  Say "uploading kit"
  & ssh huismax "rm -rf ~/huismax-kit"
  & scp -q -r "$Kit\server" "huismax:huismax-kit"
  if ($LASTEXITCODE -ne 0) { throw "upload failed" }
  Say "running bootstrap (sudo may ask for your server password)"
  & ssh -t huismax "bash ~/huismax-kit/bootstrap.sh"
  if ($LASTEXITCODE -ne 0) { throw "bootstrap failed" }
  & ssh huismax "timeout 120 bash -lc 'claude -p ping' >/dev/null 2>&1"
  if ($LASTEXITCODE -ne 0) {
    Ask "Server Claude is not logged in. A login session opens now: finish the browser login, then type /exit."
    & ssh -t huismax "bash -lc claude"
  }
}

function Get-Rclone {
  $r = Get-Command rclone -ErrorAction SilentlyContinue
  if ($r) { return $r.Source }
  $dir = Join-Path $env:LOCALAPPDATA "rclone"
  $exe = Get-ChildItem $dir -Recurse -Filter rclone.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($exe) { return $exe.FullName }
  Say "downloading rclone"
  $zip = Join-Path $env:TEMP "rclone.zip"
  Invoke-WebRequest "https://downloads.rclone.org/rclone-current-windows-amd64.zip" -OutFile $zip -UseBasicParsing
  Expand-Archive $zip -DestinationPath $dir -Force
  return (Get-ChildItem $dir -Recurse -Filter rclone.exe | Select-Object -First 1).FullName
}

function Step-Drive {
  $rclone = Get-Rclone
  Ask "A browser opens: sign in to the Google account that owns the 'huismax' folder and click Allow."
  $out = & $rclone authorize "drive" 2>&1 | Out-String
  $m = [regex]::Match($out, '\{"access_token"[^\r\n]*\}')
  if (-not $m.Success) { Write-Host $out; throw "no token from rclone authorize" }
  $m.Value | & ssh huismax "umask 077; cat > ~/.huismax-drive-token"
  & ssh -t huismax "bash ~/huismax-kit/bootstrap.sh drive ~/.huismax-drive-token"
}

function Step-Discord {
  Ask @"
Discord (one-time, in the browser that opens):
  1. Create a server named 'huismax' (Discord app: + > Create My Own) if you don't have one.
  2. Developer Portal > New Application 'huismax Claude' > Bot > Reset Token > copy it.
     On the same page enable 'MESSAGE CONTENT INTENT' and Save.
"@
  Start-Process "https://discord.com/developers/applications"
  $sec = Read-Host "Paste the bot token" -AsSecureString
  $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
  $idPart = $token.Split(".")[0]
  $idPart += "=" * ((4 - $idPart.Length % 4) % 4)
  $appId = [Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($idPart.Replace('-', '+').Replace('_', '/')))
  $token | & ssh huismax "umask 077; cat > ~/.huismax-discord-token"
  & ssh -t huismax "bash ~/huismax-kit/bootstrap.sh discord ~/.huismax-discord-token"
  # View/Send/Attach/History/Reactions/Embed/Manage Channels
  $invite = "https://discord.com/oauth2/authorize?client_id=$appId&scope=bot&permissions=117840"
  Ask "Add the bot to the huismax server (page opening). It creates the company channels itself."
  Start-Process $invite
  Say "invite link: $invite"
}

function Step-Desktop {
  New-Item -ItemType Directory -Force $Desk | Out-Null
  Copy-Item "$Kit\windows\CLAUDE.md" "$Desk\CLAUDE.md" -Force
  Set-Content -Path "$Desk\hx.cmd" -Encoding ascii -Value "@ssh huismax hx %*"
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($userPath -notlike "*$Desk*") { [Environment]::SetEnvironmentVariable("Path", "$userPath;$Desk", "User") }
  & ssh huismax "hx event desktop online" | Out-Null
  Say "desktop workspace: $Desk (hx.cmd on PATH; open Claude Code here)"
}

function Step-Status { & ssh huismax "bash ~/huismax-kit/bootstrap.sh status" }

switch ($Step) {
  "ssh" { Step-Ssh } "server" { Step-Server } "drive" { Step-Drive }
  "discord" { Step-Discord } "desktop" { Step-Desktop } "status" { Step-Status }
  "all" { Step-Ssh; Step-Server; Step-Drive; Step-Discord; Step-Desktop; Step-Status }
}
