# rules.py
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class DailySummary:
    date: str
    hrv_ms: Optional[float]      # whoop HRV
    rhr_bpm: Optional[float]     # whoop RHR
    sleep_min: Optional[int]     # final (whoop -> garmin -> manual)
    temp_delta: Optional[float]  # whoop skin temp (optional)
    load_7d: Optional[float]     # garmin training load sum last 7d
    avg7_hrv_ms: Optional[float]
    avg7_rhr_bpm: Optional[float]
    cycle_phase: Optional[str]   # "follicular"|"ovulatory"|"early_luteal"|"late_luteal"|"menstruation"
    context: List[str]           # e.g. ["renovation","holiday"]
    time_min: int                # available time today

def _percent_delta(value: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if value is None or baseline is None or baseline == 0:
        return None
    return (value - baseline) / baseline * 100.0

def compute_flags(s: DailySummary) -> Dict[str, bool]:
    flags = {
        "low_hrv": False,
        "high_rhr": False,
        "sleep_debt": False,
        "high_load": False,
        "late_luteal": (s.cycle_phase == "late_luteal"),
    }
    # HRV ↓ >10% vs 7-day avg
    hrv_delta = _percent_delta(s.hrv_ms, s.avg7_hrv_ms)
    if hrv_delta is not None and hrv_delta < -10:
        flags["low_hrv"] = True
    # RHR ↑ >5% vs 7-day avg
    rhr_delta = _percent_delta(s.rhr_bpm, s.avg7_rhr_bpm)
    if rhr_delta is not None and rhr_delta > 5:
        flags["high_rhr"] = True
    # sleep < 6h (360 min)
    if s.sleep_min is not None and s.sleep_min < 360:
        flags["sleep_debt"] = True
    # crude high load threshold – tune to your history
    if s.load_7d is not None and s.load_7d > 600:
        flags["high_load"] = True
    return flags

def _pick_focus(flags: Dict[str, bool], cycle_phase: Optional[str]) -> str:
    # If any red flags or late luteal → recovery/deload bias
    if flags["low_hrv"] or flags["high_rhr"] or flags["sleep_debt"] or flags["high_load"] or flags["late_luteal"]:
        return "Recovery / Deload"
    # Sprint window: follicular/ovulatory
    if cycle_phase in ("follicular", "ovulatory"):
        return "Speed / Strength"
    # Default consolidation
    return "Aerobic Base / Skills"

def _short_session_blocks(focus: str, time_min: int, context: List[str]) -> List[Dict[str, Any]]:
    # Minimal blocks that fit available time
    # Renovation day = avoid heavy legs
    reno = "renovation" in context

    if time_min <= 15:
        if focus == "Recovery / Deload":
            return [
                {"name":"Mobility", "duration_min":8, "content":["hips","t-spine","calves"]},
                {"name":"Core", "duration_min":5, "content":["side plank 2×30″ each","dead-bug 2×8"]},
            ]
        elif focus == "Speed / Strength" and not reno:
            return [
                {"name":"Accel", "duration_min":10, "content":["boots: 3×20 m accel (full rec)","2×20 m strides"]},
                {"name":"Core", "duration_min":5, "content":["pallof press 2×8 each"]},
            ]
        else:
            return [
                {"name":"Z2 brisk walk", "duration_min":12, "content":["RPE 3–4"]},
            ]

    if time_min <= 30:
        if focus == "Recovery / Deload":
            return [
                {"name":"Mobility", "duration_min":8, "content":["hips","t-spine","calves"]},
                {"name":"Technique", "duration_min":10, "content":["wall drill A/B","4×20 m strides (boots)"]},
                {"name":"Core", "duration_min":5, "content":["side plank 3×30″ each"]},
            ]
        elif focus == "Speed / Strength" and not reno:
            return [
                {"name":"Accel + Plyo", "duration_min":12, "content":["boots: 4×20 m accel","broad jump 3×4"]},
                {"name":"Strength (at-home)", "duration_min":10, "content":["backpack split squat 3×8-e"]},
                {"name":"Core", "duration_min":5, "content":["dead-bug 3×8"]},
            ]
        else:
            return [
                {"name":"Z2 run / bike", "duration_min":20, "content":["HR < 75% max"]},
                {"name":"Mobility", "duration_min":8, "content":["hips","glutes"]},
            ]

    # 45–60 min window
    if focus == "Recovery / Deload":
        return [
            {"name":"Z2 run / bike", "duration_min":25, "content":["HR < 75% max"]},
            {"name":"Mobility", "duration_min":10, "content":["full lower + t-spine"]},
            {"name":"Core", "duration_min":10, "content":["side plank 3×30″","dead-bug 3×10"]},
        ]
    elif focus == "Speed / Strength" and not reno:
        return [
            {"name":"Accel + Plyo", "duration_min":15, "content":["boots: 4×20 m accel","fly 2×20 m","broad jump 3×3"]},
            {"name":"Strength (at-home)", "duration_min":20, "content":["backpack front squat 4×6","single-leg RDL 3×8-e"]},
            {"name":"Core", "duration_min":8, "content":["pallof press 3×8-e"]},
        ]
    else:
        return [
            {"name":"Z2 long", "duration_min":35, "content":["HR < 75% max"]},
            {"name":"Technique", "duration_min":10, "content":["passing / ball-handling (trainers)"]},
        ]

def generate_plan(s: DailySummary) -> Dict[str, Any]:
    flags = compute_flags(s)
    focus = _pick_focus(flags, s.cycle_phase)

    blocks = _short_session_blocks(focus, s.time_min, s.context)

    recovery = []
    if flags["sleep_debt"]:
        recovery.append("Aim 7h30–8h sleep tonight; cool room; wind-down 30 min.")
    recovery.append("+500 ml electrolytes after session")
    if s.cycle_phase == "late_luteal":
        recovery.append("Gentle p.m. mobility 10 min; prioritize sleep.")

    cycle_note = None
    if s.cycle_phase in ("follicular","ovulatory"):
        cycle_note = "You’re in a high-performance window—OK to push intensity if you feel fresh."
    elif s.cycle_phase == "late_luteal":
        cycle_note = "Late luteal—deload 15–25% and favor low-impact work."

    return {
        "day_focus": focus,
        "sessions": blocks,
        "recovery": recovery,
        "flags": flags,
        "cycle_note": cycle_note,
        "notes": "Session adapted to time and context; avoid stacking two hard days."
    }
