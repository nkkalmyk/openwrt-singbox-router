#!/bin/sh
# Сторож sing-box, cron раз в минуту. Пока всё работает — молчит.
#  0) sing-box не запущен (procd бросает службу после 5 падений за час) → безопасный запуск,
#     при невалидном конфиге — с возвратом к последнему рабочему; не чаще раза в 5 мин.
#  1) Нет tun0 или ip rules sing-box 2 мин подряд → безопасный перезапуск.
#  2) VPN не работает (Cloudflare не отвечает или видит Россию) при живом провайдере:
#     1-я мин — если выбранная нода не ответила 2 раза подряд, увести группы с неё (kick_groups strict);
#     2-я мин — proxy выбран вручную → вернуть auto; увести группы с мёртвой ноды (дальше раз в 5 мин);
#     3-я мин — обновить подписки: чаще всего VPN пропадает, потому что провайдер сменил адреса серверов
#     (2026-10-04); если адреса те же — apply-vless ничего не перезапускает;
#     с 4-й мин — Clash API молчит или ноды живы, а трафик не идёт (поломка у нас) →
#     безопасный перезапуск, не чаще раза в 30 мин. Если мертвы сами ноды — это провайдер, см. 3–4.
#  2б) Нода для Gemini выбрана вручную и не отвечает → вернуть gemini-auto (проверка раз в 5 мин).
#  3) Все P1 мертвы при живых P2 3 мин подряд (P1 заблокировали) → обновить подписки.
#  4) Мертвы все ноды 5 мин подряд при живом провайдере → обновить подписки (скачаются напрямую).
#     Обновления подписок (2, 3, 4) — не чаще раза в 15 мин.
# Все перезапуски — через sb-restart.sh, обновления — через apply-vless.sh (общая блокировка).
. /usr/lib/sb-common.sh
STATE=/tmp/sb-watchdog
START_MIN_INTERVAL=300
RESTART_MIN_INTERVAL=1800
REFRESH_MIN_INTERVAL=900

mkdir -p "$STATE"
bump() { n=$(( $(cat "$STATE/$1" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/$1"; echo "$n"; }
since() { echo $(( $(date +%s) - $(cat "$STATE/$1" 2>/dev/null || echo 0) )); }
stamp() { date +%s > "$STATE/$1"; }

restart_safely() {
    [ "$(since last_restart)" -ge "$RESTART_MIN_INTERVAL" ] || return 0
    stamp last_restart
    event sb-watchdog "$1 — перезапускаю sing-box"
    /usr/bin/sb-restart.sh watchdog
}

refresh() {
    [ "$(since last_refresh)" -ge "$REFRESH_MIN_INTERVAL" ] || return 0
    stamp last_refresh
    event sb-watchdog "$1 — обновляю подписки"
    /usr/bin/apply-vless.sh >/dev/null 2>&1
    event sb-watchdog "apply-vless.sh завершился с кодом $?"
}

# Предыдущий запуск ещё работает (во время сбоя проверки идут дольше минуты) — не дублируем:
# иначе два сторожа считали бы минуты дважды и могли бы одновременно что-то перезапускать.
exec 8>/tmp/sb-watchdog.run.lock
flock -n 8 || exit 0

# Идёт обновление или перезапуск — не мешаем.
exec 9>"$SB_LOCK"
flock -n 9 || exit 0
flock -u 9

# 0) sing-box не запущен
if ! pidof sing-box >/dev/null; then
    [ "$(since last_start)" -ge "$START_MIN_INTERVAL" ] || exit 0
    stamp last_start
    event sb-watchdog "sing-box не запущен — поднимаю"
    /usr/bin/sb-restart.sh "sing-box не был запущен"
    exit 0
fi

# Только что запущен — даём подняться и протестировать ноды.
[ "$(sb_uptime)" -ge 90 ] || exit 0

# 1) Маршрутизация sing-box
if ! routing_ok; then
    [ "$(bump routing_bad)" -ge 2 ] && restart_safely "Нет tun0 или ip rules sing-box"
    exit 0
fi
rm -f "$STATE/routing_bad"

# 2) Работает ли VPN на самом деле
if tunnel_ok; then
    [ "$(cat "$STATE/proxy_fail" 2>/dev/null || echo 0)" -ge 2 ] && event sb-watchdog "VPN снова работает"
    rm -f "$STATE/proxy_fail"
elif direct_ok; then
    n=$(bump proxy_fail)
    if [ "$n" -eq 2 ]; then
        now=$(sb_api /proxies/proxy | jsonfilter -e '@.now' 2>/dev/null)
        if [ -n "$now" ] && [ "$now" != "auto" ]; then
            sb_select proxy auto
            event sb-watchdog "VPN не работает, а proxy был выбран вручную ($now) — вернул auto"
        else
            event sb-watchdog "VPN не работает 2 мин — перетест нод"
        fi
    fi
    if [ "$n" -eq 1 ]; then
        kick_groups strict
    elif [ "$n" -eq 2 ] || [ $((n % 5)) -eq 0 ]; then
        kick_groups
    fi
    if [ "$n" -eq 3 ]; then
        refresh "VPN не работает 3 мин (вдруг у провайдера новые адреса нод)"
        tunnel_ok && exit 0
    fi
    if [ "$n" -ge 4 ] && [ "$(since last_restart)" -ge "$RESTART_MIN_INTERVAL" ]; then
        if ! api_ok; then
            restart_safely "VPN не работает $n мин, Clash API не отвечает"
            exit 0
        elif providers_ok; then
            restart_safely "VPN не работает $n мин, хотя ноды живы"
            exit 0
        fi
    fi
fi

# 2б) Нода для Gemini выбрана вручную (vpn gemini N) и умерла → вернуть автовыбор. Раз в 5 мин;
#     нода должна не ответить два раза подряд, чтобы не реагировать на разовый сбой.
if [ $(( $(date +%s) / 60 % 5 )) -eq 0 ]; then
    gsel=$(sb_api /proxies/gemini-select | jsonfilter -e '@.now' 2>/dev/null)
    if [ -n "$gsel" ] && [ "$gsel" != "gemini-auto" ] && direct_ok; then
        genc=$(printf '%s' "$gsel" | jq -sRr @uri)
        if ! node_alive "$genc" && ! node_alive "$genc"; then
            sb_select gemini-select gemini-auto
            event sb-watchdog "Нода для Gemini выбрана вручную ($gsel), но не отвечает — вернул автовыбор"
        fi
    fi
fi

# 3–4) Живость нод по истории проверок
counts=$(sb_api /proxies | jq -r '[.proxies | to_entries[]
    | select((.key | startswith("P1-")) or (.key | startswith("P2-")))
    | {p: .key[0:2], d: ((.value.history | last | .delay) // 0)}] as $n
    | "\($n | map(select(.p == "P1")) | length) \($n | map(select(.p == "P1" and .d > 0)) | length) \($n | map(select(.p == "P2" and .d > 0)) | length)"' 2>/dev/null)
[ -n "$counts" ] || exit 0
set -- $counts
bl_total=$1 bl_alive=$2 vp_alive=$3

if [ "$bl_total" -gt 0 ] && [ "$bl_alive" -eq 0 ] && [ "$vp_alive" -gt 0 ]; then
    n=$(bump bl_dead)
    [ "$n" -ge 3 ] && refresh "Все $bl_total P1-нод мертвы уже $n мин (P2 живых: $vp_alive)"
else
    rm -f "$STATE/bl_dead"
fi

if [ "$bl_alive" -eq 0 ] && [ "$vp_alive" -eq 0 ] && direct_ok; then
    n=$(bump all_dead)
    [ "$n" -ge 5 ] && refresh "Все ноды мертвы уже $n мин, провайдер работает"
else
    rm -f "$STATE/all_dead"
fi
exit 0
