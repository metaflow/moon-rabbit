# Diagnose Production Issues

Fetch recent logs from the production server, identify new errors since the last `docs/log.md` entry, investigate root causes, implement fixes or suppress irrelevant noise, and record findings.

---

## Step 1 — Read the last log entry

Read `docs/log.md` and note the date of the most recent entry. That date is the **cutoff**: only errors that first appeared after it are in scope.

## Step 2 — Rsync production logs locally

Pull the entire runtime directory from the server to a local temp path so all further analysis is done locally with no repeated SSH calls:

```bash
RUNTIME_DIR=$(mktemp -d /tmp/moon-rabbit-logs-XXXX)
rsync -az tative-cmd:/var/moon-rabbit/runtime/ "$RUNTIME_DIR/"
echo "Logs synced to $RUNTIME_DIR"
```

The key files are:
- `merged.errors.log` — all ERROR+ entries
- `merged.info.log` — INFO context (useful for tracing what happened before an error)

## Step 3 — Triage and group errors

Filter to entries **after the cutoff date**, group by fingerprint (exception class + message prefix or call-site), count occurrences per group, sort descending.

```bash
# Example: count lines on or after the cutoff date
grep -E '^YYYY-MM-DD|^20(26|27)-' "$RUNTIME_DIR/merged.errors.log" | wc -l

# Group by error type (adapt the pattern to actual log format)
grep 'ERROR' "$RUNTIME_DIR/merged.errors.log" | \
  sed 's/^[^ ]* [^ ]* //' | sort | uniq -c | sort -rn | head -40
```

Drop groups with fewer than 3 occurrences unless they look like a new regression. Drop anything whose first occurrence is before the cutoff.

## Step 4 — Read the relevant source files

For each surviving error group, use the traceback to locate the call site. Read the relevant source files before proposing any fix. Use `docs/file_reference.md` and `docs/architecture.md` for orientation.

## Step 5 — Classify each error

| Class | Action |
|---|---|
| **Real bug** — wrong behaviour, data loss, crash | Implement fix |
| **Noisy but harmless** — library internals, transient network blips | Demote to WARNING or add targeted suppression |
| **Already fixed** — covered by a prior commit | Note it; no action |
| **Transient / external** — third-party outage, single occurrence | Note it; no action |

## Step 6 — Implement fixes

Apply code changes following existing project conventions (see `docs/architecture.md`, `CLAUDE.md`). For error suppression, prefer targeted `logging.Filter` or conditional log-level changes over broad `logging.disable`.

After changes run:

```bash
bash check.sh
```

Fix any test or lint failures before proceeding.

## Step 7 — Update docs/log.md

Prepend a new dated entry (today's date, `YYYY-MM-DD`) to `docs/log.md` using this template:

```markdown
## YYYY-MM-DD — Error fixes from recent logs

Source: `/var/moon-rabbit/runtime/merged.errors.log` (CUTOFF_DATE to TODAY, N total ERROR entries reviewed)

### A. Short title — STATUS

**Symptoms**
- …

**Root cause & fix**
- …

### Occurrence Summary

| # | Issue | Occurrences | Status |
|---|-------|-------------|--------|
| A | … | N | Fixed / Suppressed / Transient |
```

Keep entries concise — match the style of existing entries in the file.

## Step 8 — Commit

Stage only the files changed for this task. Do not commit unless the user confirms.
