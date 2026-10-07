"""Session storage behind one interface; callers never know which backend they have.

Every store keeps one document per finished round, keyed by ``session_id``. Documents are plain
dicts holding the validated SESSION_END fields (see ``SessionEnd.to_document``) or, for the scam quiz,
the QUIZ_SESSION_END fields (``QuizSessionEnd.to_document``), plus ``user_id``, the player the round
belongs to (null if no one was logged in). Metrics with no samples are stored as null, never zero.
The two games never share a document shape, so callers that read one game's rounds must ask for it with
``game=`` rather than assume every stored round looks like a Reach Garden round.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Protocol

from shared.protocol import QuizSessionEnd, SessionEnd

STORE_KINDS = ("jsonl", "sqlite", "mongo")
_FIELDS = tuple(SessionEnd.__dataclass_fields__)
StoredResult = SessionEnd | QuizSessionEnd
# Scam quiz rounds live in their own SQLite table, so a database made before the quiz existed needs no migration.
_QUIZ_COLUMNS = (
    ("session_id", "TEXT PRIMARY KEY"), ("stream_id", "TEXT"), ("sequence", "INTEGER"), ("timestamp", "INTEGER"),
    ("game", "TEXT"), ("hand", "TEXT"), ("difficulty", "TEXT"), ("startedAt", "INTEGER"), ("endedAt", "INTEGER"),
    ("duration", "REAL"), ("roundId", "TEXT"), ("slot", "INTEGER"), ("bankVersion", "INTEGER"),
    ("totalQuestions", "INTEGER"), ("correct", "INTEGER"), ("wrong", "INTEGER"), ("skipped", "INTEGER"),
    ("bestStreak", "INTEGER"), ("averageResponseTime", "REAL"), ("fastestResponse", "REAL"),
    ("slowestResponse", "REAL"), ("responses", "TEXT NOT NULL"),
)
_QUIZ_NAMES = tuple(name for name, _ in _QUIZ_COLUMNS) + ("user_id",)
_NEWEST_FIRST = lambda document: (document["endedAt"], document["session_id"])  # noqa: E731


class StorageError(RuntimeError):
    """The operation could not be completed; for a save, the sender should retry later."""


class ResultStore(Protocol):
    def save(self, result: StoredResult, user_id: str | None = None) -> bool:
        """Persist the result for ``user_id``. Return True if newly stored, False if the session_id
        already existed (the first owner is kept)."""

    def get(self, session_id: str) -> dict | None:
        """The stored document, or None."""

    def list_sessions(self, *, game: str | None = None, user_id: str | None = None,
                      limit: int = 20) -> list[dict]:
        """Stored documents, most recently ended first, at most ``limit`` (at least 1)."""

    def count(self, *, game: str | None = None, user_id: str | None = None) -> int: ...

    def claim_unassigned(self, user_id: str) -> int:
        """Give every round that has no player to ``user_id``. Returns how many were claimed."""

    def close(self) -> None: ...


def _check_limit(limit: int) -> None:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer.")


class JsonlResultStore:
    """Append-only JSON-lines file; works with no database installed."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._documents: dict[str, dict] = {}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                with self._path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            document = json.loads(line)
                            self._documents[document["session_id"]] = document
                        except (ValueError, KeyError, TypeError):
                            continue  # Skip a damaged line rather than refuse to start.
        except OSError as error:
            raise StorageError(f"Cannot prepare results file: {error.strerror or error}") from error

    def save(self, result: StoredResult, user_id: str | None = None) -> bool:
        if result.session_id in self._documents:
            return False
        document = {**result.to_document(), "user_id": user_id}
        line = json.dumps(document, separators=(",", ":"), allow_nan=False) + "\n"
        try:
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise StorageError(f"Cannot write results file: {error.strerror or error}") from error
        self._documents[result.session_id] = document
        return True

    def get(self, session_id: str) -> dict | None:
        document = self._documents.get(session_id)
        return {"user_id": None, **document} if document is not None else None

    def _matches(self, game: str | None, user_id: str | None) -> list[dict]:
        return [d for d in self._documents.values()
                if (game is None or d.get("game") == game)
                and (user_id is None or d.get("user_id") == user_id)]

    def list_sessions(self, *, game: str | None = None, user_id: str | None = None,
                      limit: int = 20) -> list[dict]:
        _check_limit(limit)
        matches = [d for d in self._matches(game, user_id) if isinstance(d.get("endedAt"), int)]
        return [{"user_id": None, **d} for d in sorted(matches, key=_NEWEST_FIRST, reverse=True)[:limit]]

    def count(self, *, game: str | None = None, user_id: str | None = None) -> int:
        return len(self._matches(game, user_id))

    def claim_unassigned(self, user_id: str) -> int:
        """Rewrite the file through a temporary copy so an interruption cannot lose rounds.
        Lines that are not valid rounds are kept exactly as they are."""
        claimed = 0
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with self._path.open("r", encoding="utf-8") as source, \
                    temporary.open("w", encoding="utf-8", newline="\n") as target:
                for line in source:
                    try:
                        document = json.loads(line)
                        if document["session_id"] in self._documents and document.get("user_id") is None:
                            document["user_id"] = user_id
                            line = json.dumps(document, separators=(",", ":"), allow_nan=False) + "\n"
                            claimed += 1
                    except (ValueError, KeyError, TypeError):
                        pass
                    target.write(line)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            raise StorageError(f"Cannot rewrite results file: {error.strerror or error}") from error
        for document in self._documents.values():
            if document.get("user_id") is None:
                document["user_id"] = user_id
        return claimed

    def close(self) -> None:
        pass


class SqliteResultStore:
    """Single-file SQL database from the standard library; a real database that needs no server."""

    _TYPES = {"str": "TEXT", "int": "INTEGER", "float": "REAL"}

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self._path)
            self._db.row_factory = sqlite3.Row
            columns = []
            for name, field in SessionEnd.__dataclass_fields__.items():
                base = str(field.type).split("|")[0].strip()  # "float | None" -> "float"; nulls stay allowed
                key = " PRIMARY KEY" if name == "session_id" else ""
                columns.append(f"{name} {self._TYPES[base]}{key}")
            columns.append("user_id TEXT")
            self._db.execute(f"CREATE TABLE IF NOT EXISTS sessions ({', '.join(columns)})")
            existing = {row[1] for row in self._db.execute("PRAGMA table_info(sessions)")}
            if "user_id" not in existing:  # Created before Phase 12: earlier rounds stay unassigned.
                self._db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            self._db.execute("CREATE INDEX IF NOT EXISTS sessions_ended ON sessions (endedAt DESC)")
            self._db.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id)")
            quiz = ", ".join(f"{name} {kind}" for name, kind in _QUIZ_COLUMNS) + ", user_id TEXT"
            self._db.execute(f"CREATE TABLE IF NOT EXISTS quiz_sessions ({quiz})")
            self._db.execute("CREATE INDEX IF NOT EXISTS quiz_sessions_ended ON quiz_sessions (endedAt DESC)")
            self._db.execute("CREATE INDEX IF NOT EXISTS quiz_sessions_user ON quiz_sessions (user_id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS quiz_sessions_round ON quiz_sessions (roundId)")
            self._db.commit()
        except (OSError, sqlite3.Error) as error:
            raise StorageError(f"Cannot open the SQLite database: {error}") from error

    def save(self, result: StoredResult, user_id: str | None = None) -> bool:
        document = {**result.to_document(), "user_id": user_id}
        if isinstance(result, QuizSessionEnd):
            table, names = "quiz_sessions", _QUIZ_NAMES
            document["responses"] = json.dumps(document["responses"], separators=(",", ":"), allow_nan=False)
        else:
            table, names = "sessions", (*_FIELDS, "user_id")
        sql = (f"INSERT OR IGNORE INTO {table} ({', '.join(names)}) "
               f"VALUES ({', '.join('?' for _ in names)})")
        try:
            with self._db:
                cursor = self._db.execute(sql, [document[name] for name in names])
        except sqlite3.Error as error:
            raise StorageError(f"SQLite write failed: {error}") from error
        return cursor.rowcount == 1

    @staticmethod
    def _quiz_document(row: sqlite3.Row) -> dict:
        document = dict(row)
        document["responses"] = json.loads(document["responses"])
        return document

    def get(self, session_id: str) -> dict | None:
        try:
            row = self._db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is not None:
                return dict(row)
            row = self._db.execute("SELECT * FROM quiz_sessions WHERE session_id = ?", (session_id,)).fetchone()
        except sqlite3.Error as error:
            raise StorageError(f"SQLite read failed: {error}") from error
        return self._quiz_document(row) if row is not None else None

    @staticmethod
    def _where(game: str | None, user_id: str | None) -> tuple[str, list]:
        clauses, args = [], []
        if game is not None:
            clauses.append("game = ?")
            args.append(game)
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(user_id)
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), args

    def list_sessions(self, *, game: str | None = None, user_id: str | None = None,
                      limit: int = 20) -> list[dict]:
        _check_limit(limit)
        where, args = self._where(game, user_id)
        try:
            garden = [dict(row) for row in self._db.execute(
                f"SELECT * FROM sessions {where} ORDER BY endedAt DESC, session_id DESC LIMIT ?",
                [*args, limit]).fetchall()]
            quiz = [self._quiz_document(row) for row in self._db.execute(
                f"SELECT * FROM quiz_sessions {where} ORDER BY endedAt DESC, session_id DESC LIMIT ?",
                [*args, limit]).fetchall()]
        except sqlite3.Error as error:
            raise StorageError(f"SQLite read failed: {error}") from error
        # Each table gave its newest ``limit``, so the newest ``limit`` of the two together are among them.
        return sorted(garden + quiz, key=_NEWEST_FIRST, reverse=True)[:limit]

    def count(self, *, game: str | None = None, user_id: str | None = None) -> int:
        where, args = self._where(game, user_id)
        try:
            return sum(self._db.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]
                       for table in ("sessions", "quiz_sessions"))
        except sqlite3.Error as error:
            raise StorageError(f"SQLite read failed: {error}") from error

    def claim_unassigned(self, user_id: str) -> int:
        try:
            with self._db:
                return sum(self._db.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
                                            (user_id,)).rowcount for table in ("sessions", "quiz_sessions"))
        except sqlite3.Error as error:
            raise StorageError(f"SQLite write failed: {error}") from error

    def close(self) -> None:
        self._db.close()


class MongoResultStore:
    """MongoDB store; a unique index on session_id makes repeated deliveries harmless."""

    COLLECTION = "sessions"

    def __init__(self, uri: str, database: str, *, client=None) -> None:
        try:
            if client is None:
                from pymongo import MongoClient
                client = MongoClient(uri, serverSelectionTimeoutMS=3000)
                client.admin.command("ping")
            self._client = client
            self._collection = client[database][self.COLLECTION]
            self._collection.create_index("session_id", unique=True)
            self._collection.create_index([("endedAt", -1)])
            self._collection.create_index("user_id")
        except Exception as error:  # pymongo raises several unrelated connection errors
            raise StorageError("Cannot reach MongoDB; check MONGODB_URI and that the server is running.") from error

    def save(self, result: StoredResult, user_id: str | None = None) -> bool:
        from pymongo.errors import DuplicateKeyError, PyMongoError
        try:
            self._collection.insert_one({**result.to_document(), "user_id": user_id})
        except DuplicateKeyError:
            return False
        except PyMongoError as error:
            raise StorageError("MongoDB write failed.") from error
        return True

    def get(self, session_id: str) -> dict | None:
        from pymongo.errors import PyMongoError
        try:
            document = self._collection.find_one({"session_id": session_id}, {"_id": 0})
            return {"user_id": None, **document} if document is not None else None
        except PyMongoError as error:
            raise StorageError("MongoDB read failed.") from error

    @staticmethod
    def _query(game: str | None, user_id: str | None) -> dict:
        query = {}
        if game is not None:
            query["game"] = game
        if user_id is not None:
            query["user_id"] = user_id
        return query

    def list_sessions(self, *, game: str | None = None, user_id: str | None = None,
                      limit: int = 20) -> list[dict]:
        from pymongo.errors import PyMongoError
        _check_limit(limit)
        try:
            cursor = self._collection.find(self._query(game, user_id), {"_id": 0})
            found = cursor.sort([("endedAt", -1), ("session_id", -1)]).limit(limit)
            return [{"user_id": None, **document} for document in found]
        except PyMongoError as error:
            raise StorageError("MongoDB read failed.") from error

    def count(self, *, game: str | None = None, user_id: str | None = None) -> int:
        from pymongo.errors import PyMongoError
        try:
            return self._collection.count_documents(self._query(game, user_id))
        except PyMongoError as error:
            raise StorageError("MongoDB read failed.") from error

    def claim_unassigned(self, user_id: str) -> int:
        from pymongo.errors import PyMongoError
        try:
            return self._collection.update_many({"user_id": None}, {"$set": {"user_id": user_id}}).modified_count
        except PyMongoError as error:
            raise StorageError("MongoDB write failed.") from error

    def close(self) -> None:
        self._client.close()


def open_store(kind: str, *, path: Path | None = None, mongodb_uri: str = "",
               mongodb_database: str = "motionplay") -> ResultStore:
    """Build the store named by ``kind``; ``path`` is the file for jsonl and sqlite."""
    if kind == "jsonl":
        return JsonlResultStore(path or Path("data") / "results.jsonl")
    if kind == "sqlite":
        return SqliteResultStore(path or Path("data") / "motionplay.db")
    if kind == "mongo":
        return MongoResultStore(mongodb_uri, mongodb_database)
    raise StorageError(f"Unknown store {kind!r}; choose one of {', '.join(STORE_KINDS)}.")
