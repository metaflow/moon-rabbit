# Improvement Proposals

## Nits
<!-- low priority, optional -->

## Proposals
<!-- meaningful improvements, require planning -->

- `storage.py` **(Dependency Injection / Singleton Pattern)**
  The `set_db(DB(db_connection))` and global `db()` singleton makes testing commands very difficult without a live database.
  *Fix:* Pass an interface of the `DB` or a context object containing the `DB` instance into the command `run(msg)` methods explicitly.
