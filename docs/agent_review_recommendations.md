# Improvement Proposals

## Nits
<!-- low priority, optional -->
- `data.py` — The dataclasses have some default values defined ambiguously. `CommandData.actions` is a mutable field using `field(default_factory=list)`, which is great, but could use better type bounds now that `Action` is modernized.
- `storage.py` / `query.py` — Stale comments like `# TODO: also use newline and regex` in `commands.py` should be cleaned up.
- `query.py` — The grammar parser `good_tag_name` includes a bare negation that takes a moment to read; can be slightly refactored for clarity.

## Proposals
<!-- meaningful improvements, require planning -->

- `commands.py` **(Module Size & SRP Violation)**
  Currently almost 1000 lines long, `commands.py` contains the router pipeline (`process_message`) AND every built-in command class (`HelpCommand`, `SetCommand`, etc.).
  *Fix:* Split into a package: `commands/pipeline.py` (registry/routing) and `commands/builtins.py` (the actual implementations). `PersistentCommand` could go in `commands/persistent.py`.

- `storage.py` and `commands.py` **(Synchronous DB in Async Handlers)**
  The project extensively uses `psycopg2` (which is synchronous) and calls `db().get_text()`, `db().set_text()`, etc. inside `async def process_message()` and the Discord/Twitch event handlers. This blocks the asyncio event loop on database I/O, destroying concurrency.
  *Fix:* Migrate `psycopg2` to `asyncpg` (or `psycopg` v3's async implementation). Alternatively, wrap all DB calls in `asyncio.to_thread()`.

- `storage.py` **(Dependency Injection / Singleton Pattern)**
  The `set_db(DB(db_connection))` and global `db()` singleton makes testing commands very difficult without a live database.
  *Fix:* Pass an interface of the `DB` or a context object containing the `DB` instance into the command `run(msg)` methods explicitly.

- `commands.py` **(Broad Error Handling)**
  In `process_message()`, the entire command execution is wrapped in a `try...except Exception as e:` which logs broadly. This can swallow `KeyError` or `ValueError` that represent real bugs, making them hard to trace and silently failing user commands.
  *Fix:* Catch only specific execution exceptions, or re-raise `Exception` in Dev mode so tests and developers can see the failure directly stack-traced in standard output rather than just log files.
