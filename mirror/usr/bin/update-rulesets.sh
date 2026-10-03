#!/bin/sh
# Обновление баз маршрутизации (geoip-ru, geosite-category-ru), cron раз в неделю.
# Новая база ставится, только если скачалась целиком, читается sing-box и конфиг с ней валиден.
# При любой ошибке остаётся старая. sing-box подхватывает новые файлы сам, без перезапуска.
DIR=/etc/sing-box/rule-set
CONFIG=/etc/sing-box/config.json
TMP=/tmp/rs-update
MIRRORS="https://raw.githubusercontent.com/SagerNet/%s/rule-set/%s.srs https://cdn.jsdelivr.net/gh/SagerNet/%s@rule-set/%s.srs"

log() { echo "$*"; logger -t update-rulesets "$*"; }

rm -rf "$TMP"; mkdir -p "$TMP"
for item in "sing-geoip geoip-ru" "sing-geosite geosite-category-ru"; do
    set -- $item
    repo=$1 name=$2 new="$TMP/$name.srs" cur="$DIR/$name.srs"
    ok=0
    for m in $MIRRORS; do
        url=$(printf "$m" "$repo" "$name")
        curl -sf -m 60 -o "$new" "$url" && ok=1 && break
    done
    [ "$ok" = 1 ] || { log "$name: не скачалась ни с одного зеркала — оставляю старую"; continue; }

    old_size=$(wc -c < "$cur" 2>/dev/null || echo 0)
    new_size=$(wc -c < "$new")
    if [ "$new_size" -lt $((old_size / 2)) ]; then
        log "$name: подозрительно маленькая ($new_size байт против $old_size) — пропускаю"
        continue
    fi
    if ! sing-box rule-set decompile -o "$TMP/check.json" "$new" >/dev/null 2>&1; then
        log "$name: файл не читается — пропускаю"
        continue
    fi
    cmp -s "$new" "$cur" && continue

    cp "$cur" "$TMP/$name.prev" 2>/dev/null
    cp "$new" "$cur.tmp" && mv "$cur.tmp" "$cur"
    if sing-box check -c "$CONFIG" >/dev/null 2>&1; then
        log "$name: обновлена ($old_size → $new_size байт)"
    else
        cp "$TMP/$name.prev" "$cur"
        log "$name: с новой базой конфиг невалиден — вернул старую"
    fi
done
rm -rf "$TMP"
