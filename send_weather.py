#!/usr/bin/env python3
"""
Schickt 3x täglich (per GitHub Actions Cron) aktuelle Niederschlags-Informationen
von wetterzentrale.de für ECMWF, GFS, ICON und AIFS an einen Telegram-Chat.

Teil 1 – Gesamtkarten (statische Bilder):
  Gesamt-Niederschlagssumme (var=18), Region Mitteleuropa (map=3),
  Vorhersagestunde +144h (6 Tage), jeweils der aktuellste verfügbare Modelllauf.
  Für GFS wird das echte Ensemble-Mittel (Member "AVG") verwendet, da
  wetterzentrale.de das auf dieser Karte nur für GFS anbietet. Bei ECMWF, ICON
  und AIFS gibt es dort kein Ensemble-Mittel für diese Karte -> operationeller Lauf.

Teil 2 – Ensemble-Diagramm für Ebringen (statisches Bild):
  Nutzt den Server-Endpunkt ens_image.php (Kombi-Chart "850 hPa Temp. &
  Niederschlag"), der ein fertiges PNG liefert - kein JavaScript-Rendering,
  kein Cookie-Banner-Problem wie bei der interaktiven Diagrammseite. Enthält
  alle Ensemble-Member + AVG (= "Mittel aller Berechnungen") + operationeller
  Lauf, sofern das Modell Ensemble-Daten anbietet (member=ENS). Falls nicht,
  Rückfall auf den operationellen Lauf (member=OP).

Benötigte Umgebungsvariablen (als GitHub Secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# Konfiguration – Gesamtkarten
# ---------------------------------------------------------------------------

MAP_REGION = 3        # Mitteleuropa
VAR_PRECIP = 18        # Gesamt-Niederschlagssumme
FORECAST_HOUR = 144     # +144h (6 Tage)
MAX_RUN_FALLBACKS = 6    # wie viele 6h-Schritte zurück probiert werden, falls noch nichts da ist

MAP_URL = "https://wetterzentrale.de/maps/{model}{lid}ME{run:02d}_{time}_{var}.png"

# (Filename-Präfix, Anzeigename, bevorzugtes Member: "AVG" = Ensemble-Mittel, "OP" = operationeller Lauf)
MAP_MODELS = [
    ("ECM", "ECMWF", "OP"),
    ("GFS", "GFS", "AVG"),
    ("ICO", "ICON", "OP"),
    ("AIFS", "AIFS", "OP"),
]

# ---------------------------------------------------------------------------
# Konfiguration – Ensemble-Diagramm (Station Ebringen)
# ---------------------------------------------------------------------------

DIAGRAM_GEOID = 141668       # Ebringen 79285 (DE)
DIAGRAM_VAR = 201             # Kombi: 850 hPa Temp. & Niederschlag
DIAGRAM_STATION_LABEL = "Ebringen"
DIAGRAM_URL = (
    "https://wetterzentrale.de/de/ens_image.php"
    "?geoid={geoid}&var={var}&run={run:02d}&date={date}&model={model}&member={member}&bw=1"
)

# (Modellcode, Anzeigename)
DIAGRAM_MODELS = [
    ("ecm", "ECMWF"),
    ("gfs", "GFS"),
    ("ico", "ICON"),
    ("aifs", "AIFS"),
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def latest_run_slot(now_utc: datetime) -> datetime:
    """Rundet auf den letzten 00/06/12/18Z-Slot ab."""
    hour = (now_utc.hour // 6) * 6
    return now_utc.replace(hour=hour, minute=0, second=0, microsecond=0)


def image_exists(url: str) -> bool:
    """Prüft, ob unter der URL tatsächlich ein Bild liegt (statt einer Fehlerseite)."""
    try:
        r = requests.get(url, timeout=20, stream=True)
        ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image")
        r.close()
        return ok
    except requests.RequestException:
        return False


# ---------------------------------------------------------------------------
# Teil 1: Gesamtkarten
# ---------------------------------------------------------------------------

def build_map_url(model: str, lid: str, run_dt: datetime) -> str:
    return MAP_URL.format(model=model, lid=lid, run=run_dt.hour, time=FORECAST_HOUR, var=VAR_PRECIP)


def find_latest_map(model: str, preferred_lid: str, now_utc: datetime):
    """
    Sucht rückwärts (in 6h-Schritten) den neuesten Lauf, für den es tatsächlich
    ein Kartenbild gibt. Versucht zuerst das bevorzugte Member (z.B. AVG), fällt
    bei Fehlschlag auf "OP" zurück.
    """
    run_dt = latest_run_slot(now_utc)
    for _ in range(MAX_RUN_FALLBACKS):
        for lid in dict.fromkeys([preferred_lid, "OP"]):  # preferred zuerst, Duplikate raus
            url = build_map_url(model, lid, run_dt)
            if image_exists(url):
                return url, lid, run_dt
        run_dt -= timedelta(hours=6)
    return None, None, None


def build_map_caption(display_name: str, lid: str, run_dt: datetime) -> str:
    kind = "Ensemble-Mittel (AVG)" if lid == "AVG" else "operationeller Lauf"
    return (
        f"{display_name} – {kind}\n"
        f"Lauf {run_dt.strftime('%d.%m.%Y %HZ')} · Gesamt-Niederschlag bis +{FORECAST_HOUR}h · Mitteleuropa"
    )


def collect_maps(now_utc):
    items, missing = [], []
    for model_code, display_name, preferred_lid in MAP_MODELS:
        url, lid, run_dt = find_latest_map(model_code, preferred_lid, now_utc)
        if url is None:
            missing.append(display_name)
            print(f"WARNUNG: Keine aktuelle Karte für {display_name} gefunden.")
            continue
        items.append((url, build_map_caption(display_name, lid, run_dt)))
        print(f"OK Karte: {display_name} -> {url}")
    return items, missing


# ---------------------------------------------------------------------------
# Teil 2: Ensemble-Diagramm (statisches Bild von ens_image.php)
# ---------------------------------------------------------------------------

def build_diagram_url(model: str, member: str, run_dt: datetime) -> str:
    return DIAGRAM_URL.format(
        geoid=DIAGRAM_GEOID,
        var=DIAGRAM_VAR,
        run=run_dt.hour,
        date=run_dt.strftime("%Y-%m-%d"),
        model=model,
        member=member,
    )


def find_latest_diagram(model: str, now_utc: datetime):
    """
    Sucht rückwärts (in 6h-Schritten) den neuesten Lauf, für den es das
    Diagramm-Bild gibt. Versucht zuerst das Ensemble (member=ENS), fällt bei
    Fehlschlag auf den operationellen Lauf (member=OP) zurück.
    """
    run_dt = latest_run_slot(now_utc)
    for _ in range(MAX_RUN_FALLBACKS):
        for member in ("ENS", "OP"):
            url = build_diagram_url(model, member, run_dt)
            if image_exists(url):
                return url, member, run_dt
        run_dt -= timedelta(hours=6)
    return None, None, None


def build_diagram_caption(display_name: str, member: str, run_dt: datetime) -> str:
    kind = "Ensemble (alle Berechnungen inkl. Mittel/AVG)" if member == "ENS" else "operationeller Lauf"
    return (
        f"{display_name} – {DIAGRAM_STATION_LABEL} – {kind}\n"
        f"Lauf {run_dt.strftime('%d.%m.%Y %HZ')} · 850hPa-Temp. & Niederschlag"
    )


def collect_diagrams(now_utc):
    items, missing = [], []
    for model_code, display_name in DIAGRAM_MODELS:
        url, member, run_dt = find_latest_diagram(model_code, now_utc)
        if url is None:
            missing.append(display_name)
            print(f"WARNUNG: Kein Diagramm für {display_name} gefunden.")
            continue
        items.append((url, build_diagram_caption(display_name, member, run_dt)))
        print(f"OK Diagramm: {display_name} -> {url}")
    return items, missing


# ---------------------------------------------------------------------------
# Telegram-Versand
# ---------------------------------------------------------------------------

def send_media_group_urls(items):
    media = [{"type": "photo", "media": url, "caption": caption} for url, caption in items]
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    return requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "media": media}, timeout=30)


def send_single_photo_url(url, caption):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    return requests.post(
        api_url, data={"chat_id": TELEGRAM_CHAT_ID, "photo": url, "caption": caption}, timeout=30
    )


def send_text(text):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    return requests.post(api_url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30)


def send_group(items, label: str) -> bool:
    """Verschickt eine Gruppe von Bild-URLs als eine Telegram-Nachricht, meldet Erfolg zurück."""
    if not items:
        return False
    resp = send_single_photo_url(*items[0]) if len(items) == 1 else send_media_group_urls(items)
    print(f"Telegram-Antwort ({label}):", resp.status_code, resp.text[:500])
    return resp.status_code == 200


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("FEHLER: TELEGRAM_BOT_TOKEN und/oder TELEGRAM_CHAT_ID sind nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)

    map_items, map_missing = collect_maps(now_utc)
    diagram_items, diagram_missing = collect_diagrams(now_utc)

    maps_ok = send_group(map_items, label="Karten")
    diagrams_ok = send_group(diagram_items, label="Diagramme")

    missing = map_missing + diagram_missing
    if missing:
        send_text(f"ℹ️ Hinweis: Für folgende Modelle/Produkte war gerade nichts Aktuelles verfügbar: {', '.join(missing)}")

    if not map_items and not diagram_items:
        send_text("⚠️ Wetter-Update: Es konnte gerade weder eine Karte noch ein Diagramm von wetterzentrale.de geladen werden.")
        sys.exit(1)

    if not maps_ok and not diagrams_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
