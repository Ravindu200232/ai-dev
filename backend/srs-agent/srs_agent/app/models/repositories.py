"""High-level data access used by routers and agents."""
from __future__ import annotations

import uuid
from typing import Optional

from .. import db
from ..db import get_store
from ..schemas.project import now_iso


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:24]}"


async def create_project(doc: dict) -> dict:
    await get_store().insert_one(db.PROJECTS, doc)
    return doc


async def get_project(project_id: str) -> Optional[dict]:
    return await get_store().find_one(db.PROJECTS, {"id": project_id})


async def update_project(project_id: str, fields: dict) -> bool:
    fields = {**fields, "updated_at": now_iso()}
    return await get_store().update_one(db.PROJECTS, {"id": project_id}, fields)


async def list_projects(limit: int = 100) -> list[dict]:
    return await get_store().find(
        db.PROJECTS, {}, sort=("created_at", -1), limit=limit
    )


async def add_source(doc: dict) -> dict:
    await get_store().insert_one(db.EXTRACTED_SOURCES, doc)
    return doc


async def list_sources(project_id: str) -> list[dict]:
    return await get_store().find(
        db.EXTRACTED_SOURCES, {"project_id": project_id}, sort=("created_at", 1)
    )


async def save_question_session(doc: dict) -> dict:
    existing = await get_store().find_one(
        db.QUESTION_SESSIONS, {"project_id": doc["project_id"]}
    )
    if existing:
        await get_store().update_one(
            db.QUESTION_SESSIONS, {"project_id": doc["project_id"]}, doc
        )
    else:
        await get_store().insert_one(db.QUESTION_SESSIONS, doc)
    return doc


async def get_question_session(project_id: str) -> Optional[dict]:
    return await get_store().find_one(db.QUESTION_SESSIONS, {"project_id": project_id})


async def save_answers(project_id: str, answers: list[dict]) -> None:
    store = get_store()
    existing = await store.find_one(db.ANSWERS, {"project_id": project_id})
    payload = {"project_id": project_id, "answers": answers, "updated_at": now_iso()}
    if existing:
        await store.update_one(db.ANSWERS, {"project_id": project_id}, payload)
    else:
        payload["id"] = new_id("ans_")
        await store.insert_one(db.ANSWERS, payload)


async def get_answers(project_id: str) -> list[dict]:
    doc = await get_store().find_one(db.ANSWERS, {"project_id": project_id})
    return (doc or {}).get("answers", [])


async def save_plan(doc: dict) -> dict:
    await get_store().insert_one(db.PLANS, doc)
    return doc


async def list_plans(project_id: str) -> list[dict]:
    return await get_store().find(
        db.PLANS, {"project_id": project_id}, sort=("version", 1)
    )


async def latest_plan(project_id: str) -> Optional[dict]:
    plans = await get_store().find(
        db.PLANS, {"project_id": project_id}, sort=("version", -1), limit=1
    )
    return plans[0] if plans else None


async def update_plan(plan_id: str, fields: dict) -> bool:
    return await get_store().update_one(db.PLANS, {"id": plan_id}, fields)


async def save_version(doc: dict) -> dict:
    await get_store().insert_one(db.SRS_VERSIONS, doc)
    return doc


async def list_versions(project_id: str) -> list[dict]:
    return await get_store().find(
        db.SRS_VERSIONS, {"project_id": project_id}, sort=("created_at", 1)
    )


async def latest_version(project_id: str) -> Optional[dict]:
    versions = await get_store().find(
        db.SRS_VERSIONS, {"project_id": project_id}, sort=("created_at", -1), limit=1
    )
    return versions[0] if versions else None


async def save_diagrams(project_id: str, diagrams: list[dict]) -> None:
    store = get_store()
    await store.delete_many(db.DIAGRAMS, {"project_id": project_id})
    for d in diagrams:
        await store.insert_one(
            db.DIAGRAMS, {**d, "project_id": project_id, "id": d.get("id") or new_id("dia_")}
        )


async def list_diagrams(project_id: str) -> list[dict]:
    return await get_store().find(db.DIAGRAMS, {"project_id": project_id})


async def save_artifact(doc: dict) -> dict:
    await get_store().insert_one(db.ARTIFACTS, doc)
    return doc


async def add_event(doc: dict) -> dict:
    await get_store().insert_one(db.AGENT_EVENTS, doc)
    return doc


async def list_events(project_id: str, limit: int = 500) -> list[dict]:
    return await get_store().find(
        db.AGENT_EVENTS, {"project_id": project_id}, sort=("created_at", 1), limit=limit
    )


async def add_prompt_trace(doc: dict) -> dict:
    await get_store().insert_one(db.PROMPT_TRACES, doc)
    return doc


async def list_prompt_traces(project_id: str) -> list[dict]:
    return await get_store().find(
        db.PROMPT_TRACES, {"project_id": project_id}, sort=("created_at", 1)
    )


async def add_error(doc: dict) -> dict:
    await get_store().insert_one(db.ERRORS, doc)
    return doc


async def list_errors(project_id: str) -> list[dict]:
    return await get_store().find(
        db.ERRORS, {"project_id": project_id}, sort=("created_at", 1)
    )
