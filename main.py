import os
import time
import datetime as dt
import secrets
from urllib.parse import urlencode
import httpx
from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.responses import RedirectResponse, JSONResponse
from dotenv import load_dotenv

# Load .env before any other imports that use os.getenv
load_dotenv()

from db import engine, SessionLocal
from models import Base, TokenStore, DailyRecovery, GarminActivity
from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectAuthenticationError,
)
from rules import DailySummary, generate_plan
from telegram_utils import send_telegram_message, format_plan_for_telegram

Base.metadata.create_all(bind=engine)

WHOOP_CLIENT_ID = os.getenv("WHOOP_CLIENT_ID")
WHOOP_CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")
WHOOP_REDIRECT_URI = os.getenv("WHOOP_REDIRECT_URI")

app = FastAPI()

STATE_STORE = {}

def make_state():
    s = secrets.token_urlsafe(16)
    STATE_STORE[s] = int(time.time()) + 600
    return s

def validate_state(s: str):
    exp = STATE_STORE.get(s)
    if not exp:
        return False
    if time.time() > exp:
        STATE_STORE.pop(s, None)
        return False
    STATE_STORE.pop(s, None)
    return True

@app.get("/auth/whoop/start")
def whoop_start():
    state = make_state()
    params = {
        "client_id": WHOOP_CLIENT_ID,
        "redirect_uri": WHOOP_REDIRECT_URI,
        "response_type": "code",
        "scope": "offline read:recovery read:sleep read:workout",
        "state": state,
    }
    url = "https://api.prod.whoop.com/oauth/oauth2/auth?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/auth/whoop/callback")
def whoop_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    if error:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": error, "error_description": error_description},
        )
    if not code or not state:
        raise HTTPException(422, "Missing 'code' or 'state' parameter")
    if not validate_state(state):
        raise HTTPException(400, "Invalid or expired state")

    token_url = "https://api.prod.whoop.com/oauth/oauth2/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": WHOOP_REDIRECT_URI,
        "client_id": WHOOP_CLIENT_ID,
        "client_secret": WHOOP_CLIENT_SECRET
    }
    with httpx.Client(timeout=15) as client:
        r = client.post(token_url, data=data)
        r.raise_for_status()
        tok = r.json()

    expires_at = int(time.time()) + tok["expires_in"]

    db = SessionLocal()
    row = db.query(TokenStore).filter_by(provider="whoop").one_or_none()
    if not row:
        row = TokenStore(provider="whoop")
    row.access_token = tok["access_token"]
    row.refresh_token = tok["refresh_token"]
    row.expires_at = expires_at
    db.add(row)
    db.commit()
    return JSONResponse({"status": "whoop connected"})

def get_valid_token(db):
    row = db.query(TokenStore).filter_by(provider="whoop").one_or_none()
    if not row:
        raise HTTPException(400, "Whoop not connected")
    if time.time() < row.expires_at - 60:
        return row.access_token

    token_url = "https://api.prod.whoop.com/oauth/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": row.refresh_token,
        "client_id": WHOOP_CLIENT_ID,
        "client_secret": WHOOP_CLIENT_SECRET
    }
    with httpx.Client(timeout=15) as client:
        r = client.post(token_url, data=data)
        r.raise_for_status()
        tok = r.json()
    row.access_token = tok["access_token"]
    row.refresh_token = tok.get("refresh_token", row.refresh_token)
    row.expires_at = int(time.time()) + tok["expires_in"]
    db.add(row); db.commit()
    return row.access_token

@app.post("/whoop/sync")
def whoop_sync():
    db = SessionLocal()
    token = get_valid_token(db)
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=30) as client:
        rec = client.get(
            "https://api.prod.whoop.com/developer/v2/recovery",
            params={"limit": 1, "order": "desc"},
            headers=headers
        )
        rec_json = rec.json() if rec.status_code == 200 else {}

    r0 = None
    if isinstance(rec_json, dict) and rec_json.get("records"):
        r0 = rec_json["records"][0]

    hrv_ms = None
    rhr_bpm = None
    temp_delta = None
    rec_date = dt.date.today() - dt.timedelta(days=1)
    sleep_id = None

    if r0 and isinstance(r0, dict):
        score = r0.get("score", {}) or {}
        hrv_ms = score.get("hrv_rmssd_milli")
        rhr_bpm = score.get("resting_heart_rate")
        temp_delta = score.get("skin_temp_celsius")
        sleep_id = r0.get("sleep_id")
        rec_date_str = r0.get("created_at") or r0.get("score_timestamp")
        if rec_date_str:
            try:
                rec_date = dt.datetime.fromisoformat(rec_date_str.replace("Z", "+00:00")).date()
            except Exception:
                pass

    sleep_json = {}
    if sleep_id:
        with httpx.Client(timeout=30) as client:
            slp_by_id = client.get(
                f"https://api.prod.whoop.com/developer/v2/sleep/{sleep_id}",
                headers=headers
            )
            if slp_by_id.status_code == 200:
                sleep_json = slp_by_id.json()
            elif slp_by_id.status_code == 404:
                sleep_json = {}
            else:
                sleep_json = {}

    if not sleep_json:
        with httpx.Client(timeout=30) as client:
            slp = client.get(
                "https://api.prod.whoop.com/developer/v2/sleep",
                params={"limit": 1, "order": "desc"},
                headers=headers
            )
            if slp.status_code == 200:
                sleep_json = slp.json()

    if not sleep_json:
        today = dt.date.today()
        start_dt = dt.datetime.combine(today - dt.timedelta(days=3), dt.time.min).isoformat() + "Z"
        end_dt = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min).isoformat() + "Z"
        with httpx.Client(timeout=30) as client:
            slp2 = client.get(
                "https://api.prod.whoop.com/developer/v2/sleep",
                params={"start": start_dt, "end": end_dt},
                headers=headers
            )
            if slp2.status_code == 200:
                sleep_json = slp2.json()

    sleep_min = 0
    def extract_sleep_minutes(record: dict) -> int:
        dur = (
            (record.get("sleep") or {}).get("duration")
            or (record.get("score") or {}).get("total_sleep_duration")
            or record.get("duration")
        )
        if dur:
            try:
                dur = int(dur)
                return int(round(dur / 60))
            except Exception:
                pass
        return 0

    if isinstance(sleep_json, dict) and sleep_json.get("records"):
        s0 = sleep_json["records"][0]
        sleep_min = extract_sleep_minutes(s0)
    elif isinstance(sleep_json, dict) and sleep_json:
        sleep_min = extract_sleep_minutes(sleep_json)

    row = db.query(DailyRecovery).filter_by(date=rec_date).one_or_none()
    if not row:
        row = DailyRecovery(date=rec_date)
    row.hrv_ms = hrv_ms
    row.rhr_bpm = rhr_bpm
    row.sleep_min = sleep_min
    row.temp_delta = temp_delta
    row.raw = {"recovery": rec_json, "sleep": sleep_json}
    db.add(row); db.commit()

    return {
        "status": "ok",
        "date": str(rec_date),
        "hrv_ms": hrv_ms,
        "rhr_bpm": rhr_bpm,
        "sleep_min": sleep_min
    }

def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

@app.post("/garmin/sync")
def garmin_sync():
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    db = SessionLocal()
    try:
        client = Garmin(email, password)
        client.login()
    except (GarminConnectConnectionError, GarminConnectAuthenticationError) as e:
        raise HTTPException(400, f"Garmin login failed: {e}")

    activities = client.get_activities(0, 10)
    new_activities = 0
    for act in activities:
        act_id = str(act["activityId"])
        if db.query(GarminActivity).filter_by(activity_id=act_id).first():
            continue
        ga = GarminActivity(
            activity_id=act_id,
            start_time=dt.datetime.fromisoformat(act["startTimeLocal"]),
            sport=act.get("activityType", {}).get("typeKey"),
            duration_s=safe_float(act.get("duration")),
            distance_m=safe_float(act.get("distance")),
            avg_hr=safe_float(act.get("averageHR")),
            max_hr=safe_float(act.get("maxHR")),
            training_load=safe_float(act.get("trainingLoad")),
            vo2max=safe_float(act.get("vo2MaxValue")),
            summary_json=act,
        )
        db.add(ga)
        new_activities += 1
    db.commit()
    return {"status": "ok", "new_activities": new_activities}

@app.post("/api/trigger")
def trigger_plan(payload: dict = Body(default={})):
    db = SessionLocal()
    today = dt.date.today()
    daily_rec = db.query(DailyRecovery).filter_by(date=today).first()

    seven_days_ago = today - dt.timedelta(days=7)
    garmin_activities = db.query(GarminActivity).filter(GarminActivity.start_time >= seven_days_ago).all()
    garmin_load_7d = sum([a.training_load or 0 for a in garmin_activities]) if garmin_activities else 0

    hrv_records = db.query(DailyRecovery).filter(DailyRecovery.hrv_ms != None).order_by(DailyRecovery.date.desc()).limit(7).all()
    avg7_hrv = sum([r.hrv_ms for r in hrv_records]) / len(hrv_records) if hrv_records else None

    rhr_records = db.query(DailyRecovery).filter(DailyRecovery.rhr_bpm != None).order_by(DailyRecovery.date.desc()).limit(7).all()
    avg7_rhr = sum([r.rhr_bpm for r in rhr_records]) / len(rhr_records) if rhr_records else None

    sleep_min = None
    if daily_rec and daily_rec.sleep_min and daily_rec.sleep_min > 0:
        sleep_min = daily_rec.sleep_min
    elif "manual_sleep_min" in payload:
        sleep_min = int(payload["manual_sleep_min"])
    else:
        sleep_min = None

    cycle_phase = payload.get("cycle_phase") or None

    daily = DailySummary(
        date=str(today),
        hrv_ms=daily_rec.hrv_ms if daily_rec else None,
        rhr_bpm=daily_rec.rhr_bpm if daily_rec else None,
        sleep_min=sleep_min,
        temp_delta=daily_rec.temp_delta if daily_rec else None,
        load_7d=garmin_load_7d,
        avg7_hrv_ms=avg7_hrv,
        avg7_rhr_bpm=avg7_rhr,
        cycle_phase=cycle_phase,
        context=payload.get("context", []),
        time_min=int(payload.get("time_min", 30)),
    )

    plan = generate_plan(daily)
    msg = format_plan_for_telegram(plan)
    send_telegram_message(msg)
    return plan

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "").strip().lower()

    # Only respond to your own chat
    if chat_id != os.getenv("TELEGRAM_CHAT_ID"):
        return {"ok": True}

    db = SessionLocal()
    today = dt.date.today()

    if text.startswith("log sleep"):
        # Example: "log sleep 7.5h"
        parts = text.split()
        if len(parts) >= 3:
            try:
                hours = float(parts[2].replace("h", "").replace(",", "."))
                minutes = int(hours * 60)
                # Update today's DailyRecovery
                row = db.query(DailyRecovery).filter_by(date=today).first()
                if not row:
                    row = DailyRecovery(date=today)
                row.sleep_min = minutes
                db.add(row)
                db.commit()
                send_telegram_message(f"Logged sleep: {minutes} min")
            except Exception:
                send_telegram_message("Couldn't parse sleep amount. Try 'log sleep 7.5h'")
        else:
            send_telegram_message("Usage: log sleep 7.5h")
    elif text.startswith("cycle phase"):
        # Example: "cycle phase late_luteal"
        parts = text.split()
        if len(parts) >= 3:
            phase = parts[2]
            # Update today's DailyRecovery (or your cycle tracking table)
            row = db.query(DailyRecovery).filter_by(date=today).first()
            if not row:
                row = DailyRecovery(date=today)
            row.raw = row.raw or {}
            row.raw["cycle_phase"] = phase
            db.add(row)
            db.commit()
            send_telegram_message(f"Cycle phase updated to: {phase}")
        else:
            send_telegram_message("Usage: cycle phase late_luteal")
    elif text in ["plan", "summary"]:
        # Generate or fetch today's plan
        payload = {}  # You can add more context if needed
        daily_rec = db.query(DailyRecovery).filter_by(date=today).first()
        seven_days_ago = today - dt.timedelta(days=7)
        garmin_activities = db.query(GarminActivity).filter(GarminActivity.start_time >= seven_days_ago).all()
        garmin_load_7d = sum([a.training_load or 0 for a in garmin_activities]) if garmin_activities else 0
        hrv_records = db.query(DailyRecovery).filter(DailyRecovery.hrv_ms != None).order_by(DailyRecovery.date.desc()).limit(7).all()
        avg7_hrv = sum([r.hrv_ms for r in hrv_records]) / len(hrv_records) if hrv_records else None
        rhr_records = db.query(DailyRecovery).filter(DailyRecovery.rhr_bpm != None).order_by(DailyRecovery.date.desc()).limit(7).all()
        avg7_rhr = sum([r.rhr_bpm for r in rhr_records]) / len(rhr_records) if rhr_records else None
        sleep_min = daily_rec.sleep_min if daily_rec and daily_rec.sleep_min else None
        cycle_phase = daily_rec.raw.get("cycle_phase") if daily_rec and daily_rec.raw else None
        daily = DailySummary(
            date=str(today),
            hrv_ms=daily_rec.hrv_ms if daily_rec else None,
            rhr_bpm=daily_rec.rhr_bpm if daily_rec else None,
            sleep_min=sleep_min,
            temp_delta=daily_rec.temp_delta if daily_rec else None,
            load_7d=garmin_load_7d,
            avg7_hrv_ms=avg7_hrv,
            avg7_rhr_bpm=avg7_rhr,
            cycle_phase=cycle_phase,
            context=[],
            time_min=30,
        )
        plan = generate_plan(daily)
        msg = format_plan_for_telegram(plan)
        send_telegram_message(msg)
    else:
        send_telegram_message("Commands: log sleep [hours], cycle phase [phase], plan, summary")

    return {"ok": True}
