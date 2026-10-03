#!/bin/sh
# Полный бэкап настроек роутера в формате sysupgrade -b
# (восстанавливается через LuCI: Система → Резервная копия → Восстановить).
# Что попадает в архив, кроме стандартных /etc/config — см. /etc/sysupgrade.conf.
# Хранятся только два последних архива.
DIR=/root/backups
KEEP=2

mkdir -p "$DIR"
F="$DIR/router-$(date +%Y%m%d-%H%M).tar.gz"
if ! sysupgrade -b "$F" >/dev/null 2>&1; then
    logger -t router-backup "Ошибка создания бэкапа"
    echo "Ошибка создания бэкапа"
    exit 1
fi
ls -t "$DIR"/router-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
logger -t router-backup "Бэкап $F ($(du -h "$F" | cut -f1))"
echo "$F"
