#!/bin/sh
# Снимок настроек роутера в ../mirror (пути как на роутере) + свежий полный бэкап в ../backups.
# Роутер — источник правды; mirror только для чтения и сравнения (diff -r со старой копией).
#   ./scripts/pull-from-router.sh            — через Tailscale (работает откуда угодно)
#   HOST=openwrt ./scripts/pull-from-router.sh — из дома по LAN
# Секреты (.clash-secret, ключи Tailscale/SSH, пароль Wi-Fi) сюда намеренно не попадают.
set -e
HOST=${HOST:-openwrt-ts}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MIRROR="$ROOT/mirror"
BACKUPS="$ROOT/backups/auto"
KEEP=2   # сколько автобэкапов держать; памятные бэкапы лежат в backups/<дата-событие>/ и не трогаются

FILES="
/etc/sing-box/config.json
/etc/sing-box/rule-set/my-direct.json
/etc/sing-box/rule-set/my-direct-ip.json
/etc/sing-box/rule-set/my-proxy.json
/etc/sing-box/rule-set/my-proxy-ip.json
/etc/init.d/sing-box
/etc/init.d/zram
/etc/init.d/tailscale
/etc/config/sing-box
/etc/config/dhcp
/etc/config/network
/etc/config/firewall
/etc/config/system
/etc/config/https-dns-proxy
/etc/config/tailscale
/etc/avahi/avahi-daemon.conf
/etc/crontabs/root
/etc/sysupgrade.conf
/etc/sysctl.conf
/etc/rc.local
/etc/apk/world
/usr/bin/update-vless.py
/usr/bin/apply-vless.sh
/usr/bin/sb-watchdog.sh
/usr/bin/sb-ping
/usr/bin/sb-route-test
/usr/bin/health.sh
/usr/bin/router-backup.sh
/usr/bin/update-rulesets.sh
/usr/bin/mem
/usr/bin/ts-watchdog.sh
/usr/bin/sb-restart.sh
/usr/lib/sb-common.sh
/usr/bin/vpn
/usr/bin/router-speedtest.sh
/usr/bin/router-report
"

echo "Снимаю файлы с $HOST..."
rm -rf "$MIRROR.new"
mkdir -p "$MIRROR.new"
# Одним tar по ssh; пути относительные от /. Отсутствующий файл не валит остальные.
REL=$(echo "$FILES" | sed -n 's#^/##p' | tr '\n' ' ')
ssh "$HOST" "cd / && tar -cf - $REL 2>/dev/null" | tar -xf - -C "$MIRROR.new" || true
# Список версий и состояния — чтобы не лезть на роутер ради простых вопросов.
ssh "$HOST" 'echo "# Снято: $(date -u +%Y-%m-%dT%H:%MZ)"
echo "sing-box: $(sing-box version | head -1)"
cat /etc/openwrt_release | grep -E "DISTRIB_(RELEASE|REVISION)"
echo; echo "## uptime / память"; uptime; free -m
echo; echo "## overlay"; df -h /overlay | tail -1
echo; echo "## health.log (последние строки)"; tail -5 /tmp/health.log 2>/dev/null
echo; echo "## ip rule"; ip rule
echo; echo "## пакеты (apk world)"; wc -l < /etc/apk/world' > "$MIRROR.new/STATE.txt" 2>&1 || true

rm -rf "$MIRROR"
mv "$MIRROR.new" "$MIRROR"
echo "mirror обновлён: $(find "$MIRROR" -type f | wc -l | tr -d ' ') файлов"

echo "Делаю свежий полный бэкап на роутере..."
F=$(ssh "$HOST" /usr/bin/router-backup.sh)
mkdir -p "$BACKUPS"
scp -q -O "$HOST:$F" "$BACKUPS/"
ls -t "$BACKUPS"/router-*.tar.gz | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old"; done
echo "Бэкап: $BACKUPS/$(basename "$F")"
