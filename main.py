from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import stripe
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


app = FastAPI(title="Equizard")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DATA_DIR = Path("data")
FULFILLED_SESSIONS_PATH = DATA_DIR / "fulfilled_checkout_sessions.json"


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


def _stripe_webhook_secret() -> str:
    return _required_env("STRIPE_WEBHOOK_SECRET")


# -----------------------------------------------------
# Safe Stripe helpers
# -----------------------------------------------------
def _session_field(session: Any, key: str, default: Any = "") -> Any:
    """
    Works with:
      - webhook payload dicts
      - Stripe SDK objects
    """
    if isinstance(session, dict):
        return session.get(key, default)

    try:
        return getattr(session, key, default)
    except Exception:
        return default


def _session_metadata(session: Any) -> dict:
    meta = _session_field(session, "metadata", {}) or {}

    if isinstance(meta, dict):
        return meta

    try:
        return dict(meta)
    except Exception:
        return {}


def _meta_str(meta: dict, key: str, default: str = "") -> str:
    try:
        return str(meta.get(key, default)).strip()
    except Exception:
        return default


# -----------------------------------------------------
# Fulfilled session ledger
# -----------------------------------------------------
def _load_fulfilled_sessions() -> set[str]:
    if not FULFILLED_SESSIONS_PATH.exists():
        return set()

    try:
        data = json.loads(FULFILLED_SESSIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x).strip() for x in data if str(x).strip()}
        return set()
    except Exception:
        return set()


def _save_fulfilled_sessions(session_ids: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FULFILLED_SESSIONS_PATH.write_text(
        json.dumps(sorted(session_ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_session_fulfilled(session_id: str) -> bool:
    return session_id in _load_fulfilled_sessions()


def _mark_session_fulfilled(session_id: str) -> None:
    if not session_id:
        return
    fulfilled = _load_fulfilled_sessions()
    fulfilled.add(session_id)
    _save_fulfilled_sessions(fulfilled)


# -----------------------------------------------------
# Stripe init
# -----------------------------------------------------
stripe.api_key = _required_env("STRIPE_SECRET_KEY")


# -----------------------------------------------------
# TriWizard bridge helpers
# -----------------------------------------------------
def _create_event_in_triwizard(
    wizard: str,
    event_name: str,
    club_name: str,
    contact_email: str,
    competition_date: str,
    licence: str = "free",
    payment_status: str = "none",
    external_ref: str = "",
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
            "external_ref": external_ref,
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


def _extend_access_in_triwizard(
    event_id: str,
    duration_days: int,
) -> dict:
    extend_url = f"{_triwizard_public_base_url()}/portal/extend-access"
    shared_secret = _required_env("PORTAL_SHARED_SECRET")

    form_data = urllib.parse.urlencode(
        {
            "event_id": (event_id or "").strip(),
            "duration_days": str(_safe_duration_days(duration_days)),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        extend_url,
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
        raise RuntimeError(f"Extend HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Extend URL error: {e.reason}") from e

    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"Extend failed: {payload}")

    return payload


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
# Fulfilment
# -----------------------------------------------------
def _fulfil_checkout_session(session: Any) -> dict:
    session_id = str(_session_field(session, "id", "") or "").strip()
    if not session_id:
        raise RuntimeError("Missing Stripe session id")

    if _is_session_fulfilled(session_id):
        return {"ok": True, "already_fulfilled": True, "session_id": session_id}

    payment_status = str(_session_field(session, "payment_status", "") or "").strip().lower()
    if payment_status != "paid":
        raise RuntimeError(f"Session not paid (payment_status={payment_status})")

    meta = _session_metadata(session)

    wizard = _meta_str(meta, "wizard", "triwizard").lower()
    if wizard not in {"triwizard", "tetwizard"}:
        wizard = "triwizard"
    
    event_name = _meta_str(meta, "event_name")
    club_name = _meta_str(meta, "club_name")
    contact_email = _meta_str(meta, "contact_email")
    competition_date = _meta_str(meta, "competition_date")
    licence = _meta_str(meta, "licence").lower()
    mode = _meta_str(meta, "mode", "activate").lower()
    event_id = _meta_str(meta, "event_id")
    duration_days = _safe_duration_days(_meta_str(meta, "duration_days", "0"))
    if wizard not in {"triwizard", "tetwizard"}:
        raise RuntimeError("Invalid wizard metadata")

    if mode == "extend":
        if not event_id:
            raise RuntimeError("Missing event_id metadata for extension")

        result = _extend_access_in_triwizard(
            event_id=event_id,
            duration_days=duration_days,
        )
        _mark_session_fulfilled(session_id)
        return {
            "ok": True,
            "mode": "extend",
            "session_id": session_id,
            "result": result,
        }

    if licence not in {"2_week", "1_month"}:
        raise RuntimeError("Invalid licence metadata")

    if not _parse_competition_date(competition_date):
        raise RuntimeError("Invalid competition_date metadata")

    launch_url = _create_event_in_triwizard(
        wizard=wizard,
        event_name=event_name,
        club_name=club_name,
        contact_email=contact_email,
        competition_date=competition_date,
        licence=licence,
        payment_status="paid",
        external_ref=session_id,
    )

    _mark_session_fulfilled(session_id)

    return {
        "ok": True,
        "mode": "activate",
        "session_id": session_id,
        "launch_url": launch_url,
    }


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


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=_stripe_webhook_secret(),
        )
    except ValueError:
        return JSONResponse({"ok": False, "error": "Invalid payload"}, status_code=400)
    except stripe.error.SignatureVerificationError:
        return JSONResponse({"ok": False, "error": "Invalid signature"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    event_type = str(event.get("type", "") or "").strip()

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]

        try:
            _fulfil_checkout_session(session)
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"Fulfilment failed: {e}"},
                status_code=500,
            )

    return JSONResponse({"ok": True})


@app.get("/payment-success")
def payment_success(request: Request, session_id: str | None = None):
    if not session_id:
        return RedirectResponse("/?message=Missing+payment+session", status_code=303)

    # -------------------------------------------------
    # Retrieve Stripe session safely
    # -------------------------------------------------
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return RedirectResponse(
            url=f"/?message=Stripe+session+error:+{urllib.parse.quote_plus(str(e))}",
            status_code=303,
        )

    # -------------------------------------------------
    # Attempt fulfilment immediately
    # -------------------------------------------------
    try:
        result = _fulfil_checkout_session(session)
    except Exception as e:
        return RedirectResponse(
            url=f"/?message=Fulfilment+error:+{urllib.parse.quote_plus(str(e))}",
            status_code=303,
        )

    # -------------------------------------------------
    # If already fulfilled (refresh / retry case)
    # -------------------------------------------------
    if result.get("already_fulfilled"):
        # Try to reconstruct launch if possible
        launch_url = result.get("launch_url")
        if launch_url:
            return RedirectResponse(launch_url, status_code=303)

        return RedirectResponse(
            url="/?message=Payment+already+processed",
            status_code=303,
        )

    # -------------------------------------------------
    # EXTENSION FLOW
    # -------------------------------------------------
    if result.get("mode") == "extend":
        return RedirectResponse(
            url="/?message=Extension+applied+successfully",
            status_code=303,
        )

    # -------------------------------------------------
    # ACTIVATION FLOW (PRIMARY PATH 🚀)
    # -------------------------------------------------
    launch_url = result.get("launch_url")

    if launch_url:
        return RedirectResponse(launch_url, status_code=303)

    # -------------------------------------------------
    # Fallback (should never happen)
    # -------------------------------------------------
    return RedirectResponse(
        url="/?message=Unexpected+error+during+launch",
        status_code=303,
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
def return_lookup(request: Request, email: str = Form("")):
    email = (email or "").strip()
    events = []
    message = ""

    if email:
        try:
            events = _get_return_links_from_triwizard(email)
        except Exception as e:
            message = f"Unable to retrieve events at the moment: {e}"
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
