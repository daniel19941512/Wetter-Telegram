#!/usr/bin/env python3
"""
Schickt 2x täglich (06 und 16 UTC) eine aktuelle 2m-Temperaturkarte von
kachelmannwetter.com für die Region Freiburg (Modell "Mitteleuropa Super HD",
Slug "sui-hd") an den Telegram-Chat.

Vorgehen:
1. Baut die Seiten-URL für den aktuellen UTC-Tag + Zielstunde (06 oder 16,
   kommt über die Umgebungsvariable TARGET_HOUR_UTC vom Workflow rein, siehe
   .github/workflows/kachelmann-telegram.yml), z.B.
   https://kachelmannwetter.com/de/modellkarten/sui-hd/freiburg/temperatur/20260824-0600z.html
2. Lädt diese Seite (HTML) und liest daraus die tatsächliche Bild-URL der
   Karte aus (og:image-Meta-Tag bzw. Bild-Adresse im Quelltext) - so muss
   das interne Dateinamens-Schema von kachelmannwetter.de (Lauf +
   Vorhersagestunden-Kodierung) nicht nachgebaut werden. Es wird einfach
   genommen, was die Seite für den gewünschten Zeitpunkt selbst anzeigt.
3. Lädt das Bild selbst herunter und schickt es als Datei-Upload an Telegram
   (nicht nur die URL weiterreichen) - falls kachelmannwetter.de das direkte
   Verlinken der Bild-CDN (Hotlinking) per Referer-Prüfung blockiert,
   funktioniert der Download mit passenden Browser-Headern hier trotzdem,
   während ein reiner URL-Verweis an Telegram scheitern könnte.
4. Bei Fehlschlag (Seite/Bild nicht erreichbar): bis zu MAX_DAY_FALLBACKS
   Tage zurück probieren (gleiche Zielstunde), danach Hinweis-Nachricht statt
   Absturz.

Hinweis: kachelmannwetter.de ist ein werbe-/abofinanzierter Dienst ("Plus").
Falls die Seite den automatisierten Zugriff blockiert (z.B. Cloudflare-
Prüfung), meldet das Skript das im Log und schickt eine Telegram-Hinweis-
nachricht statt eines Bildes - kein Absturz, aber auch keine Umgehung von
Schutzmaßnahmen wird versucht.

Benötigte Umgebungsvariablen:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (wie beim wetterzentrale-Skript)
  TARGET_HOUR_UTC                       (6 oder 16 - wird vom Workflow gesetzt)
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

PAGE_URL_TPL = "https://kachelmannwetter.com/de/modellkarten/sui-hd/freiburg/temperatur/{date}-{hour:02d}00z.html"
MAX_DAY_FALLBACKS = 3

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

# Bild-URLs auf kachelmannwetter.de sehen z.B. so aus:
# https://img6.kachelmannwetter.com/images/data/cache/model/complete_model_modsuihd_2026082418_4_252_1.png
IMG_RE = re.compile(r'https://img\d*\.kachelmannwetter\.com/images/data/cache/model/[^"\'<>\s]+\.(?:png|jpg|jpeg)')

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def build_page_url(target_dt: datetime) -> str:
    return PAGE_URL_TPL.format(date=target_dt.strftime("%Y%m%d"), hour=target_dt.hour)


def find_map_image_url(target_dt: datetime):
    page_url = build_page_url(target_dt)
    try:
        r = requests.get(page_url, headers=BROWSER_HEADERS, timeout=20)
    except requests.RequestException as exc:
        print(f"  Fehler beim Laden der Seite {page_url}: {exc}")
        return None, page_url
    if r.status_code != 200:
        print(f"  Seite nicht verfügbar (status={r.status_code}): {page_url}")
        return None, page_url
    m = IMG_RE.search(r.text)
    if not m:
        print(f"  Kein Kartenbild im Seitenquelltext gefunden: {page_url}")
        return None, page_url
    return m.group(0), page_url


def download_image(img_url: str):
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = "https://kachelmannwetter.com/"
    try:
        r = requests.get(img_url, headers=headers, timeout=20)
    except requests.RequestException as exc:
        print(f"  Fehler beim Bild-Download ({img_url}): {exc}")
        return None
    content_type = r.headers.get("Content-Type", "")
    if r.status_code != 200 or not content_type.startswith("image"):
        print(
            f"  Bild-Download fehlgeschlagen (status={r.status_code}, "
            f"content-type='{content_type}'): {img_url}"
        )
        return None
    return r.content


def find_latest_map(now_utc: datetime, target_hour: int):
    """Probiert die Zielstunde für heute, dann tageweise rückwärts (MAX_DAY_FALLBACKS)."""
    target_dt = now_utc.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    for _ in range(MAX_DAY_FALLBACKS):
        img_url, page_url = find_map_image_url(target_dt)
        if img_url:
            img_bytes = download_image(img_url)
            if img_bytes:
                print(f"OK: Karte gefunden für {target_dt.isoformat()} -> {img_url}")
                return img_bytes, target_dt, page_url
        target_dt -= timedelta(days=1)
    return None, None, None


def send_photo_bytes(img_bytes: bytes, caption: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": ("karte.png", img_bytes)}
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
    return requests.post(api_url, data=data, files=files, timeout=30)


def send_text(text: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    return requests.post(api_url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30)


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("FEHLER: TELEGRAM_BOT_TOKEN und/oder TELEGRAM_CHAT_ID sind nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    try:
        target_hour = int(os.environ.get("TARGET_HOUR_UTC", "6"))
    except ValueError:
        target_hour = 6
    if target_hour not in (6, 16):
        print(f"WARNUNG: unerwartete TARGET_HOUR_UTC='{target_hour}', nutze 6.")
        target_hour = 6

    now_utc = datetime.now(timezone.utc)
    img_bytes, target_dt, page_url = find_latest_map(now_utc, target_hour)

    if img_bytes is None:
        print("WARNUNG: Keine Kachelmannwetter-Karte verfügbar (Seite blockiert oder kein Bild gefunden).")
        send_text(
            "ℹ️ Kachelmannwetter-Temperaturkarte (Freiburg, "
            f"{target_hour:02d}Z) war gerade nicht verfügbar."
        )
        sys.exit(1)

    caption = (
        "Freiburg – 2m-Temperatur (Kachelmannwetter, Mitteleuropa Super HD)\n"
        f"Stand: {target_dt.strftime('%d.%m.%Y %HZ')} · {page_url}"
    )
    resp = send_photo_bytes(img_bytes, caption)
    print("Telegram-Antwort:", resp.status_code, resp.text[:300])
    if resp.status_code != 200:
        sys.exit(1)


if __name__ == "__main__":
    main()
