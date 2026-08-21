"""Persistence layer."""
from __future__ import annotations

import copy

from .config import settings


PROJECTS = "projects"
PROJECT_INPUTS = "project_inputs"
EXTRACTED_SOURCES = "extracted_sources"
QUESTION_SESSIONS = "question_sessions"
QUESTIONS = "questions"
ANSWERS = "answers"
PLANS = "plans"
SRS_VERSIONS = "srs_versions"
DIAGRAMS = "diagrams"
ARTIFACTS = "artifacts"
AGENT_EVENTS = "agent_events"
PROMPT_TRACES = "prompt_traces"
ERRORS = "errors"

ALL_COLLECTIONS = [
    PROJECTS, PROJECT_INPUTS, EXTRACTED_SOURCES, QUESTION_SESSIONS, QUESTIONS,
    ANSWERS, PLANS, SRS_VERSIONS, DIAGRAMS, ARTIFACTS, AGENT_EVENTS,
    PROMPT_TRACES, ERRORS,
]


def _matches(doc: dict, query: dict) -> bool:
    """Minimal Mongo-style matcher: equality + `$in`."""
    for key, cond in query.items():
        value = doc.get(key)
        if isinstance(cond, dict) and "$in" in cond:
            if value not in cond["$in"]:
                return False
        elif value != cond:
            return False
    return True


class MemoryStore:
    backend = "memory"

    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {c: [] for c in ALL_COLLECTIONS}

    async def insert_one(self, collection: str, doc: dict) -> dict:
        self._data.setdefault(collection, []).append(copy.deepcopy(doc))
        return doc

    async def find_one(self, collection: str, query: dict) -> dict | None:
        for doc in self._data.get(collection, []):
            if _matches(doc, query):
                return copy.deepcopy(doc)
        return None

    async def find(
        self,
        collection: str,
        query: dict | None = None,
        sort: tuple[str, int] | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        query = query or {}
        rows = [copy.deepcopy(d) for d in self._data.get(collection, []) if _matches(d, query)]
        if sort:
            field, direction = sort
            rows.sort(key=lambda d: d.get(field) or "", reverse=direction < 0)
        if limit:
            rows = rows[:limit]
        return rows

    async def update_one(self, collection: str, query: dict, set_fields: dict) -> bool:
        for doc in self._data.get(collection, []):
            if _matches(doc, query):
                doc.update(copy.deepcopy(set_fields))
                return True
        return False

    async def count(self, collection: str, query: dict | None = None) -> int:
        query = query or {}
        return sum(1 for d in self._data.get(collection, []) if _matches(d, query))

    async def delete_many(self, collection: str, query: dict) -> int:
        rows = self._data.get(collection, [])
        keep = [d for d in rows if not _matches(d, query)]
        removed = len(rows) - len(keep)
        self._data[collection] = keep
        return removed

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:  # pragma: no cover - nothing to close
        pass


class MotorStore:
    backend = "mongodb"

    def __init__(self, client, db) -> None:
        self._client = client
        self._db = db

    async def insert_one(self, collection: str, doc: dict) -> dict:

        await self._db[collection].insert_one(_strip_oid(doc))
        return _strip_oid(doc)

    async def find_one(self, collection: str, query: dict) -> dict | None:
        return _strip_oid(await self._db[collection].find_one(query))

    async def find(
        self,
        collection: str,
        query: dict | None = None,
        sort: tuple[str, int] | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        cursor = self._db[collection].find(query or {})
        if sort:
            cursor = cursor.sort(*sort)
        if limit:
            cursor = cursor.limit(limit)
        return [_strip_oid(d) for d in await cursor.to_list(length=limit or 1000)]

    async def update_one(self, collection: str, query: dict, set_fields: dict) -> bool:
        res = await self._db[collection].update_one(query, {"$set": set_fields})
        return res.modified_count > 0

    async def count(self, collection: str, query: dict | None = None) -> int:
        return await self._db[collection].count_documents(query or {})

    async def delete_many(self, collection: str, query: dict) -> int:
        res = await self._db[collection].delete_many(query)
        return res.deleted_count

    async def ping(self) -> bool:
        await self._client.admin.command("ping")
        return True

    async def close(self) -> None:
        self._client.close()


def _strip_oid(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


_store: MotorStore | MemoryStore | None = None


async def connect_store() -> MotorStore | MemoryStore:
    """Connect to Mongo, or fall back to the in-memory store."""
    global _store
    if _store is not None:
        return _store
    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=1500)
        await client.admin.command("ping")
        _store = MotorStore(client, client[settings.mongodb_db])
        await _ensure_indexes(_store)
        print(f"[db] connected to MongoDB at {settings.mongodb_uri}")
    except Exception as exc:  # noqa: BLE001 - any failure -> fallback
        if not settings.mongodb_allow_memory_fallback:
            raise
        print(f"[db] MongoDB unavailable ({exc!s}); using in-memory store")
        _store = MemoryStore()
    return _store


async def _ensure_indexes(store: MotorStore) -> None:
    try:
        await store._db[PROJECTS].create_index("id", unique=True)
        for col in (EXTRACTED_SOURCES, SRS_VERSIONS, AGENT_EVENTS, QUESTION_SESSIONS,
                    ANSWERS, DIAGRAMS, PROMPT_TRACES, ERRORS):
            await store._db[col].create_index("project_id")
    except Exception:  # noqa: BLE001 - index creation is best-effort
        pass


async def ensure_store() -> MotorStore | MemoryStore:
    """Re-check the store, and fix it if it has gone wrong."""
    global _store
    if _store is None:
        return await connect_store()

    if isinstance(_store, MotorStore):
        try:
            await _store.ping()
            return _store
        except Exception:                                   # noqa: BLE001
            print("[db] MongoDB stopped answering; reconnecting")
            try:
                await _store.close()
            except Exception:                               # noqa: BLE001
                pass
            _store = None
            return await connect_store()

    if any(_store._data.get(c) for c in ALL_COLLECTIONS):
        return _store

    was = _store
    _store = None
    upgraded = await connect_store()
    if isinstance(upgraded, MemoryStore):
        _store = was
    return _store


def get_store() -> MotorStore | MemoryStore:
    if _store is None:
        raise RuntimeError("Store not initialised; call connect_store() first")
    return _store


async def close_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None


__all__ = [
    "connect_store", "get_store", "close_store",
    *[c.upper() for c in ()],
]
