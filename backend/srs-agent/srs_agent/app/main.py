"""AgentForge Studio API — FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import close_store, connect_store
from .llm import get_llm
from .routers import (
    analyze,
    customize,
    downloads,
    events,
    interview,
    plan,
    projects,
    srs,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = await connect_store()
    app.state.store_backend = store.backend
    yield
    await close_store()


app = FastAPI(
    title="AgentForge Studio API",
    version="1.0.0",
    description="Agentic SRS generator — FastAPI + LangGraph + Ollama + MongoDB.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (projects.router, analyze.router, interview.router, plan.router,
          srs.router, customize.router, downloads.router, events.router):
    app.include_router(r)


@app.get("/")
async def root():
    return {"name": "AgentForge Studio API", "status": "ok", "docs": "/docs"}


@app.get("/health")
async def health():
    llm = get_llm()
    return {
        "status": "ok",
        "store": getattr(app.state, "store_backend", "unknown"),
        "ollama": await llm.health(),
        "models": {"primary": settings.ollama_primary_model, "fallback": settings.ollama_fallback_model},
        "offline_fallback": settings.llm_allow_offline_fallback,
    }
