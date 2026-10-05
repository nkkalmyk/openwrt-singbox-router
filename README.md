# Home router: OpenWrt + sing-box split tunnel

**English** · [Русский](README.ru.md)

A home router that sends local (Russian) traffic directly and everything else through VLESS + Reality, and fixes
most failures on its own. This repo holds its docs, a snapshot of its config, and its automation scripts.
I maintain it together with an AI agent (Claude Code); [CLAUDE.md](CLAUDE.md) holds the agent's rules.

- **Hardware:** Xiaomi AX3000T (MT7981, 2 cores, 256 MB RAM + zram), OpenWrt 25.12, behind the ISP's router.
- **sing-box 1.13 in TUN mode:** local sites (geoip-ru, geosite-category-ru, `.ru/.su/.рф`) go direct, the rest goes
  through VLESS + Reality. Two subscriptions from different providers, with automatic selection of a live node.
- **Split DNS inside sing-box:** local domains go to a local resolver directly; the rest go to Cloudflare DoH through
  the tunnel. dnsmasq has two fallbacks: https-dns-proxy, then the upstream router's DNS.
- **Self-healing:** subscriptions refresh every 30 min, with rollback if the new config has no connectivity. A
  watchdog restarts a crashed or hung sing-box, moves off dead nodes, and re-pulls subscriptions when a provider
  rotates server IPs. Restarts are safe (shared lock + post-checks), and there is a persistent event log.
- **Tailscale on the router** for remote access and as an exit node. Its traffic bypasses sing-box (fwmark), so
  remote access survives tunnel crashes.
- **Telegram:** the router messages you when something breaks and when it recovers, and takes commands and buttons
  (`/status`, `/day`, `/restart` with ✅/✖️ confirmation…) from anywhere, no public IP needed; replies are laid out for a
  phone screen. See [docs/telegram.md](docs/telegram.md) (RU).
- Plain busybox `sh` + python3, memory-tuned for 256 MB (`GOMEMLIMIT`).

## Build your own

**[docs/setup.md](docs/setup.md)** walks through it from a fresh OpenWrt: packages, which file goes where, what to
fill in, first start, and what to check if it doesn't work. I tested it by building from scratch on an arm64
OpenWrt VM. *The guide and the other docs are in Russian*; the commands and file paths are universal, and a
browser translator handles the rest.

## What's where

| Path | What |
|---|---|
| [docs/setup.md](docs/setup.md) | Step-by-step build guide (RU) |
| [docs/telegram.md](docs/telegram.md) | Router in Telegram: outage alerts and chat commands, 3-minute setup (RU) |
| [docs/architecture.md](docs/architecture.md) | How it works: traffic path, routing rules, DNS, automation, "what happens if…" (RU) |
| [docs/runbook.md](docs/runbook.md) | Everyday tasks and troubleshooting (RU) |
| [docs/history.md](docs/history.md) | What changed and why, incidents, decisions (RU) |
| [docs/resilience-audit.md](docs/resilience-audit.md) | Resilience audit: the gaps found and how they were closed (RU) |
| [mirror/](mirror/) | Config snapshot with router paths: scripts in `usr/bin`, shared library `usr/lib/sb-common.sh`, configs in `etc` |
| [scripts/](scripts/) | `pull-from-router.sh` takes a snapshot; `router-dnstest.py` tests DNS rules on a temporary sing-box |
| [CLAUDE.md](CLAUDE.md) | Entry point for AI agents: access, golden rules, open questions (`AGENTS.md` is a symlink to it) |
| [publish/publish.py](publish/publish.py) | How this public copy is built and scrubbed of personal data |

## Scripts

On the router (`/usr/bin`; in this repo, [mirror/usr/bin/](mirror/usr/bin/)):

| Script | What it does | Run by |
|---|---|---|
| [vpn](mirror/usr/bin/vpn) | One command for humans: status, nodes, where a site is routed, manual lists, restart, log, speedtest | you |
| [sb-ping](mirror/usr/bin/sb-ping) | Nodes and latency, manual node choice, per-site route, manual list edits, event log | you, via `vpn` |
| [update-vless.py](mirror/usr/bin/update-vless.py) | Fetches subscriptions (through the tunnel, falls back to direct), filters nodes, rebuilds groups and bypass rules | `apply-vless.sh` |
| [apply-vless.sh](mirror/usr/bin/apply-vless.sh) | Applies subscriptions: config check → safe restart → tunnel check → rollback if no connectivity | cron, every 30 min |
| [sb-watchdog.sh](mirror/usr/bin/sb-watchdog.sh) | Watchdog: starts a crashed sing-box, fixes a hung one, moves off dead nodes, re-pulls subscriptions | cron, every minute |
| [sb-restart.sh](mirror/usr/bin/sb-restart.sh) | The only correct way to restart sing-box: under a shared lock, with checks afterwards | you, watchdog, WAN up |
| [sb-common.sh](mirror/usr/lib/sb-common.sh) | Shared library: checks for internet, tunnel, routing and nodes; lock; persistent log | everything |
| [ts-watchdog.sh](mirror/usr/bin/ts-watchdog.sh) | Tailscale watchdog (polls the local socket instead of running the heavy `tailscale` binary) | cron, every 2 min |
| [health.sh](mirror/usr/bin/health.sh) | One line in `/tmp/health.log`: memory, conntrack, Wi-Fi clients, sing-box memory, tunnel and direct checks | cron, every 5 min |
| [update-rulesets.sh](mirror/usr/bin/update-rulesets.sh) | Updates the geoip / geosite databases, installing a new one only after it is verified | cron, weekly |
| [router-backup.sh](mirror/usr/bin/router-backup.sh) | Full `sysupgrade -b` backup, keeps two | cron, weekly |
| [router-speedtest.sh](mirror/usr/bin/router-speedtest.sh) | Speed test through the tunnel and direct, down and up, with CPU load | `vpn speed` |
| [router-report](mirror/usr/bin/router-report) | Health report for the last N hours: current checks, outages, memory, grouped events, verdict | cron, twice a day; `vpn report` |
| [router-notify](mirror/usr/bin/router-notify) | Telegram alerts (tunnel down/back, ISP outage, reboot, important events); with `--bot`, chat commands | cron, every minute; `router-bot` service |
| [router-tg](mirror/usr/bin/router-tg) | Phone-sized reply formatting for the Telegram bot (rewrites `vpn status`, `sb-ping`, the report…) | bot |
| [uptime-hourly](mirror/usr/bin/uptime-hourly) / [router-day](mirror/usr/bin/router-day) | Hourly outage summary (31 days) and its rendering: `vpn day`, `/day` | cron at :58; `vpn day` |
| [sb_ui.py](mirror/usr/lib/sb_ui.py) | Shared output style: node names without flags, colors only in a terminal | sb-ping, router-report, router-day |
| [mem](mirror/usr/bin/mem) | Per-process memory: own (RssAnon) vs code (RssFile) | you |
| [sb-route-test](mirror/usr/bin/sb-route-test) | Opens a list of sites and shows which node and rule each connection took | you |

Usage on the router:

```sh
vpn                      # status: internet, VPN (exit country and node), sing-box, Tailscale, recent events
vpn nodes                # nodes with latency
vpn use 3 / vpn auto     # pin node #3 / back to auto-select
vpn site example.com     # is this site direct or via VPN, and which rule decides it
vpn direct example.ru    # always direct (vpn proxy … — always via VPN, vpn del … — remove)
vpn update               # refresh nodes from subscriptions (restarts only if they changed)
vpn restart              # safe sing-box restart (~6 s without internet)
vpn log                  # what the automation did
vpn day                  # outages hour by hour, yesterday and today (vpn day 7 — a week)
vpn report 24            # health report for the last 24 h: all good / past outages handled / problem now
vpn tg setup             # connect Telegram: alerts and chat commands (/status, /nodes, /restart…)
vpn speed                # speed test, tunnel vs direct
```

The script output and comments are in Russian.

## This is a scrubbed copy

The original lives in a private folder. It gets here through [publish/publish.py](publish/publish.py), which replaces
personal data and refuses to publish if anything is left:

- home network and Tailscale addresses are placeholders: `LAN_IP`, `WAN_IP`, `UPSTREAM_IP`, `TS_IP`…;
- VPN providers are `P1` (main) and `P2` (backup), including the node tag prefixes `P1-…` / `P2-…`;
- [config.json](mirror/etc/sing-box/config.json) has example nodes instead of the real ones (`203.0.113.x`, zero UUID,
  placeholder Reality keys); the Clash API secret is `CHANGE_ME`; subscription URLs are placeholders;
- manual site lists are empty, static DHCP leases are removed, MACs are masked; backups are not published.

So the files won't work if you just copy them to a router: [docs/setup.md](docs/setup.md) shows what to fill in.

## License

[MIT](LICENSE). No warranty: this is a hobby setup, and it touches your network.
