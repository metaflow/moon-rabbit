#!/usr/bin/env bash
set -e

echo "Running ruff linter..."
uv run ruff check .

echo "Running ty typechecker..."
uv run ty check .

echo "Running deptry dependency verification..."
uv run deptry .

echo "Running tests..."
uv run pytest tests/

echo "All checks passed!"
