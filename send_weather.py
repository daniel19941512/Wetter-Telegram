#!/usr/bin/env python3
"""
Schickt 3x täglich (per GitHub Actions Cron) aktuelle Niederschlags-Informationen
von wetterzentrale.de für ECMWF, GFS, ICON und AIFS an einen Telegram-Chat.

Teil 1 – Gesamtkarten (statische Bilder, schnell):
  Gesamt-Niederschlagssumme (var=18), Region Mitteleuropa (map=3),
  Vorhersagestunde +144h (6 Tage), jeweils der aktuellste verfügbare Modelllauf.
  Für GFS wird das echte Ensemble-Mittel (Member "AVG") verwendet, da
  wetterzentrale.de das auf dieser Karte nur für GFS anbietet. Bei ECMWF, ICON
  und AIFS gibt es dort kein Ensemble-Mittel für diese Karte -> operationeller Lauf.

Teil 2 – Ensemble-Diagramm für Ebringen (JS-Chart, per Screenshot):
  Diese Diagrammseite (show_diagrams.php) rendert den Chart per JavaScript,
  es gibt kein fertiges Bild zum Verlinken. Deshalb wird die Seite mit einem
  headless Chromium-Browser (Playwright) geöffnet und ein Screenshot gemacht.
  Enthält alle Ensemble-Member + CONTROL + AVG (= "Mittel aller Berechnungen")
  + OPER, sofern das Modell Ensemble-Daten anbietet (lid=ENS). Falls nicht
  (z.B. vermutlich bei AIFS), Rückfall auf den operationellen Lauf (lid=OP).

Benötigte Umgebungsvariablen (als GitHub Secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Konfiguration – Gesamtkarten
# ---------------------------------------------------------------------------

MAP_REGION = 3        # Mitteleuropa
VAR_PRECIP = 18        # Gesamt-Niederschlagssumme
FORECAST_HOUR = 144     # +144h (6 Tage)
MAX_RUN_FALLBACKS = 6    # wie viele 6h-Schritte zurück probiert werden, falls Karte noch nicht da ist

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

DIAGRAM_GEOID = 141668     # Ebringen 79285 (DE), 48N 8E
DIAGRAM_VAR = 4             # Niederschlag
DIAGRAM_STATION_LABEL = "Ebringen"
DIAGRAM_URL = (
    "https://wetterzentrale.de/de/show_diagrams.php"
    "?geoid={geoid}&model={model}&var={var}&run={run:02d}&lid={lid}&bw=1"
)

# (Modellcode für show_diagrams.php, Anzeigename)
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


# ---------------------------------------------------------------------------
# Teil 1: Gesamtkarten
# ---------------------------------------------------------------------------

def build_map_url(model: str, lid: str, run_dt: datetime) -> str:
    return MAP_URL.format(model=model, lid=lid, run=run_dt.hour, time=FORECAST_HOUR, var=VAR_PRECIP)


def image_exists(url: str) -> bool:
    """Prüft, ob unter der URL tatsächlich ein Bild liegt (statt einer Fehlerseite)."""
    try:
        r = requests.get(url, timeout=20, stream=True)
        ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image")
        r.close()
        return ok
    except requests.RequestException:
        return False


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
# Teil 2: Ensemble-Diagramm (Screenshot via Playwright)
# ---------------------------------------------------------------------------

def build_diagram_caption(display_name: str, lid: str, run_dt: datetime) -> str:
    kind = "Ensemble (alle Berechnungen inkl. Mittel/AVG)" if lid == "ENS" else "operationeller Lauf"
    return (
        f"{display_name} – {DIAGRAM_STATION_LABEL} – {kind}\n"
        f"Lauf {run_dt.strftime('%d.%m.%Y %HZ')} · Niederschlag"
    )


COOKIE_ACCEPT_SELECTORS = [
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Zustimmen')",
    "button:has-text('Einverstanden')",
    "#cmpwelcomebtnyes",
    ".cmpboxbtnyes",
    "[aria-label='Accept all']",
]


def dismiss_cookie_banner(page):
    """Best-effort: schließt ein Cookie-/Consent-Banner, falls eins da ist."""
    for selector in COOKIE_ACCEPT_SELECTORS:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=1500)
                page.wait_for_timeout(500)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def screenshot_diagram(browser, model: str, run_dt: datetime, lid: str, out_path: str) -> bool:
    url = DIAGRAM_URL.format(geoid=DIAGRAM_GEOID, model=model, var=DIAGRAM_VAR, run=run_dt.hour, lid=lid)
    page = browser.new_page(viewport={"width": 1200, "height": 1200})
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        dismiss_cookie_banner(page)
        page.wait_for_timeout(4000)  # Chart-Rendering abwarten
        page.screenshot(path=out_path, full_page=True)
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        print(f"Diagramm-Screenshot {model}/{lid}: {size} Bytes ({url})")
        return size > 15_000
    except Exception as exc:  # noqa: BLE001
        print(f"Diagramm-Fehler ({model}, {lid}, {url}): {exc}")
        return False
    finally:
        page.close()


def collect_diagrams(now_utc):
    items, missing = [], []
    run_dt = latest_run_slot(now_utc)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for model_code, display_name in DIAGRAM_MODELS:
            out_path = f"diagram_{model_code}.png"
            used_lid = None
            for lid in ("ENS", "OP"):
                if screenshot_diagram(browser, model_code, run_dt, lid, out_path):
                    used_lid = lid
                    break
            if used_lid:
                items.append((out_path, build_diagram_caption(display_name, used_lid, run_dt)))
                print(f"OK Diagramm: {display_name} ({used_lid})")
            else:
                missing.append(display_name)
                print(f"WARNUNG: Kein Diagramm für {display_name} verfügbar.")
        browser.close()
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


def send_media_group_files(items):
    media, files = [], {}
    for i, (path, caption) in enumerate(items):
        key = f"photo{i}"
        files[key] = open(path, "rb")
        media.append({"type": "photo", "media": f"attach://{key}", "caption": caption})
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    try:
        return requests.post(
            api_url, data={"chat_id": TELEGRAM_CHAT_ID, "media": json.dumps(media)}, files=files, timeout=60
        )
    finally:
        for f in files.values():
            f.close()


def send_single_photo_file(path, caption):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        return requests.post(
            api_url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f}, timeout=60
        )


def send_text(text):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    return requests.post(api_url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30)


def send_group(items, url_based: bool, label: str) -> bool:
    """Verschickt eine Gruppe von Items, meldet Erfolg/Misserfolg zurück."""
    if not items:
        return False
    if len(items) == 1:
        resp = send_single_photo_url(*items[0]) if url_based else send_single_photo_file(*items[0])
    else:
        resp = send_media_group_urls(items) if url_based else send_media_group_files(items)
    print(f"Telegram-Antwort ({label}):", resp.status_code, resp.text[:500])
    return resp.status_code == 200


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("FEHLER: TELEGRAM_BOT_TOKEN und/oder TELEGRAM_CHAT_ID sind nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)

    map_items, map_missing = collect_maps(now_utc)
    diagram_items, diagram_missing = collect_diagrams(now_utc)

    maps_ok = send_group(map_items, url_based=True, label="Karten")
    diagrams_ok = send_group(diagram_items, url_based=False, label="Diagramme")

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
