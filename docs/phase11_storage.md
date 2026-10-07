# Phase 11: Database abstraction and MongoDB

## What changed

Phase 10 could only write results. Phase 11 turns storage into one interface that
can also read them back, with three interchangeable stores. The receiver, the
session viewer, and the later phases (users, dashboard, reports) talk only to the
interface.

```text
backend.result_receiver ──► ResultStore (backend/storage.py) ◄── backend.sessions (list, import)
                              ├─ JsonlResultStore    data/results.jsonl     (default, no database)
                              ├─ SqliteResultStore   data/motionplay.db     (real SQL database, no server)
                              └─ MongoResultStore    MONGODB_URI            (needs a MongoDB server)
```

## The interface

| Method | Behavior |
|---|---|
| `save(result)` | Store a validated `SESSION_END`. Returns `True` if new, `False` if its `session_id` already exists. Raises `StorageError` if it cannot be stored. |
| `get(session_id)` | The stored document, or `None`. |
| `list_sessions(game=None, limit=20)` | Documents newest first (by `endedAt`), at most `limit`; `limit` must be a positive integer. |
| `count(game=None)` | How many sessions are stored. |
| `close()` | Release files or connections. |

A document is the validated result fields (`session_id`, `game`, `hand`, `startedAt`,
`endedAt`, `duration`, the counts, and the five metrics). Metrics with no samples are
stored as `null`, never zero, in every store. Returned documents are copies. Every
failure of the underlying database is reported as `StorageError`, so callers handle
one error type.

All three stores run the same test contract (`tests/test_storage.py`), so a new store
only has to pass it.

## Choosing a store

| Store | Pick it when |
|---|---|
| `jsonl` | Simplest; easy to read and diff. Loads everything into memory, so it suits small histories. |
| `sqlite` | A real database with an index on `endedAt`, no server to install. Good default for a single player machine. Reach Garden rounds are in the `sessions` table and scam quiz rounds in `quiz_sessions` (created on first use, so an older database needs no migration); the store reads and writes both. |
| `mongo` | You already run MongoDB. Uses `MONGODB_URI` and `MONGODB_DATABASE` from `.env`; collection `sessions` with a unique index on `session_id` and an index on `endedAt`. |

## Commands (Windows 11, PowerShell)

Receive results into a chosen store:

```powershell
.\.venv\Scripts\python.exe -m backend.result_receiver --store sqlite
```

`--file` overrides the path for `jsonl` and `sqlite` (relative paths are under the project root).

List the newest rounds (missing metrics print as `-`):

```powershell
.\.venv\Scripts\python.exe -m backend.sessions list --store sqlite --limit 10
```

Copy an existing JSONL file into another store. Repeating it is safe; rounds already
there are counted as "already present" and lines that fail validation are skipped and counted:

```powershell
.\.venv\Scripts\python.exe -m backend.sessions import data/results.jsonl --store sqlite
```

## MongoDB

MongoDB is not installed on the development machine. The MongoDB store is tested
against an in-memory stand-in for pymongo's collection, which checks the logic
(duplicates, ordering, filters, errors) but not a real server. To try it for real,
install and start MongoDB locally, confirm `MONGODB_URI` in `.env`, then:

```powershell
.\.venv\Scripts\python.exe -m backend.result_receiver --store mongo
.\.venv\Scripts\python.exe -m backend.sessions import data/results.jsonl --store mongo
```

If the server is unreachable, the command stops within about three seconds with a
message naming `MONGODB_URI`; the credentials are never printed.

## Acceptance checklist

- [ ] `--store sqlite` receiver saves a Reach Garden round and `backend.sessions list --store sqlite` shows it.
- [ ] Importing the same JSONL file twice reports everything as already present the second time.
- [ ] Restarting the receiver does not duplicate or lose rounds in `jsonl` or `sqlite`.
- [ ] A round where nothing was watered lists with `-` for reaction, stability, and efficiency, not `0`.
- [ ] (Optional, needs MongoDB) the same checks pass with `--store mongo`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

23 new Python cases: the shared store contract run on all three stores (idempotent
save, round trip, nulls, ordering, limit and filter, bad limits, copies, reopening,
database failures), the factory, and the `sessions` command including import with
duplicates and invalid lines. Unity is unchanged in this phase.

Rounds are tied to players from [Phase 12](phase12_accounts.md) on.
This is a gameplay prototype for a portfolio project, not a medical device or therapy tool.
