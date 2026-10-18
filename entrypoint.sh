#!/bin/sh
# rid2all container entrypoint:
# fall back to the bundled example config when /data/config.toml is absent
# (typical cause: mounting the config path before creating the file, which
# makes Docker create a directory there instead).
set -e

if [ -d /data/config.toml ]; then
    echo "WARN: /data/config.toml is a directory (host file was missing when" \
         "the container was created); docker compose down && up after fixing" \
         "the host side." >&2
fi

if [ ! -f /data/config.toml ]; then
    quiet=0
    for arg in "$@"; do
        case "$arg" in -V|-h|--version|--help) quiet=1 ;; esac
    done
    if [ "$quiet" = 0 ]; then
        echo "INFO: no config.toml found; starting with bundled example config" \
             "(copy config.example.toml to config.toml next to docker-compose.yml" \
             "for real settings)." >&2
    fi
    out=""
    for arg in "$@"; do
        if [ "$arg" = "/data/config.toml" ]; then
            out="$out /app/config.example.toml"
        else
            out="$out $arg"
        fi
    done
    eval "set -- $out"
fi

exec /usr/local/bin/rid2all "$@"
