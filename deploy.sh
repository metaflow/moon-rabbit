#!/usr/bin/env bash
# Deploy moon-rabbit to the production server.
#
#   ./deploy.sh             full deploy: pull --rebase, checks, push, sync files, uv sync, restart
#   ./deploy.sh --dry-run   read-only: show which files would change/be deleted on the server
#
# The server directory is NOT a git checkout (it was copied during the a-cmd migration and the server
# has no GitHub credentials), so the committed files are rsynced from here. Server-only state is never
# touched: .env, runtime/ (logs, images), .venv/.
#
# Overrides: DEPLOY_HOST (ssh alias, default a-cmd), REMOTE_DIR (default /var/moon-rabbit).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

DEPLOY_HOST="${DEPLOY_HOST:-a-cmd}"
REMOTE_DIR="${REMOTE_DIR:-/var/moon-rabbit}"
APP_NAME="moon-rabbit"

DRY_RUN=0
case "${1:-}" in
    "") ;;
    --dry-run) DRY_RUN=1 ;;
    *)
        echo "Usage: $0 [--dry-run]" >&2
        exit 2
        ;;
esac

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

# Files that exist only on the server (or are rebuilt there) and must survive --delete.
RSYNC_PROTECT=(
    --exclude=/.env
    --exclude=/.git/
    --exclude=/runtime/
    --exclude=/.venv/
    --exclude=/.deployed_revision
    --exclude='__pycache__/'
)

ssh_run() { ssh "$DEPLOY_HOST" "$@"; }

check_branch_and_tree() {
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD)"
    if [[ "$branch" != "main" ]]; then
        echo "ERROR: deploys must run from branch 'main' (currently '$branch')."
        exit 1
    fi
    if [[ -n "$(git status --short)" ]]; then
        if ((DRY_RUN)); then
            echo "NOTE: uncommitted changes present; --dry-run previews the working tree (tracked + untracked, minus ignored)."
        else
            echo "ERROR: Uncommitted changes detected. Please commit or stash them first."
            git status --short
            exit 1
        fi
    fi
}

check_server() {
    echo "==> Checking server $DEPLOY_HOST:$REMOTE_DIR..."
    ssh_run "test -d '$REMOTE_DIR' && test -f '$REMOTE_DIR/.env' && command -v uv >/dev/null && command -v pm2 >/dev/null && command -v rsync >/dev/null" || {
        echo "ERROR: $DEPLOY_HOST is unreachable or $REMOTE_DIR/.env, uv, pm2 or rsync is missing."
        exit 1
    }
}

# Copy exactly the project files (tracked + untracked-but-not-ignored; the tree is clean in a real
# deploy so that is just the committed files) into a temp dir, so rsync --delete can mirror it.
stage_files() {
    # rsync exit 24 = a listed file vanished (only possible in a dirty --dry-run)
    git ls-files -z --cached --others --exclude-standard |
        rsync -rlt --from0 --files-from=- ./ "$STAGE_DIR/" || [[ $? -eq 24 ]]
}

sync_files() {
    # --checksum: compare content, not mtimes (a fresh checkout has new mtimes everywhere); mtimes are not synced.
    # --no-perms/owner/group: don't copy local uid/mode onto the server; new files are owned by the ssh user.
    local opts=(-rl --checksum --no-perms --no-owner --no-group -z --delete --itemize-changes
        "${RSYNC_PROTECT[@]}")
    if ((DRY_RUN)); then
        opts+=(--dry-run)
        echo "==> [dry-run] Files that would change on the server (>f = send, *deleting = remove):"
    else
        echo "==> Syncing files to $DEPLOY_HOST:$REMOTE_DIR..."
    fi
    rsync "${opts[@]}" "$STAGE_DIR/" "$DEPLOY_HOST:$REMOTE_DIR/"
}

# Runs on the server: install locked deps, smoke-test imports, then (re)register the PM2 process.
# The ecosystem file is re-read on every deploy (delete + start) because `pm2 restart` keeps the old
# script/args, and `pm2 save` refreshes the dump that is resurrected on reboot.
remote_install_and_restart() {
    local revision="$1"
    ssh "$DEPLOY_HOST" bash -s -- "$REMOTE_DIR" "$APP_NAME" "$revision" <<'REMOTE'
set -euo pipefail
dir="$1"; app="$2"; rev="$3"
cd "$dir"

echo "==> [server] uv sync (locked, no dev deps)..."
uv sync --no-dev --locked

echo "==> [server] Import smoke test (old process keeps running if this fails)..."
.venv/bin/python3 -c "import main, twitchio_patches"

previous="$(cat .deployed_revision 2>/dev/null || echo 'none')"
echo "==> [server] Restarting $app (previous deploy: $previous)"
pm2 delete "$app" >/dev/null 2>&1 || true
pm2 start ecosystem.config.cjs --only "$app" --update-env
pm2 save >/dev/null
echo "$rev $(date -Is)" > .deployed_revision
REMOTE
}

verify_service() {
    echo "==> Verifying service status..."
    ssh "$DEPLOY_HOST" bash -s -- "$REMOTE_DIR" "$APP_NAME" <<'REMOTE'
set -euo pipefail
dir="$1"; app="$2"

status() {
    pm2 jlist | python3 -c '
import json, sys
app = sys.argv[1]
for p in json.load(sys.stdin):
    if p["name"] == app:
        print(p["pm2_env"]["status"], p["pm2_env"]["restart_time"], p["pid"], p["monit"]["memory"] // 1048576)
        break
else:
    print("missing 0 0 0")
' "$app"
}

sleep 15
read -r state restarts pid mem < <(status)
echo "status=$state restarts=$restarts pid=$pid rss=${mem}MB"
if [[ "$state" != "online" || "$restarts" != "0" ]]; then
    echo "ERROR: $app is not stable. Recent output:"
    pm2 logs "$app" --lines 30 --nostream || true
    exit 1
fi

cd "$dir"
# Only count the line if it came after the latest startup ("args Namespace" is logged by main()).
if awk '/ INFO args Namespace\(/{found=0} /twitchio_patches: applied/{found=1} END{exit !found}' \
    runtime/merged.info.log 2>/dev/null; then
    echo "twitchio leak workaround: applied"
else
    echo "WARNING: 'twitchio_patches: applied' not found in runtime/merged.info.log yet"
fi
echo "--- last log lines ---"
tail -n 8 runtime/merged.info.log 2>/dev/null | cut -c1-200 || true
REMOTE
}

main() {
    check_branch_and_tree
    if ((DRY_RUN)); then
        check_server
        stage_files
        sync_files
        echo "==> Dry run complete; nothing was changed on the server."
        return
    fi

    echo "==> Pulling with rebase..."
    git pull --rebase origin main

    echo "==> Running checks..."
    bash check.sh

    echo "==> Pushing to origin..."
    git push origin main

    check_server
    stage_files
    sync_files
    remote_install_and_restart "$(git rev-parse --short HEAD)"
    verify_service
    echo "==> Deployed $(git rev-parse --short HEAD) to $DEPLOY_HOST."
}

main
