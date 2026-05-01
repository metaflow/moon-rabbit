#!/usr/bin/env bash
set -euo pipefail

# Step 1: ensure working tree is clean
if [[ -n "$(git status --short)" ]]; then
    echo "ERROR: Uncommitted changes detected. Please commit or stash them first."
    git status --short
    exit 1
fi

# Step 2: pull-rebase
echo "==> Pulling with rebase..."
git pull --rebase origin main

# Step 3: run checks
echo "==> Running checks..."
bash check.sh

# Step 4: push
echo "==> Pushing to origin..."
git push origin main

# Step 5: deploy on server
echo "==> Deploying on production server..."
ssh tative-cmd "cd /var/moon-rabbit && git pull && pm2 restart moon-rabbit --update-env"

# Step 6: verify service is up
echo "==> Verifying service status..."
sleep 3
ssh tative-cmd "pm2 show moon-rabbit"
