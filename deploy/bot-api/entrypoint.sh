#!/bin/sh
set -eu
umask 0077
# No credentials are needed until the application explicitly starts local API.
exec python3 /usr/local/lib/tgd-api-supervisor.py
