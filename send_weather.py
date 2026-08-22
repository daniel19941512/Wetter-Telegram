#!/usr/bin/env python3
"""
Schickt 3x täglich (per GitHub Actions Cron) die aktuellen Niederschlags-Karten
von wetterzentrale.de für ECMWF, GFS, ICON und AIFS an einen Telegram-Chat.

Kartentyp: Gesamt-Niederschlagssumme (var=18), Region Mitteleuropa (map=3),
Vorhersagestunde +144h (6 Tage), jeweils der aktuellste verfügbare Modelllauf.

Für GFS wird zusätzlich versucht, das echte Ensemble-Mittel (Member "AVG" =
"Mittel aller Berechnungen") zu holen, da wetterzentrale.de dieses Produkt nur
für GFS anbietet. ECMWF, ICON und AIFS haben dort kein Ensemble-Mittel für
diese Karte - dort wird der reguläre (operationelle) Lauf verwendet.

Benötigte Umgebungsvariablen (als GitHub Secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

MAP_REGION = 3      # Mitteleuropa
VAR_PRECIP = 18      # Gesamt-Niederschlagssumme
FORECAST_HOUR = 144   # +144h (6 Tage)
MAX_RUN_FALLBACKS = 6  # wie viele 6h-Schritte zurück probiert werden, falls Karte noch nicht da ist

BASE_URL = "https://wetterzentrale.de/maps/{model}{lid}ME{run:02d}_{time}_{var}.png"

# (Filename-Präfix, Anzeigename, bevorzugtes Member: "AVG" = Ensemble-Mittel, "OP" = operationeller Lauf)
MODELS = [
    ("ECM", "ECMWF", "OP"),
    ("GFS", "GFS", "AVG"),
    ("ICO", "ICON", "OP"),
    ("AIFS", "AIFS", "OP"),
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def latest_run_slot(now_utc: datetime) -> datetime:
    """Rundet auf den letzten 00/06/12/18Z-Slot ab."""
    hour = (now_utc.hour // 6) * 6
    return now_utc.replace(hour=hour, minute=0, second=0, microsecond=0)


def build_url(model: str, lid: str, run_dt: datetime) -> str:
    return BASE_URL.format(model=model, lid=lid, run=run_dt.hour, time=FORECAST_HOUR, var=VAR_PRECIP)


def image_exists(url: str) -> bool:
    """Prüft, ob unter der URL tatsächlich ein Bild liegt (statt einer Fehlerseite)."""
    try:
        r = requests.get(url, timeout=20, stream=True)
        ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image")
        r.close()
        return ok
    except requests.RequestException:
        return False


def find_latest_available(model: str, preferred_lid: str, now_utc: datetime):
    """
    Sucht rückwärts (in 6h-Schritten) den neuesten Lauf, für den es tatsächlich
    ein Bild gibt. Versucht zuerst das bevorzugte Member (z.B. AVG), fällt bei
    Fehlschlag auf "OP" zurück.
    """
    run_dt = latest_run_slot(now_utc)
    for _ in range(MAX_RUN_FALLBACKS):
        for lid in dict.fromkeys([preferred_lid, "OP"]):  # preferred zuerst, Duplikate raus
            url = build_url(model, lid, run_dt)
            if image_exists(url):
                return url, lid, run_dt
        run_dt -= timedelta(hours=6)
    return None, None, None


def build_caption(display_name: str, lid: str, run_dt: datetime) -> str:
    kind = "Ensemble-Mittel (AVG)" if lid == "AVG" else "operationeller Lauf"
    return (
        f"{display_name} – {kind}\n"
        f"Lauf {run_dt.strftime('%d.%m.%Y %HZ')} · Gesamt-Niederschlag bis +{FORECAST_HOUR}h · Mitteleuropa"
    )


def send_media_group(items):
    media = []
    for url, caption in items:
        media.append({"type": "photo", "media": url, "caption": caption})

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    resp = requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "media": media}, timeout=30)
    return resp


def send_single_photo(url, caption):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        api_url,
        data={"chat_id": TELEGRAM_CHAT_ID, "photo": url, "caption": caption},
        timeout=30,
    )
    return resp


def send_text(text):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    return requests.post(api_url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30)


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("FEHLER: TELEGRAM_BOT_TOKEN und/oder TELEGRAM_CHAT_ID sind nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    items = []
    missing = []

    for model_code, display_name, preferred_lid in MODELS:
        url, lid, run_dt = find_latest_available(model_code, preferred_lid, now_utc)
        if url is None:
            missing.append(display_name)
            print(f"WARNUNG: Keine aktuelle Karte für {display_name} gefunden.")
            continue
        caption = build_caption(display_name, lid, run_dt)
        items.append((url, caption))
        print(f"OK: {display_name} -> {url}")

    if not items:
        send_text("⚠️ Wetterkarten-Update: Es konnte aktuell keine einzige Karte von wetterzentrale.de geladen werden.")
        sys.exit(1)

    if len(items) == 1:
        resp = send_single_photo(*items[0])
    else:
        resp = send_media_group(items)

    print("Telegram-Antwort:", resp.status_code, resp.text[:500])

    if missing:
        send_text(f"ℹ️ Hinweis: Für folgende Modelle war gerade keine aktuelle Karte verfügbar: {', '.join(missing)}")

    if resp.status_code != 200:
        sys.exit(1)


if __name__ == "__main__":
    main()
