#!/bin/sh
set -eu
umask 0077
if [ "$(id -u)" = 0 ]; then
    echo 'Bot API must run with a non-root TGD_UID.' >&2
    exit 1
fi
# No credentials are needed until the application explicitly starts local API.
exec python3 /usr/local/lib/tgd-api-supervisor.py
