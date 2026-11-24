#!/bin/sh
set -e

# Контейнер стартует от root только чтобы выставить права на каталог с базой:
# bind-mount часто принадлежит root, а приложение работает от пользователя bot.
if [ "$(id -u)" = "0" ]; then
    db_path="${DB_PATH:-/data/birthdays.db}"
    db_dir="$(dirname "$db_path")"
    if [ -z "$db_dir" ] || [ "$db_dir" = "/" ]; then
        db_dir="/data"
    fi
    mkdir -p "$db_dir"
    chown -R bot:bot "$db_dir" 2>/dev/null || true
    exec su-exec bot "$@"
fi

exec "$@"
