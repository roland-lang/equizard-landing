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


# -----------------------------------------------------
# Helpers
# -----------------------------------------------------
def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _package_for_wizard(wizard: str) -> str:
    return "tetrathlon" if wizard == "tetwizard" else "triathlon"


def _title_for_wizard(wizard: str) -> str:
    return "TetWizard" if wizard == "tetwizard" else "TriWizard"


# -----------------------------------------------------
# Bridge: create event in TriWizard
# -----------------------------------------------------
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


# -----------------------------------------------------
# Bridge: get return links
# -----------------------------------------------------
def _get_return_links_from_triwizard(contact_email: str) -> list[dict]:
    return_url = _required_env("TRIWIZARD_RETURN_URL")
    shared_secret = _required_env("PORTAL_SHARED_SECRET")

    form_data = urllib.parse.urlencode(
        {
            "contact_email": (contact_email or "").strip(),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        return_url,
        data=form_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Portal-Secret": shared_secret,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    return (payload or {}).get("links") or []


# -----------------------------------------------------
# Routes
# -----------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": [],
            "email": "",
            "message": "",
        },
    )


@app.get("/triwizard", response_class=HTMLResponse)
def triwizard_form(request: Request):
    return templates.TemplateResponse(
        request,
        "wizard_onboarding.html",
        {
            "wizard": "triwizard",
            "wizard_title": _title_for_wizard("triwizard"),
            "package_type": _package_for_wizard("triwizard"),
        },
    )


@app.get("/tetwizard", response_class=HTMLResponse)
def tetwizard_form(request: Request):
    return templates.TemplateResponse(
        request,
        "wizard_onboarding.html",
        {
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
    licence: str = Form("free"),
):
    wizard = (wizard or "").strip().lower()
    licence = (licence or "free").strip().lower()

    if wizard not in {"triwizard", "tetwizard"}:
        return RedirectResponse(url="/", status_code=303)

    if licence not in {"free", "2_week", "1_month"}:
        licence = "free"

    # Free = existing behaviour
    if licence == "free":
        launch_url = _create_event_in_triwizard(
            wizard=wizard,
            event_name=(event_name or "").strip(),
            club_name=(club_name or "").strip(),
            contact_email=(contact_email or "").strip(),
        )
        return RedirectResponse(url=launch_url, status_code=303)

    # Paid options = send to temporary payment placeholder
    query = urllib.parse.urlencode(
        {
            "wizard": wizard,
            "event_name": (event_name or "").strip(),
            "club_name": (club_name or "").strip(),
            "contact_email": (contact_email or "").strip(),
            "licence": licence,
        }
    )
    return RedirectResponse(url=f"/payment?{query}", status_code=303)


@app.get("/payment", response_class=HTMLResponse)
def payment_page(
    request: Request,
    wizard: str = "",
    event_name: str = "",
    club_name: str = "",
    contact_email: str = "",
    licence: str = "",
):
    wizard_title = _title_for_wizard((wizard or "").strip().lower() or "triwizard")

    licence_label = {
        "2_week": "2-week access",
        "1_month": "1-month access",
    }.get((licence or "").strip().lower(), "selected access")

    return HTMLResponse(
        f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <title>Payment – Equizard</title>
          <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
          <style>
            body {{
              margin: 0;
              font-family: 'Montserrat', Arial, sans-serif;
              background: #f6f7fb;
              color: #111827;
              padding: 28px 18px 50px;
            }}
            .wrap {{
              max-width: 760px;
              margin: 0 auto;
            }}
            .card {{
              background: #fff;
              border: 1px solid #e5e7eb;
              border-radius: 16px;
              padding: 22px;
              box-shadow: 0 4px 18px rgba(17,24,39,0.05);
              text-align: center;
            }}
            h1 {{
              margin-top: 0;
            }}
            .muted {{
              color: #6b7280;
              line-height: 1.6;
            }}
            .back {{
              display: inline-block;
              margin-top: 18px;
              color: #374151;
              text-decoration: none;
              font-weight: 600;
            }}
          </style>
        </head>
        <body>
          <div class="wrap">
            <div class="card">
              <h1>Payment placeholder</h1>
              <p class="muted">
                This is where Stripe will go next.
              </p>
              <p class="muted">
                Product: <strong>{wizard_title}</strong><br>
                Licence: <strong>{licence_label}</strong><br>
                Event: <strong>{event_name or "-"}</strong>
              </p>
              <a class="back" href="/">← Back to Equizard</a>
            </div>
          </div>
        </body>
        </html>
        """
    )


@app.get("/return", response_class=HTMLResponse)
def return_form(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": [],
            "email": "",
            "message": "",
        },
    )


@app.post("/return", response_class=HTMLResponse)
def return_lookup(
    request: Request,
    email: str = Form(""),
):
    email = (email or "").strip()
    events: list[dict] = []
    message = ""

    if email:
        try:
            events = _get_return_links_from_triwizard(email)
        except Exception:
            message = "Unable to retrieve events at the moment."

    if email and not events and not message:
        message = "No events were found for that email address."

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": events,
            "email": email,
            "message": message,
        },
    )


@app.get("/eventingwizard")
def eventingwizard_entry():
    return RedirectResponse(url="/", status_code=303)
