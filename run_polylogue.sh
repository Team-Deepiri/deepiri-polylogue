#!/bin/bash
# Polylogue startup script - run multiple AI coding tools via Redis message hub
#
# Usage:
#   ./run_polylogue.sh start      # Start all enabled tools
#   ./run_polylogue.sh status  # Show tool status
#   ./run_polylogue.sh submit "task description"  # Submit a task
#   ./run_polylogue.sh stop    # Stop all tools
#
# Requirements:
#   - Redis running on localhost:6379
#   - AI tools installed: opencode, claude, gemini (config in polylogue.yaml)

set -e

 cd "$(dirname "$0")"

export PYTHONPATH="${PWD}/src:$PYTHONPATH"

case "${1:-start}" in
    start)
        echo "Starting polylogue..."
        python3 -m polylogue start -c polylogue.yaml
        ;;
    status)
        echo "Getting status..."
        python3 -m polylogue status -c polylogue.yaml
        ;;
    submit)
        shift
        echo "Submitting task..."
        python3 -m polylogue submit "$@"
        ;;
    stop)
        echo "Stopping polylogue..."
        python3 -m polylogue stop -c polylogue.yaml
        ;;
    *)
        echo "Usage: $0 {start|status|submit|stop}"
        exit 1
        ;;
esac