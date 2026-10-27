#!/usr/bin/env bash
# Start the bot. `./run.sh --check` validates .env and activities.toml without connecting.
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" != "--check" ]]; then
    # One copy at a time: a second one would answer every button twice. The lock is on
    # this folder and is held for as long as the bot runs, however it ends.
    exec 9<.
    if ! flock -n 9; then
        echo "error: the bot is already running, as the albion-announcer service" \
            "(systemctl --user status albion-announcer) or in another terminal." >&2
        exit 1
    fi
fi
exec uv run python -m announcer "$@"
