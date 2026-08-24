#!/usr/bin/env python3
"""
Schickt 3x täglich (per GitHub Actions Cron) aktuelle Niederschlags-Informationen
von wetterzentrale.de für ECMWF, GFS, ICON und AIFS an einen Telegram-Chat.

Lauf-Auswahl: Es wird IMMER der 18Z-Lauf verwendet (TARGET_RUN_HOUR) - der
  heutige, sobald wetterzentrale.de ihn bereitgestellt hat, bis dahin der von
  gestern. Das heißt: bei den früheren der 3 täglichen Sendungen (z.B. 06/12
  Uhr UTC) zeigt der Bot noch den 18Z-Lauf von GESTERN, ab dem Abend dann den
  heutigen. Damit ist immer klar, welcher Lauf gemeint ist, statt bei jeder
  Sendung einen anderen der 4 Tagesläufe zu zeigen.

Teil 1 – Gesamtkarten (statische Bilder), siehe MAP_PRODUCTS:
  a) Gesamt-Niederschlagssumme (var=18), Region Mitteleuropa (map=3),
     Vorhersagestunde so weit wie möglich (siehe time_candidates - probiert
     von der längsten Vorhersagestunde absteigend, welche das jeweilige
     Modell/Lauf tatsächlich anbietet, mit 144h als garantierter Rückfall).
     Für GFS wird das echte Ensemble-Mittel (Member "AVG") verwendet, da
     wetterzentrale.de das auf dieser Karte nur für GFS anbietet. Bei ECMWF,
     ICON und AIFS gibt es dort kein Ensemble-Mittel für diese Karte ->
     operationeller Lauf.
  b) 850 hPa Temperatur (var=2), Region Mitteleuropa, aktueller Lauf (time=0).

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
from email.utils import parsedate_to_datetime

import requests

# ---------------------------------------------------------------------------
# Konfiguration – Gesamtkarten (mehrere "Produkte" möglich)
# ---------------------------------------------------------------------------

MAP_REGION = 3        # Mitteleuropa
TARGET_RUN_HOUR = 18    # bevorzugter Lauf: immer der 18Z-Lauf (heutiger, sobald verfügbar)
MAX_DAY_FALLBACKS = 3    # wie viele Tage zurück probiert werden, falls der 18Z-Lauf noch fehlt

MAP_URL = "https://wetterzentrale.de/maps/{model}{lid}ME{run:02d}_{time}_{var}.png"

# Jedes Produkt: var + Liste möglicher Vorhersagestunden ("time_candidates", längste
# zuerst - das Skript nimmt die längste, die tatsächlich verfügbar ist) + welches
# Member je Modell bevorzugt wird ("AVG" = Ensemble-Mittel, "OP" = operationeller
# Lauf; bei einem Modell ohne AVG fällt das Skript automatisch auf OP zurück).
MAP_PRODUCTS = [
    {
        "key": "precip_total",
        "var": 18,     # Gesamt-Niederschlagssumme
        # so weit wie möglich, absteigend probiert (deckt GFS/AIFS/ICON-Langfrist
        # sowie ECMWF-Kurzläufe ab, die z.B. beim 18Z-Lauf oft nur bis 144h gehen)
        "time_candidates": [384, 360, 240, 180, 168, 144],
        "label_tpl": "Gesamt-Niederschlag bis +{time}h · Mitteleuropa",
        "models": [
            ("ECM", "ECMWF", "OP"),
            ("GFS", "GFS", "AVG"),
            ("ICO", "ICON", "OP"),
            ("AIFS", "AIFS", "OP"),
        ],
    },
    {
        "key": "temp850",
        "var": 2,      # 850 hPa Temperatur
        "time_candidates": [0],  # aktueller Lauf (Analyse, kein Forecast-Offset)
        "label_tpl": "850 hPa Temperatur (aktueller Lauf) · Mitteleuropa",
        "models": [
            ("ECM", "ECMWF", "OP"),
            ("GFS", "GFS", "OP"),
            ("ICO", "ICON", "OP"),
            ("AIFS", "AIFS", "OP"),
        ],
    },
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


def latest_target_run_slot(now_utc: datetime, target_hour: int = TARGET_RUN_HOUR) -> datetime:
    """
    Liefert den letzten TARGET_RUN_HOUR-Lauf (Standard: 18Z) - den heutigen, falls
    der Zeitpunkt schon erreicht ist, sonst den von gestern. Ein Aufruf um 08:00 UTC
    liefert also den 18Z-Lauf von GESTERN (heutiger 18Z existiert ja noch nicht);
    ein Aufruf um 20:00 UTC liefert (sobald von wetterzentrale.de bereitgestellt)
    den heutigen 18Z-Lauf.
    """
    candidate = now_utc.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if candidate > now_utc:
        candidate -= timedelta(days=1)
    return candidate


MAX_STALENESS_HOURS = 33  # Datei muss innerhalb dieses Fensters ab JETZT aktualisiert worden sein

# Wird an jede Bild-URL angehängt: erzwingt, dass wir (und Telegram beim Abholen
# der URL) nicht an einer von einem CDN/Proxy zwischengespeicherten alten Version
# der immer gleichen, datumslosen Bild-URL hängen bleiben. Grund für den Fix:
# Die HTML-Übersichtsseite (topkarten.php) meldete für die 850hPa-Temperaturkarte
# bereits den korrekten, aktuellen Lauf - das direkt abgerufene PNG unter derselben
# Pfad-URL zeigte trotzdem tagealte Daten. Das deutet auf eine zwischengespeicherte
# Kopie hin, nicht auf ein falsch berechnetes Datum.
NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}


def _bust_cache(url: str, now_utc: datetime) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_cb={int(now_utc.timestamp())}"


def image_exists(url: str, now_utc: datetime = None) -> bool:
    """
    Prüft, ob unter der URL tatsächlich ein Bild liegt (statt einer Fehlerseite).

    WICHTIG: Die Kartei-Bilder (MAP_URL) enthalten KEIN Datum im Dateinamen, nur
    die Laufzeit (z.B. "...ME12_0_2.png") - dieselbe Datei wird jeden Tag am
    selben Pfad überschrieben. Ein reiner Status-200-Check reicht deshalb nicht:
    falls wetterzentrale.de die Datei mal nicht neu erzeugt hat (oder ein
    zwischengeschalteter Cache eine alte Kopie ausliefert), sähe die alte Datei
    von vor Tagen identisch "gültig" aus. Deshalb zwei Schutzschichten:

    1. Cache-Busting: eindeutiger Query-Parameter + No-Cache-Header bei jedem
       Abruf, damit eine evtl. zwischengespeicherte alte Kopie umgangen wird.
    2. Last-Modified-Check gegen die TATSÄCHLICHE aktuelle Zeit (now_utc), NICHT
       gegen den gerade getesteten Kandidaten-Lauf - sonst würde die Rückwärts-
       suche einfach so lange zurücklaufen, bis eine uralte Datei "zufällig"
       wieder zu einem alten Kandidaten-Lauf passt.
    """
    try:
        r = requests.get(url, headers=NO_CACHE_HEADERS, timeout=20, stream=True)
        ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image")
        if now_utc is not None:
            last_mod_raw = r.headers.get("Last-Modified")
            print(f"  [Last-Modified-Check] status={r.status_code} Last-Modified='{last_mod_raw}' -> {url}")
            if ok and last_mod_raw:
                try:
                    lm_dt = parsedate_to_datetime(last_mod_raw)
                    if lm_dt.tzinfo is None:
                        lm_dt = lm_dt.replace(tzinfo=timezone.utc)
                    age = now_utc - lm_dt
                    if age > timedelta(hours=MAX_STALENESS_HOURS):
                        print(
                            f"  (verworfen, veraltet: Last-Modified {lm_dt.isoformat()} "
                            f"ist {age} alt (> {MAX_STALENESS_HOURS}h) -> {url})"
                        )
                        ok = False
                except Exception as exc:  # noqa: BLE001
                    print(f"  (Last-Modified '{last_mod_raw}' nicht parsebar: {exc})")
        r.close()
        return ok
    except requests.RequestException:
        return False


# ---------------------------------------------------------------------------
# Teil 1: Gesamtkarten
# ---------------------------------------------------------------------------

def build_map_url(model: str, lid: str, run_dt: datetime, time_hour: int, var: int, now_utc: datetime) -> str:
    base = MAP_URL.format(model=model, lid=lid, run=run_dt.hour, time=time_hour, var=var)
    return _bust_cache(base, now_utc)


def find_latest_map(model: str, preferred_lid: str, now_utc: datetime, time_candidates, var: int):
    """
    Sucht den TARGET_RUN_HOUR-Lauf (Standard 18Z) - den heutigen, sonst tageweise
    rückwärts bis zu MAX_DAY_FALLBACKS Tage. Für jeden Lauf: erst das bevorzugte
    Member (z.B. AVG), sonst "OP"; für jedes Member werden die time_candidates
    von der LÄNGSTEN Vorhersagestunde absteigend probiert - "so weit wie möglich".
    """
    run_dt = latest_target_run_slot(now_utc)
    for _ in range(MAX_DAY_FALLBACKS):
        for lid in dict.fromkeys([preferred_lid, "OP"]):  # preferred zuerst, Duplikate raus
            for time_hour in time_candidates:
                url = build_map_url(model, lid, run_dt, time_hour, var, now_utc)
                if image_exists(url, now_utc=now_utc):
                    return url, lid, run_dt, time_hour
        run_dt -= timedelta(days=1)
    return None, None, None, None


def build_map_caption(display_name: str, lid: str, run_dt: datetime, label: str) -> str:
    kind = "Ensemble-Mittel (AVG)" if lid == "AVG" else "operationeller Lauf"
    return f"{display_name} – {kind}\n" f"Lauf {run_dt.strftime('%d.%m.%Y %HZ')} · {label}"


def collect_maps(product: dict, now_utc):
    items, missing = [], []
    for model_code, display_name, preferred_lid in product["models"]:
        url, lid, run_dt, time_hour = find_latest_map(
            model_code, preferred_lid, now_utc, product["time_candidates"], product["var"]
        )
        if url is None:
            missing.append(f"{display_name} ({product['key']})")
            print(f"WARNUNG: Keine aktuelle Karte für {display_name} ({product['key']}) gefunden.")
            continue
        label = product["label_tpl"].format(time=time_hour)
        items.append((url, build_map_caption(display_name, lid, run_dt, label)))
        print(f"OK Karte [{product['key']}]: {display_name} (+{time_hour}h) -> {url}")
    return items, missing


# ---------------------------------------------------------------------------
# Teil 2: Ensemble-Diagramm (statisches Bild von ens_image.php)
# ---------------------------------------------------------------------------

def build_diagram_url(model: str, member: str, run_dt: datetime, now_utc: datetime) -> str:
    base = DIAGRAM_URL.format(
        geoid=DIAGRAM_GEOID,
        var=DIAGRAM_VAR,
        run=run_dt.hour,
        date=run_dt.strftime("%Y-%m-%d"),
        model=model,
        member=member,
    )
    return _bust_cache(base, now_utc)


def find_latest_diagram(model: str, now_utc: datetime):
    """
    Sucht den TARGET_RUN_HOUR-Lauf (Standard 18Z) - den heutigen, sonst tageweise
    rückwärts. Versucht zuerst das Ensemble (member=ENS), fällt bei Fehlschlag auf
    den operationellen Lauf (member=OP) zurück. Die Diagramm-URL enthält bereits
    ein explizites Datum; Cache-Busting wird trotzdem angewendet, für den Fall,
    dass auch dieser Endpunkt hinter einem Cache liegt.
    """
    run_dt = latest_target_run_slot(now_utc)
    for _ in range(MAX_DAY_FALLBACKS):
        for member in ("ENS", "OP"):
            url = build_diagram_url(model, member, run_dt, now_utc)
            if image_exists(url):
                return url, member, run_dt
        run_dt -= timedelta(days=1)
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

    missing = []
    any_items = False
    any_sent = False

    for product in MAP_PRODUCTS:
        items, product_missing = collect_maps(product, now_utc)
        missing += product_missing
        any_items = any_items or bool(items)
        any_sent = send_group(items, label=f"Karten [{product['key']}]") or any_sent

    diagram_items, diagram_missing = collect_diagrams(now_utc)
    missing += diagram_missing
    any_items = any_items or bool(diagram_items)
    any_sent = send_group(diagram_items, label="Diagramme") or any_sent

    if missing:
        send_text(f"ℹ️ Hinweis: Für folgende Modelle/Produkte war gerade nichts Aktuelles verfügbar: {', '.join(missing)}")

    if not any_items:
        send_text("⚠️ Wetter-Update: Es konnte gerade nichts von wetterzentrale.de geladen werden.")
        sys.exit(1)

    if not any_sent:
        sys.exit(1)


if __name__ == "__main__":
    main()
