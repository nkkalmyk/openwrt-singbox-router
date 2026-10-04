# Общие функции для скриптов обслуживания sing-box и Tailscale. Подключать: . /usr/lib/sb-common.sh
# Все проверки независимы от того, в каком состоянии sing-box и DNS: иначе зависший sing-box
# выглядел бы как упавший провайдер, и автоматика ничего бы не делала.

SB_LOCK=/tmp/apply-vless.lock
SB_CONFIG=/etc/sing-box/config.json
SB_GOOD=/etc/sing-box/config.json.good
SB_API=http://127.0.0.1:9090
SB_SECRET=$(cat /etc/sing-box/.clash-secret 2>/dev/null)
EVENTS=${EVENTS:-/root/events.log}
EVENTS_KEEP=500

# Взять общую блокировку на fd 9 (сначала exec 9>"$SB_LOCK"), ожидая до $1 секунд.
# В busybox flock нет -w, поэтому цикл.
lock_wait() {
    local i=0
    until flock -n 9; do
        i=$((i + 1))
        [ "$i" -ge "${1:-120}" ] && return 1
        sleep 1
    done
}

# event <тег> <сообщение> — в syslog и в постоянный журнал /root/events.log (переживает перезагрузку).
event() {
    local tag=$1
    shift
    logger -t "$tag" "$*"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $tag: $*" >> "$EVENTS"
    if [ "$(wc -l < "$EVENTS")" -gt $((EVENTS_KEEP + 50)) ]; then
        tail -n "$EVENTS_KEEP" "$EVENTS" > "$EVENTS.tmp" && mv "$EVENTS.tmp" "$EVENTS"
    fi
}

# Интернет у провайдера есть: пинг с привязкой к WAN, мимо sing-box и без DNS.
# Устройство WAN: на AX3000T — wan, на других роутерах бывает eth1, eth0.2, pppoe-wan. Как в update-vless.py.
wan_dev() {
    local d
    d=$(ubus call network.interface.wan status 2>/dev/null | jsonfilter -e '@.l3_device' 2>/dev/null)
    echo "${d:-wan}"
}

direct_ok() {
    local ip dev
    dev=$(wan_dev)
    for ip in 77.88.8.8 77.88.8.1 8.8.8.8; do
        ping -c 1 -W 2 -I "$dev" "$ip" >/dev/null 2>&1 && return 0
    done
    return 1
}

# Трафик реально уходит в VPN: Cloudflare видит выход не из России. По IP — без DNS.
tunnel_ok() {
    local ip loc
    for ip in 1.1.1.1 1.0.0.1; do
        loc=$(curl -s -m 8 "https://$ip/cdn-cgi/trace" 2>/dev/null | sed -n 's/^loc=//p')
        [ -n "$loc" ] && [ "$loc" != "RU" ] && return 0
    done
    return 1
}

# Маршрутизация sing-box на месте: tun0 поднят и есть его ip rules.
routing_ok() {
    ip link show tun0 2>/dev/null | grep -Eq '[<,]UP[,>]' || return 1
    ip rule 2>/dev/null | grep -q 'lookup 2022' || return 1
    return 0
}

sb_api() {
    curl -s -m "${2:-8}" -H "Authorization: Bearer $SB_SECRET" "$SB_API$1"
}

# Выбрать в группе-селекторе: sb_select <группа> <имя> (имена здесь только служебные: auto, gemini-auto).
sb_select() {
    curl -s -m 8 -X PUT -H "Authorization: Bearer $SB_SECRET" -H "Content-Type: application/json" \
        -d "{\"name\":\"$2\"}" "$SB_API/proxies/$1" >/dev/null
}

# Clash API отвечает (sing-box не завис намертво).
api_ok() {
    sb_api /version 5 | grep -q version
}

SB_TEST_URL_ENC=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204

# Настоящая проверка одной ноды (имя уже url-кодировано). Соединения sing-box идут мимо tun0.
# Важно: при неудаче sing-box стирает историю этой ноды, и группа urltest перестаёт её выбирать.
node_alive() {
    [ "$(sb_api "/proxies/$1/delay?url=$SB_TEST_URL_ENC&timeout=5000" 10 \
        | jsonfilter -e '@.delay' 2>/dev/null || echo 0)" -gt 0 ] 2>/dev/null
}

# Хоть одна нода жива. /group/<имя>/delay для этого не годится: в sing-box 1.13 он перепроверяет
# только ноды с устаревшей историей и при свежей возвращает пустой ответ. Поэтому — поштучно:
# по три лучших ноды каждого провайдера.
providers_ok() {
    local name
    for name in $(sb_api /proxies | jq -r '[.proxies | to_entries[]
            | select((.key | startswith("P1-")) or (.key | startswith("P2-")))
            | {k: .key, p: .key[0:2], d: ((.value.history | last | .delay) // 0)}]
            | (map(select(.p == "P1")) | sort_by(if .d > 0 then .d else 1e9 end) | .[0:3])
            + (map(select(.p == "P2")) | sort_by(if .d > 0 then .d else 1e9 end) | .[0:3])
            | .[].k | @uri' 2>/dev/null); do
        node_alive "$name" && return 0
    done
    return 1
}

# Заставить группы urltest уйти с мёртвой ноды сейчас, а не через интервал проверки:
# проверка выбранной ноды отдельно стирает её историю при неудаче, затем проверка группы
# тестирует ноды без свежей истории и выбирает лучшую живую.
kick_groups() {
    local g now
    for g in auto gemini-auto; do
        now=$(sb_api "/proxies/$g" | jsonfilter -e '@.now' 2>/dev/null)
        [ -n "$now" ] || continue
        node_alive "$(printf '%s' "$now" | jq -sRr @uri)" && continue
        sb_api "/group/$g/delay?url=$SB_TEST_URL_ENC&timeout=5000" 30 >/dev/null
    done
}

# Секунд с запуска sing-box (большое число, если не запущен).
sb_uptime() {
    local pid start now hz=100
    pid=$(pidof sing-box | cut -d' ' -f1)
    [ -n "$pid" ] || { echo 999999; return; }
    start=$(awk '{print $22}' "/proc/$pid/stat")
    now=$(awk '{print int($1 * 100)}' /proc/uptime)
    echo $(( (now - start) / hz ))
}

# Рабочий конфиг: текущий валиден — оставить; нет — вернуть последний рабочий.
ensure_valid_config() {
    sing-box check -c "$SB_CONFIG" >/dev/null 2>&1 && return 0
    if [ -f "$SB_GOOD" ] && sing-box check -c "$SB_GOOD" >/dev/null 2>&1; then
        cp "$SB_CONFIG" "$SB_CONFIG.broken" 2>/dev/null
        cp "$SB_GOOD" "$SB_CONFIG.tmp" && mv "$SB_CONFIG.tmp" "$SB_CONFIG"
        event sb-common "Конфиг невалиден — вернул последний рабочий (испорченный: $SB_CONFIG.broken)"
        return 0
    fi
    event sb-common "Конфиг невалиден, рабочей копии нет — sing-box не запустится"
    return 1
}

# Запомнить текущий конфиг как рабочий (вызывать только после проверки связи).
save_good_config() {
    cp "$SB_CONFIG" "$SB_GOOD.tmp" && mv "$SB_GOOD.tmp" "$SB_GOOD"
}

# Безопасный перезапуск sing-box. Вызывать, держа блокировку $SB_LOCK.
# stop → пауза → start: быстрый `init.d restart` однажды оставил LAN без интернета.
sb_restart_locked() {
    local i
    /etc/init.d/sing-box stop
    sleep 3
    ensure_valid_config
    /etc/init.d/sing-box start
    for i in 1 2 3 4 5 6 7 8 9 10; do
        routing_ok && break
        sleep 2
    done
    # Flowtable держит устройства по ifindex: новый tun0 туда возвращает перезагрузка файрвола
    # по событию vlesstun up. Если за 10 с не вернулся — делаем её сами.
    for i in 1 2 3 4 5; do
        nft list flowtable inet fw4 ft 2>/dev/null | grep -q '"tun0"' && break
        sleep 2
    done
    if ! nft list flowtable inet fw4 ft 2>/dev/null | grep -q '"tun0"'; then
        fw4 reload >/dev/null 2>&1
        event sb-common "tun0 не вернулся во flowtable — сделал fw4 reload"
    fi
    routing_ok
}
