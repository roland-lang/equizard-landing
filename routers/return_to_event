from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from typing import List, Dict, Any

from equizard_app.core import templates
from tetrathlon_app.services import events_store
from tetrathlon_app.services.tokens_store import create_token

router = APIRouter()


def _find_events_for_email(email: str) -> List[Dict[str, Any]]:
    email = (email or "").strip().lower()

    matches: List[Dict[str, Any]] = []

    events = events_store.list_events() or []

    for e in events:
        if not isinstance(e, dict):
            continue

        contact_email = (e.get("contact_email") or "").strip().lower()

        if contact_email == email:
            matches.append(e)

    return matches


@router.get("/return", response_class=HTMLResponse)
def return_page(request: Request):

    return templates.TemplateResponse(
        "return_to_event.html",
        {
            "request": request,
            "events": [],
            "email": "",
        },
    )


@router.post("/return", response_class=HTMLResponse)
def return_lookup(
    request: Request,
    email: str = Form(""),
):

    email = (email or "").strip()

    events = _find_events_for_email(email)

    results = []

    for e in events:

        event_id = (e.get("event_id") or "").strip()
        event_name = (e.get("event_name") or event_id).strip()

        token = create_token(event_id, days_valid=30)

        link = f"https://triwizard.co.uk/recover?token={token}"

        results.append(
            {
                "event_name": event_name,
                "event_id": event_id,
                "link": link,
            }
        )

    return templates.TemplateResponse(
        "return_to_event.html",
        {
            "request": request,
            "events": results,
            "email": email,
        },
    )
