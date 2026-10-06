"""Ledger HTTP service. Runs on the internal network only (see docker-compose.override.yml)."""
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from .chain import EVENT_TYPES
from .store import Ledger, LedgerError


class Actor(BaseModel):
    type: str
    id: str


class EventIn(BaseModel):
    actor: Actor
    event: str
    payload: Dict[str, Any] = {}


def create_app(db_path: Optional[str] = None, token: Optional[str] = None) -> FastAPI:
    ledger = Ledger(db_path or os.environ.get("LEDGER_DB", "/data/ledger.db"))
    required_token = token if token is not None else os.environ.get("LEDGER_TOKEN")
    app = FastAPI(title="Workbench ledger", version="0.1.0")
    app.state.ledger = ledger

    def check_token(supplied: Optional[str]) -> None:
        if required_token and supplied != required_token:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "bad or missing X-Ledger-Token"})

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"status": "ok", "head": ledger.head()}

    @app.post("/v1/ledger/events", status_code=201)
    def append(body: EventIn, x_ledger_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        check_token(x_ledger_token)
        try:
            return ledger.append(body.actor.type, body.actor.id, body.event, body.payload)
        except LedgerError as e:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_FAILED", "message": str(e)})

    @app.get("/v1/ledger/events")
    def list_events(after_seq: int = 0, limit: int = 100, event: Optional[str] = None,
                    actor_id: Optional[str] = None) -> Dict[str, Any]:
        if event and event not in EVENT_TYPES:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_FAILED", "message": "unknown event type"})
        return {"events": ledger.events(after_seq, limit, event, actor_id), "head": ledger.head()}

    @app.get("/v1/ledger/head")
    def head() -> Dict[str, Any]:
        """The 'anchor': save this somewhere else (paper, USB) to detect deletion of recent entries."""
        return ledger.head()

    @app.get("/v1/ledger/verify")
    def verify(expect_seq: Optional[int] = Query(default=None),
               expect_hash: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        return ledger.verify(expect_seq, expect_hash)

    return app


app = None  # created lazily so importing this module never touches the disk


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
