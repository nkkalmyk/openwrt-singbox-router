#!/bin/sh
# Безопасный перезапуск sing-box — единственный правильный способ (не /etc/init.d/sing-box restart).
#   sb-restart.sh [причина]           — синхронно, под общей блокировкой
#   sb-restart.sh --detach <причина>  — в фоне (для procd-триггеров: они не ждут долгих команд)
# Причина wan-up: пропуск, если sing-box запущен меньше 30 с назад (так бывает при загрузке).
. /usr/lib/sb-common.sh

if [ "$1" = "--detach" ]; then
    shift
    setsid "$0" "$@" </dev/null >/dev/null 2>&1 &
    exit 0
fi
reason=${1:-вручную}

exec 9>"$SB_LOCK"
if ! lock_wait 120; then
    event sb-restart "($reason) блокировка занята больше 2 мин — пропускаю"
    exit 1
fi

if [ "$reason" = "wan-up" ] && [ "$(sb_uptime)" -lt 30 ]; then
    exit 0
fi

if sb_restart_locked; then
    event sb-restart "Перезапуск ($reason) — готово"
else
    event sb-restart "Перезапуск ($reason) — tun0/ip rules не поднялись"
    exit 1
fi
