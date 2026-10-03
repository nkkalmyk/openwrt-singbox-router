#!/bin/sh
L=/tmp/health.log
{
printf '%s mem=%sMB ct=%s sta=%s+%s ' "$(date +%H:%M)" \
  "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" \
  "$(cat /proc/sys/net/netfilter/nf_conntrack_count)" \
  "$(iwinfo phy0-ap0 assoclist 2>/dev/null | grep -c dBm)" \
  "$(iwinfo phy1-ap0 assoclist 2>/dev/null | grep -c dBm)"
# sb= — настоящая память sing-box (RssAnon); code= — его загруженный код (RssFile), ядро освобождает его само.
# До 2026-10-03 13:20 в sb= писался VmRSS (сумма обоих) — старые строки не сравнивать с новыми напрямую.
SBP=$(pidof sing-box | cut -d' ' -f1)
printf 'sb=%sMB code=%sMB ' "$(awk '/RssAnon/{print int($2/1024)}' /proc/$SBP/status 2>/dev/null)" \
  "$(awk '/RssFile/{print int($2/1024)}' /proc/$SBP/status 2>/dev/null)"
printf 'proxy=%s ' "$(curl -s -m5 -o /dev/null -w '%{http_code}/%{time_namelookup}' https://www.gstatic.com/generate_204)"
printf 'direct=%s\n' "$(curl -s -m5 -o /dev/null -w '%{http_code}' https://ya.ru)"
} >> $L
tail -n 500 $L > $L.tmp && mv $L.tmp $L
