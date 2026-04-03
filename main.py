from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

import stripe
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


def _normalise_licence(val: str | None) -> str:
    v = (val or "").strip().lower()
    return v if v in {"free", "2_week", "1_month"} else "free"


def _normalise_mode(val: str | None) -> str:
    v = (val or "").strip().lower()
    return v if v in {"activate", "extend"} else "activate"


def _safe_duration_days(val: str | int | None) -> int:
    try:
        n = int(val or 0)
    except Exception:
        n = 0
    return n if n in {7, 14, 30} else 7


def _duration_label(duration_days: int) -> str:
    return {
        7: "1-week extension",
        14: "2-week extension",
        30: "1-month extension",
    }.get(duration_days, "Extension")


def _parse_competition_date(val: str | None) -> date | None:
    s = (val or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _two_week_warning(licence: str, competition_date: str) -> str:
    if _normalise_licence(licence) != "2_week":
        return ""

    comp = _parse_competition_date(competition_date)
    if not comp:
        return ""

    today = datetime.utcnow().date()
    if comp > (today + timedelta(days=14)):
        return (
            "This 2-week licence is likely to expire before your event. "
            "Consider choosing the 1-month option, or activating within 2 weeks of your competition."
        )

    return ""


def _triwizard_public_base_url() -> str:
    return _required_env("TRIWIZARD_PUBLIC_BASE_URL").rstrip("/")


# -----------------------------------------------------
# Stripe init
# -----------------------------------------------------
stripe.api_key = _required_env("STRIPE_SECRET_KEY")


# -----------------------------------------------------
# Bridge: create event in TriWizard
# -----------------------------------------------------
def _create_event_in_triwizard(
    wizard: str,
    event_name: str,
    club_name: str,
    contact_email: str,
    competition_date: str,
    licence: str = "free",
    payment_status: str = "none",
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
            "competition_date": competition_date,
            "duration_days": "30",
            "package_type": package_type,
            "access_type": access_type,
            "source": "equizard",
            "payment_status": payment_status,
            "licence_duration": licence,
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

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bridge HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Bridge URL error: {e.reason}") from e

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
def home(request: Request, message: str = ""):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": [],
            "email": "",
            "message": message or "",
        },
    )


@app.get("/triwizard", response_class=HTMLResponse)
def triwizard_form(
    request: Request,
    event_name: str = "",
    club_name: str = "",
    contact_email: str = "",
    competition_date: str = "",
    licence: str = "",
):
    licence = _normalise_licence(licence)
    warning = _two_week_warning(licence, competition_date)

    return templates.TemplateResponse(
        request,
        "wizard_onboarding.html",
        {
            "wizard": "triwizard",
            "wizard_title": _title_for_wizard("triwizard"),
            "package_type": _package_for_wizard("triwizard"),
            "event_name": event_name,
            "club_name": club_name,
            "contact_email": contact_email,
            "competition_date": competition_date,
            "licence": licence,
            "warning": warning,
        },
    )


@app.get("/tetwizard", response_class=HTMLResponse)
def tetwizard_form(
    request: Request,
    event_name: str = "",
    club_name: str = "",
    contact_email: str = "",
    competition_date: str = "",
    licence: str = "",
):
    licence = _normalise_licence(licence)
    warning = _two_week_warning(licence, competition_date)

    return templates.TemplateResponse(
        request,
        "wizard_onboarding.html",
        {
            "wizard": "tetwizard",
            "wizard_title": _title_for_wizard("tetwizard"),
            "package_type": _package_for_wizard("tetwizard"),
            "event_name": event_name,
            "club_name": club_name,
            "contact_email": contact_email,
            "competition_date": competition_date,
            "licence": licence,
            "warning": warning,
        },
    )


@app.post("/start-wizard")
def start_wizard(
    request: Request,
    wizard: str = Form(""),
    event_name: str = Form(""),
    club_name: str = Form(""),
    contact_email: str = Form(""),
    competition_date: str = Form(""),
    licence: str = Form("free"),
):
    wizard = (wizard or "").strip().lower()
    licence = _normalise_licence(licence)
    competition_date = (competition_date or "").strip()

    if wizard not in {"triwizard", "tetwizard"}:
        return RedirectResponse("/", status_code=303)

    if not _parse_competition_date(competition_date):
        return RedirectResponse("/?message=Please+enter+a+valid+event+date", status_code=303)

    if licence == "free":
        try:
            launch_url = _create_event_in_triwizard(
                wizard=wizard,
                event_name=event_name.strip(),
                club_name=club_name.strip(),
                contact_email=contact_email.strip(),
                competition_date=competition_date,
                licence="free",
                payment_status="none",
            )
            return RedirectResponse(launch_url, status_code=303)
        except Exception as e:
            return RedirectResponse(
                f"/?message={urllib.parse.quote_plus(str(e))}",
                status_code=303,
            )

    query = urllib.parse.urlencode(
        {
            "wizard": wizard,
            "event_name": event_name,
            "club_name": club_name,
            "contact_email": contact_email,
            "competition_date": competition_date,
            "licence": licence,
            "mode": "activate",
            "event_id": "",
            "duration_days": "",
        }
    )
    return RedirectResponse(f"/payment?{query}", status_code=303)


@app.get("/payment", response_class=HTMLResponse)
def payment_page(
    request: Request,
    wizard: str = "",
    event_name: str = "",
    club_name: str = "",
    contact_email: str = "",
    competition_date: str = "",
    licence: str = "",
    mode: str = "activate",
    event_id: str = "",
    duration_days: str = "",
):
    wizard = (wizard or "").strip().lower()
    licence = _normalise_licence(licence)
    mode = _normalise_mode(mode)
    event_id = (event_id or "").strip()
    duration_days_int = _safe_duration_days(duration_days) if duration_days else 0

    wizard_title = _title_for_wizard(wizard or "triwizard")

    if mode == "extend":
        licence_label = _duration_label(duration_days_int)
        warning = ""
    else:
        licence_label = {
            "2_week": "2-week access",
            "1_month": "1-month access",
        }.get(licence, "selected access")
        warning = _two_week_warning(licence, competition_date)

    return templates.TemplateResponse(
        request,
        "payment.html",
        {
            "wizard": wizard,
            "wizard_title": wizard_title,
            "event_name": event_name,
            "club_name": club_name,
            "contact_email": contact_email,
            "competition_date": competition_date,
            "licence": licence,
            "licence_label": licence_label,
            "warning": warning,
            "mode": mode,
            "event_id": event_id,
            "duration_days": duration_days_int,
        },
    )


@app.post("/create-checkout-session")
def create_checkout_session(
    wizard: str = Form(""),
    event_name: str = Form(""),
    club_name: str = Form(""),
    contact_email: str = Form(""),
    competition_date: str = Form(""),
    licence: str = Form(""),
    mode: str = Form("activate"),
    event_id: str = Form(""),
    duration_days: str = Form(""),
):
    wizard = (wizard or "").strip().lower()
    licence = _normalise_licence(licence)
    mode = _normalise_mode(mode)
    event_id = (event_id or "").strip()
    duration_days_int = _safe_duration_days(duration_days) if duration_days else 0
    competition_date = (competition_date or "").strip()

    if wizard not in {"triwizard", "tetwizard"}:
        return RedirectResponse("/?message=Invalid+wizard", status_code=303)

    if mode == "extend":
        if not event_id:
            return RedirectResponse("/?message=Missing+event+id+for+extension", status_code=303)
        if duration_days_int not in {7, 14, 30}:
            return RedirectResponse("/?message=Invalid+extension+duration", status_code=303)
    else:
        if licence not in {"2_week", "1_month"}:
            return RedirectResponse("/?message=Invalid+licence", status_code=303)
        if not _parse_competition_date(competition_date):
            return RedirectResponse("/?message=Please+enter+a+valid+event+date", status_code=303)

    activation_price_map = {
        "triwizard": {"2_week": 2500, "1_month": 3500},
        "tetwizard": {"2_week": 3500, "1_month": 4500},
    }

    extension_price_map = {
        "triwizard": {7: 1000, 14: 2000, 30: 3000},
        "tetwizard": {7: 1500, 14: 2500, 30: 3500},
    }

    if mode == "extend":
        amount = extension_price_map.get(wizard, {}).get(duration_days_int)
        product_name = f"{_title_for_wizard(wizard)} {_duration_label(duration_days_int)}"
        cancel_query = urllib.parse.urlencode(
            {
                "wizard": wizard,
                "event_name": event_name,
                "club_name": club_name,
                "contact_email": contact_email,
                "competition_date": competition_date,
                "licence": "",
                "mode": "extend",
                "event_id": event_id,
                "duration_days": str(duration_days_int),
            }
        )
    else:
        amount = activation_price_map.get(wizard, {}).get(licence)
        product_name = f"{_title_for_wizard(wizard)} {licence.replace('_', ' ')} access"
        cancel_query = urllib.parse.urlencode(
            {
                "wizard": wizard,
                "event_name": event_name,
                "club_name": club_name,
                "contact_email": contact_email,
                "competition_date": competition_date,
                "licence": licence,
                "mode": "activate",
                "event_id": "",
                "duration_days": "",
            }
        )

    if not amount:
        return RedirectResponse("/?message=Price+not+found", status_code=303)

    base_url = _required_env("BASE_URL")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "gbp",
                        "product_data": {
                            "name": product_name,
                        },
                        "unit_amount": amount,
                    },
                    "quantity": 1,
                }
            ],
            success_url=f"{base_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/payment?{cancel_query}",
            metadata={
                "wizard": wizard,
                "event_name": event_name,
                "club_name": club_name,
                "contact_email": contact_email,
                "competition_date": competition_date,
                "licence": licence,
                "mode": mode,
                "event_id": event_id,
                "duration_days": str(duration_days_int),
            },
        )
    except Exception as e:
        return RedirectResponse(
            f"/?message=Stripe+checkout+error:+{urllib.parse.quote_plus(str(e))}",
            status_code=303,
        )

    return RedirectResponse(session.url, status_code=303)


@app.get("/payment-success")
def payment_success(request: Request, session_id: str | None = None):
    if not session_id:
        return RedirectResponse("/?message=Missing+payment+session", status_code=303)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return RedirectResponse(
            url=f"/?message=Stripe+session+error:+{urllib.parse.quote_plus(str(e))}",
            status_code=303,
        )

    try:
        meta = dict(session.metadata or {})
        wizard = (meta.get("wizard") or "").strip().lower()
        event_name = (meta.get("event_name") or "").strip()
        club_name = (meta.get("club_name") or "").strip()
        contact_email = (meta.get("contact_email") or "").strip()
        competition_date = (meta.get("competition_date") or "").strip()
        licence = (meta.get("licence") or "").strip().lower()
        mode = (meta.get("mode") or "activate").strip().lower()
        event_id = (meta.get("event_id") or "").strip()
        duration_days = _safe_duration_days(meta.get("duration_days"))
except Exception as e:
        return RedirectResponse(
            url=f"/?message=Stripe+metadata+read+error:+{urllib.parse.quote_plus(str(e))}",
            status_code=303,
        )

    if wizard not in {"triwizard", "tetwizard"}:
        return RedirectResponse("/?message=Invalid+wizard+metadata", status_code=303)

    if mode == "extend":
        if not event_id:
            return RedirectResponse("/?message=Missing+event+id+metadata", status_code=303)

        triwizard_base = _required_env("TRIWIZARD_PUBLIC_BASE_URL").rstrip("/")

        return HTMLResponse(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="utf-8" />
              <meta name="viewport" content="width=device-width, initial-scale=1" />
              <title>Completing extension…</title>
              <style>
                body {{
                  font-family: Arial, sans-serif;
                  background: #f6f7fb;
                  color: #111827;
                  margin: 0;
                  padding: 32px 18px;
                }}
                .wrap {{
                  max-width: 680px;
                  margin: 0 auto;
                }}
                .card {{
                  background: #fff;
                  border: 1px solid #e5e7eb;
                  border-radius: 16px;
                  padding: 24px;
                  text-align: center;
                }}
              </style>
            </head>
            <body>
              <div class="wrap">
                <div class="card">
                  <h1>Completing your extension…</h1>
                  <p>Please wait while we restore access to your event.</p>

                  <form id="extendForm" method="post" action="{triwizard_base}/access/extend">
                    <input type="hidden" name="event_id" value="{event_id}">
                    <input type="hidden" name="duration_days" value="{duration_days}">
                  </form>
                </div>
              </div>

              <script>
                document.getElementById("extendForm").submit();
              </script>
            </body>
            </html>
            """
        )

    if licence not in {"2_week", "1_month"}:
        return RedirectResponse("/?message=Invalid+licence+metadata", status_code=303)

    if not _parse_competition_date(competition_date):
        return RedirectResponse("/?message=Invalid+competition+date", status_code=303)

    try:
        launch_url = _create_event_in_triwizard(
            wizard=wizard,
            event_name=event_name,
            club_name=club_name,
            contact_email=contact_email,
            competition_date=competition_date,
            licence=licence,
            payment_status="paid",
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/?message=Bridge+error:+{urllib.parse.quote_plus(str(e))}",
            status_code=303,
        )

    return RedirectResponse(launch_url, status_code=303)


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
def return_lookup(request: Request, email: str = Form("")):
    email = (email or "").strip()
    events = []
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
    return RedirectResponse("/", status_code=303)
