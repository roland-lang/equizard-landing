from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Equizard")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _package_for_wizard(wizard: str) -> str:
    return "tetrathlon" if wizard == "tetwizard" else "triathlon"


def _title_for_wizard(wizard: str) -> str:
    return "TetWizard" if wizard == "tetwizard" else "TriWizard"


def _create_event_in_triwizard(
    wizard: str,
    event_name: str,
    club_name: str,
    contact_email: str,
) -> str:
    bridge_url = _required_env("TRIWIZARD_BRIDGE_URL")
    shared_secret = _required_env("PORTAL_SHARED_SECRET")

    access_type = wizard
    package_type = _package_for_wizard(wizard)

    form_data = urllib.parse.urlencode(
        {
            "event_name": event_name,
            "club_name": club_name,
            "contact_email": contact_email,
            "duration_days": "30",
            "package_type": package_type,
            "access_type": access_type,
            "source": "equizard",
            "payment_status": "testing",
            "external_ref": "",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        bridge_url,
        data=form_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Portal-Secret": shared_secret,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    launch_url = (payload or {}).get("launch_url") or ""
    if not launch_url:
        raise RuntimeError("TriWizard bridge did not return a launch URL")

    return launch_url


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/triwizard", response_class=HTMLResponse)
def triwizard_form(request: Request):
    return templates.TemplateResponse(
        "wizard_onboarding.html",
        {
            "request": request,
            "wizard": "triwizard",
            "wizard_title": _title_for_wizard("triwizard"),
            "package_type": _package_for_wizard("triwizard"),
        },
    )


@app.get("/tetwizard", response_class=HTMLResponse)
def tetwizard_form(request: Request):
    return templates.TemplateResponse(
        "wizard_onboarding.html",
        {
            "request": request,
            "wizard": "tetwizard",
            "wizard_title": _title_for_wizard("tetwizard"),
            "package_type": _package_for_wizard("tetwizard"),
        },
    )


@app.post("/start-wizard")
def start_wizard(
    request: Request,
    wizard: str = Form(""),
    event_name: str = Form(""),
    club_name: str = Form(""),
    contact_email: str = Form(""),
):
    wizard = (wizard or "").strip().lower()
    if wizard not in {"triwizard", "tetwizard"}:
        return RedirectResponse(url="/", status_code=303)

    launch_url = _create_event_in_triwizard(
        wizard=wizard,
        event_name=(event_name or "").strip(),
        club_name=(club_name or "").strip(),
        contact_email=(contact_email or "").strip(),
    )

    return RedirectResponse(url=launch_url, status_code=303)


@app.get("/eventingwizard")
def eventingwizard_entry():
    return RedirectResponse(url="/", status_code=303)
