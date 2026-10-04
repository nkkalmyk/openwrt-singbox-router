#!/bin/sh
# Обновление подписок и применение конфига sing-box.
#   apply-vless.sh          — обновить ноды; перезапуск только если конфиг изменился
#   apply-vless.sh --force  — перезапустить даже без изменений нод (после ручной правки конфига)
# После успешного применения конфиг запоминается как последний рабочий (config.json.good).
# Сброс сети и перезагрузка — только при поломке у нас; если мертвы сами ноды, это провайдеры VPN.
. /usr/lib/sb-common.sh
CONFIG=$SB_CONFIG
REBOOT_STAMP=/etc/sing-box/.last-auto-reboot
REBOOT_MIN_INTERVAL=21600

log() { echo "$*"; logger -t apply-vless "$*"; }
ev() { echo "$*"; event apply-vless "$*"; }

wait_net() {
    for i in 1 2 3 4 5 6; do
        tunnel_ok && return 0
        sleep 5
    done
    return 1
}

# Храним один бэкап: вместе с config.json.good это две копии.
rotate_backups() {
    ls -t "$CONFIG".bak.* 2>/dev/null | tail -n +2 | xargs -r rm -f
}

exec 9>"$SB_LOCK"
flock -n 9 || { log "Уже идёт другой запуск — выхожу."; exit 0; }

FORCE=0
[ "$1" = "--force" ] && FORCE=1

if ! direct_ok; then
    log "Нет интернета у провайдера — ничего не трогаю."
    exit 1
fi

out=$(python3 /usr/bin/update-vless.py 2>&1)
rc=$?
echo "$out"
echo "$out" | logger -t apply-vless

CHANGED=0
case $rc in
    0) CHANGED=1 ;;
    2) ;;
    *) log "Обновление не выполнено (код $rc), конфиг не тронут."
       [ "$FORCE" = 1 ] || exit 1 ;;
esac

if [ "$CHANGED" = 0 ] && [ "$FORCE" = 0 ]; then
    log "Ноды не изменились — перезапуск не нужен."
    exit 0
fi

# Бэкап, который только что сделал update-vless.py (конфиг до обновления).
LAST=""
[ "$CHANGED" = 1 ] && LAST=$(ls -t "$CONFIG".bak.* 2>/dev/null | head -1)

if ! sing-box check -c "$CONFIG"; then
    if [ -n "$LAST" ]; then
        cp "$LAST" "$CONFIG"
        ev "НЕВАЛИДНЫЙ КОНФИГ — откат на $LAST, sing-box не перезапускаю."
    else
        ev "НЕВАЛИДНЫЙ КОНФИГ — sing-box не перезапускаю."
    fi
    exit 1
fi

# Работал ли VPN до перезапуска. Если нет — сбой не из-за нового конфига (провайдер VPN или что-то ещё,
# со всем этим разбирается сторож): тогда не откатываем на старые адреса (они могут быть уже мертвы)
# и не трогаем сеть и роутер. Иначе сторож, обновляя подписки во время сбоя, мог бы довести до перезагрузки.
was_ok=1
tunnel_ok || was_ok=0

if [ "$CHANGED" = 1 ]; then
    ev "Ноды изменились — перезапускаю sing-box"
else
    ev "Перезапуск sing-box (--force)"
fi
sb_restart_locked
if wait_net; then
    ev "Применено, связь есть."
    save_good_config
    rotate_backups
    exit 0
fi

if [ "$was_ok" = 0 ]; then
    ev "VPN не работал и до перезапуска — новый конфиг оставлен, сеть и роутер не трогаю (дальше — сторож)."
    exit 1
fi

if [ -n "$LAST" ]; then
    ev "Связи нет после 30 сек — откатываю конфиг на $LAST."
    cp "$LAST" "$CONFIG"
    sb_restart_locked
    if wait_net; then
        ev "После отката связь есть. Новые ноды нерабочие — оставлен прежний конфиг."
        exit 1
    fi
fi

# Дальше тяжёлые меры. Они имеют смысл, только если поломка у нас, а не у провайдеров.
if ! direct_ok; then
    ev "Пропал интернет у провайдера — сброс сети не поможет."
    exit 1
fi
if api_ok && ! providers_ok; then
    ev "Мертвы все ноды — это провайдеры VPN, не роутер. Сеть и роутер не трогаю."
    exit 1
fi

ev "Ноды живы, а трафик не идёт — пробую мягкий сброс сети..."
/etc/init.d/network restart
sleep 10
sb_restart_locked
if wait_net; then
    ev "Связь восстановлена после network restart."
    save_good_config
    rotate_backups
    exit 0
fi

now=$(date +%s)
last_reboot=$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)
if [ $((now - last_reboot)) -lt "$REBOOT_MIN_INTERVAL" ]; then
    ev "network restart не помог, но автоперезагрузка уже была меньше 6 ч назад — не перезагружаю."
    exit 1
fi
echo "$now" > "$REBOOT_STAMP"
ev "network restart не помог. Перезагружаю роутер через 10 сек..."
sleep 10
reboot
