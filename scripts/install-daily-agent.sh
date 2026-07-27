#!/bin/bash
# Install (or refresh) the daily-scan LaunchAgent so the scan fires at login.
#
#   bash scripts/install-daily-agent.sh          # install / reinstall
#   bash scripts/install-daily-agent.sh --remove # uninstall
#
# The repo path is baked into the installed plist, so re-run this after moving
# the repo.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.ong.property-finder.daily"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
SRC="$REPO/scripts/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$DEST"
    echo "Removed $LABEL"
    exit 0
fi

chmod +x "$REPO/scripts/run-daily.sh"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/output/daily_runs"

sed "s|__REPO__|$REPO|g" "$SRC" > "$DEST"

# bootout first so a re-install picks up an edited plist (load -w silently keeps
# the old definition when the label is already registered).
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$DEST"

echo "Installed $LABEL"
echo "  plist:  $DEST"
echo "  runs:   at login, plus 09:30 and 14:00 retries"
echo "  log:    $REPO/output/daily_runs/launchd.log"
echo
echo "Test it now without waiting for a login:"
echo "  launchctl kickstart -p gui/$UID/$LABEL"
echo "Check it is registered:"
echo "  launchctl print gui/$UID/$LABEL | head -20"
