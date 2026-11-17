# PenguinTerm

**Decentralized, cross-platform, multiplayer terminal.**

**[github.com/yeahseven7/penguinterm](https://github.com/yeahseven7/penguinterm)**

PenguinTerm connects Windows and Linux computers into a collaborative terminal
network. Every installation is both a **client** and a **server** — there is no
mandatory central cloud service, no simulated shell, and no fake operating
system. Windows machines run real ConPTY/PowerShell/CMD processes; Linux
machines run real PTY/bash/zsh/fish processes. Friends connect directly to each
other's self-hosted instances over HTTPS/WSS.

> Windows 11 PowerShell ↔ Arch Linux Fish ↔ Ubuntu Bash — everyone types on
> real terminals, on their friends' real machines, with explicit permissions.

---

## Highlights

- **Real terminals, real OSes.** ConPTY on Windows (PowerShell 7, Windows
  PowerShell, CMD, optional WSL2), PTY on Linux (bash, zsh, fish, … auto-detected).
- **Multiplayer.** Rooms, invitation links, roles (OWNER / EDITOR / VIEWER),
  presence, shared sessions with input locking, live viewer counts.
- **Private by default.** Your local terminals are invisible to everyone else.
  Shared sessions are opt-in per session.
- **Secure file transfer.** SHA-256 verified, size-capped, root-confined,
  peer-to-peer between your server and a friend's server. No central storage.
- **Works over TCP** with HTTPS/WSS, and over Tailscale/ZeroTier for CGNAT
  networks. An optional **self-hostable relay** (frame forwarding only) exists
  for cases where direct connection is impossible.
- **Honest diagnostics.** Local listen state, public IP, CGNAT heuristics,
  firewall status — and the diagnostics page will *never* claim your port is
  forwarded just because it is open locally.
- **Dark, minimal UI.** xterm.js frontend, no IDE-in-a-terminal, no bloat.

## What PenguinTerm is NOT

- Not a cloud terminal service — your friends' commands run on *your* machine,
  and yours run on *theirs*. Nothing executes elsewhere.
- Not a Linux simulator — Windows commands never pretend to be Linux commands.
  The optional compatibility layer is a tiny muscle-memory helper, not an emulator.
- Not an online IDE — it is a terminal, on purpose.

---

## Tutorial: start PenguinTerm from a terminal

This guide takes you from a fresh checkout to a running multiplayer terminal,
covering both ways to launch it: **directly, for development**, and
**installed, for daily use**. Every command below is copy-pasteable.

### What you need first

- **Python 3.12 or newer** (3.12, 3.13, 3.14, 3.15 are all fine). Check with:

  ```powershell
  python --version     # Windows
  python3 --version    # Linux / macOS
  ```

  If it's missing or older:

  - **Windows**: install from <https://www.python.org/downloads/> — check
    **“Add python.exe to PATH”** during setup, or use `winget install Python.Python.3.12`.
  - **Linux**: `sudo apt install python3 python3-venv python3-pip` (Debian/Ubuntu),
    `sudo pacman -S python python-pip` (Arch), or your distro's equivalent.

- No npm/Node.js tooling required to *run* the app — but the vendored
  frontend dependencies (xterm.js, ~300KB) are **not committed to git**; they
  are fetched once from the npm registry with `scripts\fetch-vendor.ps1`
  (Windows) or `bash scripts/fetch-vendor.sh` (Linux). Both scripts verify
  SHA-256 hashes of each file. Running it manually once is enough.

- A terminal: PowerShell or Command Prompt on Windows; any shell on Linux.

---

### Method A — run it directly from the terminal (development)

Use this when you've cloned or copied the project and want to hack on it.

**Windows (PowerShell):**

```powershell
git clone https://github.com/yeahseven7/penguinterm.git
cd penguinterm
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # CMD instead: .venv\Scripts\activate.bat
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1   # one-time, ~300KB
python -m backend.main
```

**Linux / macOS (bash, zsh, fish):**

```bash
git clone https://github.com/yeahseven7/penguinterm.git
cd penguinterm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
bash scripts/fetch-vendor.sh           # one-time, ~300KB
python -m backend.main
```

Expected output:

```
PenguinTerm 1.0.0 — decentralized multiplayer terminal
  platform : win32
  bind     : 0.0.0.0:8443  (tls=yes)
  data dir : C:\Users\you\AppData\Local\PenguinTerm
INFO:     Started server process [xxxx]
INFO:     Application startup complete.
INFO:     Uvicorn running on https://0.0.0.0:8443
```

Then:

1. Open `https://localhost:8443` in your browser. You'll get a
   **certificate warning** — that's expected: PenguinTerm auto-generates a
   self-signed certificate on first run (`<data dir>\tls\cert.pem` / `key.pem`).
   Click **Advanced → Proceed**. To use a real certificate, drop your own
   `cert.pem` and `key.pem` into that folder instead, or point a reverse proxy
   (Caddy/nginx) with a proper cert at it.
2. **Create the owner account** (first-run setup screen). This computer
   becomes your server; your account is the OWNER.
3. Click **Open a terminal**. You now have a real local shell —
   PowerShell/CMD on Windows, bash/zsh/fish on Linux — running on your OS.
   Type `ls`/`dir`, create files, anything. It is not a simulation.

**Useful variants:**

```powershell
python -m backend.main --port 3000          # different port
python -m backend.main --port 3000 --no-tls # plain HTTP — development only
python -m backend.main --host 127.0.0.1     # listen on localhost only
$env:PENGUINTERM_DATA_DIR = "D:\PenguinTermData"   # custom data dir (Windows)
PENGUINTERM_DATA_DIR=/srv/penguinterm python -m backend.main   # (Linux)
```

`--no-tls` is fine on your own machine for development; never expose it.

**To stop it**: press `Ctrl+C` in the terminal. (When it runs as a background
service instead — see Method B — use the service manager.)

> `python -m backend.main` only works from the project root (the folder that
> contains `backend/`). Run it from anywhere else and you'll get
> `No module named backend` — `cd` back first.

---

### Method B — install it properly (daily use)

The installers set up an isolated Python environment, generate a config file,
register background start, and write an uninstaller. You never edit source
files.

**Windows 10/11:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

No arguments are needed. It will:

1. find (or offer to install via winget) Python 3.12+,
2. create `%LOCALAPPDATA%\PenguinTerm` with `app\` + `venv\` + `tls\`,
3. **fetch the vendored frontend dependencies** (xterm.js, pinned + SHA-256 verified) if missing,
4. install all dependencies into that venv,
5. generate `config.json` (if you already have one, it's kept),
6. register a per-user Scheduled Task **“PenguinTerm”** that starts the
   server at every logon (limited privileges),
7. ask whether to add a Windows Firewall inbound rule for TCP 8443 (needs admin),
8. print exactly how to launch PenguinTerm.

Overrides (optional, e.g. in an admin-less setup or on a custom port):

```powershell
$env:PENGUINTERM_PORT = '9090'              # default 8443
$env:PENGUINTERM_PREFIX = 'D:\PenguinTerm'  # default %LOCALAPPDATA%\PenguinTerm
$env:PENGUINTERM_NO_STARTUP_TASK = '1'      # don't register the logon task
$env:PENGUINTERM_NO_FIREWALL_PROMPT = '1'   # skip the firewall prompt
```

**Start it after installing** — pick one:

```powershell
# 1. just click on it / run the wrapper directly (what the Scheduled Task runs):
start-penguinterm.cmd              # %LOCALAPPDATA%\PenguinTerm\start-penguinterm.cmd

# 2. or launch it in a visible terminal to watch the logs:
set PENGUINTERM_DATA_DIR=%LOCALAPPDATA%\PenguinTerm
cd /d %LOCALAPPDATA%\PenguinTerm\app
venv\Scripts\python.exe -m backend.main

# 3. or via the task scheduler by name:
schtasks /run /TN PenguinTerm
```

**Linux (Arch, Ubuntu, Debian, Fedora, Mint, …):**

```bash
bash scripts/install-linux.sh                  # installs + enables a user service
bash scripts/install-linux.sh --no-service     # install only, start manually
bash scripts/install-linux.sh --port=9090      # custom port
```

It copies the app to `~/.local/share/penguinterm/app`, **fetches the vendored
frontend dependencies** (xterm.js, pinned + SHA-256 verified) if missing,
creates a venv, installs deps, generates `~/.local/share/penguinterm/config.json`,
and installs a systemd **user** service:

```bash
systemctl --user start penguinterm          # start now
systemctl --user enable --now penguinterm   # start now + at login
systemctl --user status penguinterm         # is it running?
journalctl --user -u penguinterm -f         # live logs
systemctl --user restart penguinterm
systemctl --user stop penguinterm
```

(The installer also enables `loginctl linger` so the server survives logout.)

---

### Verify it's actually running

Don't trust “it started” — probe it:

```powershell
curl.exe -sk https://127.0.0.1:8443/api/health        # Windows (skip cert verify)
```
```bash
curl -k https://127.0.0.1:8443/api/health             # Linux
```

Expected: `{"ok":true,"service":"PenguinTerm","version":"1.0.0","platform":"win32",...}`.

Then open `https://localhost:8443` (or `https://<LAN-IP>:8443` from another
device on your network, or your DDNS/Tailscale address from outside).

---

### First 5 minutes inside the app

1. **Sign in** with the owner account you created.
2. **`+ New terminal`** (top right) — pick your shell: PowerShell 7 / Windows
   PowerShell / CMD / WSL on Windows; bash/zsh/fish on Linux. The tab bar now
   shows `LOCAL  yourshell`.
3. Type commands. `Ctrl+Shift+T` opens another tab; `Ctrl+Tab` switches;
   `Ctrl+Shift+W` closes. Each tab is an independent shell process with its
   own cwd and history.
4. **Open a shared session** (`+ New terminal → Shared session`): everyone on
   this server can watch and (Editors, unless you lock input) type in it.
   Your locals stay private. The sidebar shows `1 watching` next to a shared
   session — live.
5. **Let a friend in**: sidebar → *Users → Create invitation* → pick role
   (Viewer/Editor) → copy the link, send it to them. They open
   `https://<your-address>:8443/?invite=…`, create credentials, and they're on
   your server. Firewall + port forwarding (or Tailscale) required for
   non-LAN friends — see **Connecting to your friends** below.

---

### Common startup problems (and fixes)

| Symptom                                           | Cause & fix |
|---------------------------------------------------|-------------|
| `No module named backend`                         | You're not in the project root. `cd` into the folder that contains `backend/`. |
| `pip install` fails building **PyYAML**           | Outdated requirements file — `uvicorn[standard]` pulled PyYAML, which doesn't build on Python 3.15. This repo now uses plain `uvicorn`; make sure your `requirements.txt` contains `uvicorn>=0.30` without `[standard]`, then re-run `pip install -r requirements.txt` (or the installer). |
| Installer aborts with “Cannot convert value … SwitchParameter” | Leftover argument from the old installer CLI. The current installer takes **no arguments** — just run `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1`. |
| `Python 3.12+ is required`                        | Install a newer Python (see prerequisites) and re-run. On Windows make sure it's on PATH; the installer can also install it via winget. |
| `Address already in use` / port taken             | Something else is on 8443. Use `--port 9090` (or set `$env:PENGUINTERM_PORT`), or stop the other process: Windows `netstat -ano \| findstr :8443` then `taskkill /PID <pid> /F`. |
| Browser says “Your connection is not private”     | Self-signed cert. Accept it once per machine, or replace `cert.pem`/`key.pem` in the data dir with your own, or use `--no-tls` on localhost only. |
| `ModuleNotFoundError: No module named 'winpty'`   | Windows shell support needs `pywinpty` (it's in `requirements.txt`). On non-Windows it's intentionally skipped. Re-run `pip install -r requirements.txt`. |
| Friends can reach the page but the terminal never connects | The page loaded over HTTPS/WSS but the WebSocket is blocked — usually the firewall. Windows: `netsh advfirewall firewall add rule name="PenguinTerm (TCP 8443)" dir=in action=allow protocol=TCP localport=8443`. Linux: `sudo ufw allow 8443/tcp`. |
| Scheduled task exists but PenguinTerm doesn't start at logon | Check it: `schtasks /query /TN PenguinTerm /V /FO LIST`. Make sure `...\PenguinTerm\start-penguinterm.cmd` exists. |
| WSL doesn't show up in the new-tab menu           | WSL not found/`wsl.exe` missing — install it with `wsl --install` and restart PenguinTerm. |
| Service won't start on Linux (`systemctl --user`) | No user session/systemd — use `--no-service` and start manually, or enable lingering: `sudo loginctl enable-linger $USER`. |
| “401 Unauthorized” loop after reinstall           | The data dir changed or the token expired (14-day tokens). Sign in again. Tokens live in `penguinterm.db` in the data dir. |

---

### Where everything lives (so you can troubleshoot)

- **Data directory** — `%LOCALAPPDATA%\PenguinTerm` (Windows) or
  `~/.local/share/penguinterm` (Linux); override with `PENGUINTERM_DATA_DIR`.
  Contains `config.json`, `penguinterm.db`, `tls\`, `transfers\`, `install.token`.
- **Logs** — install service logs via `journalctl --user -u penguinterm -f`;
  in-app: **Server settings → Connection log**.
- **Health check** — `GET /api/health` returns `{"ok":true,...}`; the
  frontend shows “connected” when both HTTP and the `/ws/events` channel work.

## Installation (users)

### Linux (Arch, Ubuntu, Debian, Fedora, Mint, …)

```bash
./scripts/install-linux.sh
```

Per-user install into `~/.local/share/penguinterm` with a **systemd user
service** (`penguinterm.service`, auto-start at login, restart on failure),
a generated `config.json`, and an `uninstall.sh`. `--no-service` skips the
service; `--uninstall` removes everything.

### Windows 10/11

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

Per-user install into `%LOCALAPPDATA%\PenguinTerm`, a virtual environment,
optional **Scheduled Task** ("PenguinTerm", starts at logon, limited
privileges) and an optional **inbound firewall rule** for the port.
Uninstall with `...\uninstall.ps1` (full path is printed at the end of the install, under `%LOCALAPPDATA%\PenguinTerm`), or `scripts\uninstall-windows.ps1`.

Both installers generate configuration — **you never edit source files.**

---

## Connecting to your friends (no central server)

Every PenguinTerm instance listens on TCP with HTTPS/WSS. Pick one:

### 1. Direct connection (port forwarding)

1. Choose a port (default `8443`) and configure it in Server settings.
2. **Forward TCP 8443 on your router** to this machine (e.g.
   `router admin → Port Forwarding → 8443 → 192.168.x.y`).
3. Give friends your address: `https://yourname.ddns.net:8443` (any DDNS /
   dynamic DNS service works — PenguinTerm does not care which one).
4. Open **Diagnostics** — it shows local IPs, public IP, firewall status and
   CGNAT heuristics. Note: the diagnostics page can only verify the *local*
   socket; proving external reachability needs an external probe, so it will
   not lie to you about it.
5. Friends open the link, sign in with the account you created for them (or
   register with your **invitation link** — see Users → Create invitation).

Firewall notes: ufw → `sudo ufw allow 8443/tcp` · firewalld →
`sudo firewall-cmd --add-port=8443/tcp --permanent && sudo firewall-cmd --reload`
· Windows → the installer offers the rule; manual command:
`netsh advfirewall firewall add rule name="PenguinTerm (TCP 8443)" dir=in action=allow protocol=TCP localport=8443`

### 2. Private networking (CGNAT-friendly, no forwarding)

Install **Tailscale** (recommended) or **ZeroTier** on both machines. No port
forwarding is needed at all. Friends use your `100.x.y.z` (Tailscale) or your
ZeroTier network address: `https://100.x.y.z:8443`. PenguinTerm detects
Tailscale/ZeroTier interfaces in Diagnostics and shows them as connection
options. This is the right choice for CGNAT connections (where port forwarding
is impossible).

### 3. Optional relay (self-hostable, last resort)

If direct connection and private networking are both unavailable, run the
bundled relay somewhere reachable and connect both ends to it:

```bash
python -m backend.networking.relay --host 0.0.0.0 --port 9443
```

The relay exchanges an ephemeral **rendezvous code** between a HOST and a
GUEST and forwards opaque frames between them. It:

- **never executes commands**,
- **never stores files** or terminal output,
- **does not understand** the PenguinTerm protocol.

Trust model: a relay operator can observe traffic metadata, and unless
end-to-end encryption is layered on top, can also read terminal content. Run
your own relay and use WSS — or prefer Tailscale/ZeroTier. The relay is never
required and is disabled by default.

---

## Multiplayer model

Every install is client + server. On **your** server, you are the OWNER.
People you invite get accounts with roles:

| Role   | Can do                                                        |
|--------|---------------------------------------------------------------|
| OWNER  | Everything: settings, users, invites, shared sessions, files  |
| EDITOR | Join shared sessions & type (unless the owner locks input), transfer files |
| VIEWER | Watch shared sessions. Cannot type, cannot transfer files     |

New accounts are created **by the owner** (Users → + User) or via **single- or
multi-use invitation links** (Users → Create invitation). There is no open
registration.

**Shared sessions** are created by the owner ("+ New terminal → Shared
session"). Everyone on the server can watch live; EDITOR+ can type unless the
owner clicks **Lock input** (session becomes owner-only typing). VIEWERs never
get keystrokes through, enforced server-side.

**Private terminals** (kind=LOCAL) are visible and usable only by their
creator. Remote users can never spawn a local shell on your machine unless you
explicitly enable `remote_create_sessions` (default: off).

Cross-machine collaboration works by **joining each other's PenguinTerm
servers directly**: save a friend's server in **Servers → + Add**, then
"Open UI" launches their full PenguinTerm in a new tab — the same multiplayer
feature set, scoped to their machine, with their shell and their OS.

## Rooms

Rooms are named collaboration groups on a server (e.g. "gpu-rig"). Create one,
share the **join code**, teammates enter it under Rooms. Invitation links can
also be scoped to a room.

## File transfer

- Upload via the **Files** panel (button "Send file"); files land in this
  server's **transfer inbox** — never in your home directory implicitly.
- Download any inbox/transfer-history file, or an explicitly **shared
  directory** (Server settings → Shared directories).
- Every transfer computes and records **SHA-256**; downloads expose it in
  `X-Sha256`; uploads can present it for verification.
- **Send to a friend**: from Files → ➤, pick a saved server, enter *your
  credentials on that server* (used once, **never stored**) — the file is
  pushed to their inbox over the same HTTPS/WSS channel. No central service
  touches it.
- Limits: `max_upload_bytes` (default 512 MB), inbox/outbox + explicit shared
  roots only, symlink escapes refused, collisions auto-renamed (`name (1).ext`),
  existing files never silently overwritten.

## Security model (read this)

Remote shell access is powerful. PenguinTerm's posture, honestly stated:

1. **Identity & approval.** Only the owner creates accounts or invitations.
   Blocked users cannot authenticate. Login attempts are rate-limited.
2. **Restricted mode (default on for remote/non-owner sessions).**
   - Environment scrubbing (secrets like `*TOKEN*`/`*KEY*` are removed).
   - **PATH shim interposition**: `sudo`, `shutdown`, `mkfs`, `diskpart`, …
     resolve to refusal shims under restricted sessions (external commands
     only — shell builtins cannot be intercepted, which is why this is not
     the primary barrier).
   - **Resource limits.** Linux: `setrlimit` (CPU time, address space, file
     size, process count) + `umask 077` in the forked child. Windows: Job
     Object with process/memory caps (kill-on-close), best-effort via Win32.
   - **Cwd jail** for remote sessions (default: the server owner's home).
   - Risky-command heuristics (`rm -rf /`, `sudo`, `mkfs`, fork bombs…) are
     **logged** as security events. This is defense-in-depth telemetry, not a
     command blacklist and never the only control.
3. **The real boundary is your OS.** Run the remote-facing side of PenguinTerm
   under a **dedicated unprivileged system account**, and consider further
   isolation: Linux — filesystem namespaces/bubblewrap/firejail, no sudoers
   entries, resource-restricted units; containers (Docker/Podman) are a very
   reasonable way to host untrusted editors. Windows — run as a standard
   (non-admin) account; remote sessions are **never** spawned elevated.
   Elevation is always the owner's explicit decision.
4. **Encryption.** HTTPS/WSS (TLS) is on by default with an auto-generated
   self-signed certificate (replace with your own in the TLS data dir).
   Sensitive payloads (terminal streams, files) are encrypted in transit.
5. **No silent egress.** The server phones home nowhere. Public-IP lookup is
   a single configurable HTTPS request (default `https://api.ipify.org`,
   disable by pointing it at `""` — or accept "unknown" in Diagnostics).

If you expose this to the internet: use a strong owner password, keep
`restricted_mode: true`, prefer Tailscale/ZeroTier over raw port forwarding,
and run the server as an unprivileged user.

## Compatibility layer (optional, off by default)

For muscle memory only — **native commands always win**, flags are not
translated, and the other OS is never emulated:

| Action         | Linux→Windows | Windows→Linux |
|----------------|---------------|---------------|
| List files     | `ls` → `dir`  | `dir` → `ls`  |
| Clear          | `clear` → `cls` | `cls` → `clear` |
| Remove file    | `rm` → `del`  | `del` → `rm`  |
| Copy / Move    | `cp`/`mv` → `copy`/`move` | `copy`/`move` → `cp`/`mv` |

Enable via Server settings → "Compat layer". Works two ways: a PATH shim
directory (covers bare commands in CMD/bash/fish) and complete-line
translation when the browser submits a full line.

## Keyboard shortcuts

| Shortcut             | Action              |
|----------------------|---------------------|
| Ctrl+Shift+T         | New terminal        |
| Ctrl+Shift+W         | Close tab           |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous tab |
| Ctrl+Shift+F         | Search output       |
| Ctrl+= / Ctrl+-      | Font size           |
| Double-click tab     | Rename session      |
| Esc                  | Close search/modal  |

## CLI

```
python -m backend.main                 # serve with config (TLS on)
python -m backend.main --port 9000     # override port
python -m backend.main --no-tls        # plain HTTP (development only)
python -m backend.networking.relay     # run the optional relay (port 9443)
```

State lives in the data directory — `%LOCALAPPDATA%\PenguinTerm` (Windows),
`~/.local/share/penguinterm` (Linux), overridable with `PENGUINTERM_DATA_DIR`.
It contains `config.json`, `penguinterm.db` (SQLite), `tls/`, `transfers/`,
`shim/`, and a rotating `install.token`.

---

## Project structure

```
penguinterm/
├── backend/
│   ├── main.py                # FastAPI app, REST + WebSocket gateway
│   ├── config.py              # data dir + JSON configuration
│   ├── db.py                  # SQLite (users, tokens, rooms, transfers, logs)
│   ├── shells.py              # OS shell detection (Windows/Linux)
│   ├── compat.py              # optional Linux↔Windows muscle-memory layer
│   ├── tls.py                 # self-signed cert generation
│   ├── events.py              # pub/sub bus for /ws/events
│   ├── terminal/              # ConPTY (Windows) / PTY (Linux), session manager
│   ├── authentication/        # accounts, roles, invites, PBKDF2 + tokens
│   ├── permissions/           # role hierarchy checks
│   ├── security/              # env scrub, PATH shims, limits, path safety
│   ├── file_transfer/         # streaming transfers, SHA-256, root jail
│   ├── multiplayer/           # rooms, invites
│   └── networking/            # diagnostics, discovery, optional relay
├── frontend/                  # vanilla HTML/CSS/JS (xterm.js vendored on demand, see fetch-vendor)
├── scripts/install-linux.sh   # Linux installer (venv + systemd user unit)
├── scripts/install-windows.ps1# Windows installer (venv + task + firewall)
├── scripts/fetch-vendor.ps1   # Windows: fetch pinned xterm.js + SHA-256 verify
├── scripts/fetch-vendor.sh    # Linux: same, POSIX-sh
├── scripts/uninstall-windows.ps1 # Windows uninstaller (zero args)
├── config/penguinterm.example.json
├── tests/                     # pytest unit tests + HTTP/WS end-to-end harnesses
└── requirements.txt
```

## Testing

```bash
pip install -r requirements-dev.txt

# unit tests (no server needed)
python -m pytest tests/ -v

# end-to-end against a running instance (spawns REAL shells)
python -m backend.main --no-tls --port 8899 &
python tests/smoke_e2e.py http://127.0.0.1:8899
python tests/shared_session_e2e.py http://127.0.0.1:8899
```

Covered by automated tests on Windows: owner setup/login, shell detection,
ConPTY spawn + echo round-trip, local/shared/viewer permission enforcement,
input locking, private-session isolation, upload/download with SHA-256,
invite + user management, rooms, diagnostics, TLS auto-generation.

**Stated honestly:** the Linux PTY backend (`backend/terminal/linux.py`) is
written to the standard `pty`/`fork` contract and passes static review, but
was not executed on a Linux machine in this environment — run the same
commands on Linux/macOS to exercise it. Multi-distro shell detection reads
`/etc/shells` + `$PATH`. WSL2 detection exists in the Windows installer path.
Cross-machine Windows↔Linux sessions were validated here as
Windows→(browser)→localhost; the peer protocol itself is OS-agnostic.

## Contributing

Issues and PRs welcome. Keep the security model intact: never weaken
restricted mode defaults, never bypass the transfer root jail, never log
secrets. Run `python -m pytest` before submitting.

## License

MIT (see `LICENSE`). xterm.js vendored under MIT (c) xterm.js contributors.
