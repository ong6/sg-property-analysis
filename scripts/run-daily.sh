#!/bin/bash
# Wrapper the LaunchAgent calls. Exists so the plist stays dumb and everything
# environment-shaped (venv, PATH for `claude`, network wait, logging) lives in a
# file that is easy to read and to run by hand.
#
# Run it directly to test what launchd will do:  bash scripts/run-daily.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

mkdir -p output/daily_runs
LOG="output/daily_runs/launchd.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# At login the network is often not up yet, and a poll that starts before DNS
# resolves just burns the attempt. Wait up to 2 minutes for a TCP 443 handshake
# (not ICMP — pings to Cloudflare-fronted hosts get dropped on plenty of
# networks, and a false "offline" here would silently skip the day).
#
# Fail OPEN: if connectivity can't be confirmed we still run. A poll that fails
# is handled properly downstream — it leaves the day un-closed so the 09:30 /
# 14:00 triggers retry — whereas exiting here would skip the scan on any
# network where the probe itself is unavailable.
for i in $(seq 1 24); do
    if nc -z -G2 -w2 propertyguru.com.sg 443 >/dev/null 2>&1; then break; fi
    [ "$i" -eq 24 ] && log "connectivity unconfirmed after 2min — running anyway"
    sleep 5
done

# The agent step shells out to `claude`, which a launchd job will not find on
# the default PATH. Pick it up from the login shell's PATH, plus the usual spots.
export PATH="$HOME/.local/bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:$PATH"

PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

log "starting daily scan ($PY)"
"$PY" daily.py "$@" >> "$LOG" 2>&1
rc=$?
log "daily scan exited rc=$rc"
exit $rc
