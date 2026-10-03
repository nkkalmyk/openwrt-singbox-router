#!/bin/sh
# Сторож Tailscale, cron раз в 2 минуты. Удалённый доступ к роутеру должен подниматься сам.
#  - tailscaled не запущен (procd исчерпал попытки) → запустить;
#  - Tailscale не в состоянии Running/Online 3 проверки подряд (6 мин) при живом провайдере →
#    перезапустить tailscaled, не чаще раза в 30 минут;
#  - нужен вход (NeedsLogin) → только запись в журнал: это может сделать лишь человек.
# Состояние берём через локальный сокет tailscaled — без запуска 26-мегабайтного CLI.
# Настройки Tailscale (exit node, auto-update и т.д.) хранятся в его state-файле — здесь их не трогаем.
. /usr/lib/sb-common.sh
STATE=/tmp/ts-watchdog
RESTART_MIN_INTERVAL=1800
SOCK=/var/run/tailscale/tailscaled.sock

mkdir -p "$STATE"

if ! pidof tailscaled >/dev/null; then
    event ts-watchdog "tailscaled не запущен — запускаю"
    /etc/init.d/tailscale start
    exit 0
fi

json=$(curl -s -m 10 --unix-socket "$SOCK" http://local-tailscaled.sock/localapi/v0/status 2>/dev/null)
backend=$(echo "$json" | jsonfilter -e '@.BackendState' 2>/dev/null)
online=$(echo "$json" | jsonfilter -e '@.Self.Online' 2>/dev/null)

if [ "$backend" = "Running" ] && [ "$online" = "true" ]; then
    [ -f "$STATE/bad" ] && [ "$(cat "$STATE/bad")" -ge 3 ] && event ts-watchdog "Tailscale снова в строю"
    rm -f "$STATE/bad" "$STATE/needs_login_logged"
    exit 0
fi

if [ "$backend" = "NeedsLogin" ] || [ "$backend" = "NeedsMachineAuth" ]; then
    [ -f "$STATE/needs_login_logged" ] || event ts-watchdog "Tailscale требует входа ($backend) — нужен человек: tailscale up"
    touch "$STATE/needs_login_logged"
    exit 0
fi

# Без интернета у провайдера Tailscale и не должен работать — перезапуск не поможет.
direct_ok || exit 0

n=$(( $(cat "$STATE/bad" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE/bad"
[ "$n" -ge 3 ] || exit 0

now=$(date +%s)
last=$(cat "$STATE/last_restart" 2>/dev/null || echo 0)
[ $((now - last)) -ge "$RESTART_MIN_INTERVAL" ] || exit 0
echo "$now" > "$STATE/last_restart"
event ts-watchdog "Tailscale не в строю уже $((n * 2)) мин (state=${backend:-нет ответа} online=${online:-?}) — перезапускаю tailscaled"
/etc/init.d/tailscale restart
