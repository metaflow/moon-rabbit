#!/usr/bin/env bash

FAILED=0
SUMMARY=()

run_check() {
    local name=$1
    local cmd=$2
    local repro=$3

    echo "Running $name..."
    if eval "$cmd"; then
        SUMMARY+=("$name - PASS")
    else
        SUMMARY+=("$name - FAIL - run \`$repro\` to reproduce")
        FAILED=1
    fi
    echo ""
}

run_check "ruff format" "uv run ruff format --check ." "uv run ruff format --check ."
run_check "ruff linter" "uv run ruff check ." "uv run ruff check ."
run_check "ty" "uv run ty check ." "uv run ty check ."
run_check "deptry" "uv run deptry ." "uv run deptry ."
run_check "tests" "uv run pytest tests/" "uv run pytest tests/"

echo "Summary:"
for item in "${SUMMARY[@]}"; do
    echo "$item"
done

if [ $FAILED -ne 0 ]; then
    exit 1
fi
