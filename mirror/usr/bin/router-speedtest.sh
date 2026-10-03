#!/bin/sh
# Замер скорости с самого роутера: через VPN и напрямую, приём и отдача, плюс загрузка CPU во время замера.
#   scp -O scripts/router-speedtest.sh openwrt-ts:/tmp/ && ssh openwrt-ts 'sh /tmp/router-speedtest.sh; rm /tmp/router-speedtest.sh'
# Не запускать около :07 (cron apply-vless). Каждый замер ограничен по времени, качает в /dev/null.
T=15
WAN=$(ubus call network.interface.wan status | jsonfilter -e '@.l3_device')

cpu() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat; }
run() {  # run <название> <curl-аргументы...>
    name=$1; shift
    set -- $(cpu) "$@"; t0=$1 i0=$2; shift 2
    out=$(curl -s -o /dev/null -m $T -w '%{size_download} %{size_upload} %{speed_download} %{speed_upload} %{time_connect} %{http_code}' "$@")
    set -- $(cpu) $out; t1=$1 i1=$2
    awk -v n="$name" -v sd="$5" -v su="$6" -v bd="$3" -v bu="$4" -v tc="$7" -v code="$8" \
        -v busy="$(( 100 - 100 * (i1 - i0) / (t1 - t0 + 1) ))" 'BEGIN {
        s = (sd > su ? sd : su); b = (bd > bu ? bd : bu)
        printf "%-34s %6.1f Мбит/с  %5.1f MB  connect %4d мс  CPU %3d%%  http %s\n", n, s*8/1e6, b/1e6, tc*1000, busy, code }'
}

echo "Нода VPN: $(sb-ping route speed.cloudflare.com 2>/dev/null | awk '{print $2, $3}')"
# Cloudflare отдаёт не больше ~25 MB за раз (на 100 MB отвечает 403) — три замера подряд.
for i in 1 2 3; do
    run "VPN, приём (Cloudflare) #$i" -A "Mozilla/5.0" "https://speed.cloudflare.com/__down?bytes=25000000"
done
run "напрямую, приём (Selectel)"     "https://speedtest.selectel.ru/100MB"
run "напрямую, приём (mirror.yandex)" "https://mirror.yandex.ru/archlinux/iso/latest/archlinux-x86_64.iso"
# Отдача потоком из /dev/zero: /tmp на роутере живёт в RAM.
head -c 60000000 /dev/zero | run "VPN, отдача (Cloudflare)" -T - "https://speed.cloudflare.com/__up"
head -c 60000000 /dev/zero | run "напрямую, отдача (Cloudflare)" --interface "$WAN" -T - "https://speed.cloudflare.com/__up"
