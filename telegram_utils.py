import os
import requests

def send_telegram_message(text: str):
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bot token or chat ID not set.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def format_plan_for_telegram(plan: dict) -> str:
    msg = f"*Day Focus:* {plan['day_focus']}\n"
    msg += "*Sessions:*\n"
    for s in plan['sessions']:
        msg += f"  - {s['name']} ({s['duration_min']} min): " + ", ".join(s['content']) + "\n"
    msg += "*Recovery:*\n"
    for r in plan['recovery']:
        msg += f"  - {r}\n"
    if plan.get("cycle_note"):
        msg += f"*Cycle Note:* {plan['cycle_note']}\n"
    msg += f"_Notes:_ {plan['notes']}\n"
    return msg
