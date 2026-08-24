#!/usr/bin/env python3
"""
Zusatzmodul zu send_weather.py - wird von dort importiert und läuft im selben
06/12/18-UTC-Rhythmus mit. Baut eine zusätzliche Text-Nachricht für Ebringen
bei Freiburg mit:

  1. Amtlichen DWD-Unwetterwarnungen (über Bright Sky, einen offenen,
     kostenlosen Wrapper um die DWD-CAP-Warndaten: https://brightsky.dev -
     kein API-Key nötig, kein Bot-Schutz).
  2. Einem Zahlen-Modellvergleich (Niederschlag/Temp. heute+morgen je Modell)
     über die kostenlose Open-Meteo-API (https://open-meteo.com, kein Key
     nötig). wetterzentrale.de liefert nur Bilder, keine Zahlen - für einen
     Vergleich/Schwellenwert-Check werden echte Zahlen gebraucht, deshalb
     diese zweite, unabhängige Datenquelle.
  3. Einem einfachen Schwellenwert-Hinweis (⚠️), falls mind. ein Modell
     Starkregen oder starke Böen für heute/morgen zeigt.
  4. Einer "Modell-Trefferquote": wie weit lagen die Modelle in der
     Vergangenheit (Vorhersage vor VERIFY_LEAD_DAYS Tagen) tatsächlich vom
     eingetroffenen Wetter entfernt, gemittelt über die letzten
     VERIFY_WINDOW_DAYS auswertbaren Tage. Nutzt Open-Meteos "Previous Runs
     API" (archivierte, tatsächlich zum Zeitpunkt X gemachte Vorhersagen)
     gegen die "Historical Weather API" (ERA5-Reanalyse als Ground Truth,
     hat ca. 5 Tage Verzögerung - deshalb VERIFY_LATENCY_DAYS als
     Sicherheitsabstand).

WICHTIG - noch nicht live getestet: Ich konnte aus der Sandbox heraus keine
echten Testanfragen an Open-Meteo/Bright Sky schicken (Netzwerk dort
blockiert), die URLs/Parameter/Feldnamen stammen aus deren Dokumentation.
Alle Abschnitte sind einzeln fehlertolerant (try/except, fehlende Felder
werden übersprungen statt das Skript abstürzen zu lassen) - falls nach dem
ersten echten Lauf ein Abschnitt fehlt, bitte das GitHub-Actions-Log
schicken, dann lässt sich das anhand der [Diagnose]-Zeilen gezielt fixen.
"""

from datetime import date, timedelta

import requests

EBRINGEN_LAT = 47.94
EBRINGEN_LON = 7.79

# Open-Meteo Modell-Kennungen (siehe https://open-meteo.com/en/docs)
OPEN_METEO_MODELS = {
    "ECMWF": "ecmwf_ifs025",
    "GFS": "gfs_seamless",
    "ICON": "icon_seamless",
    "AIFS": "ecmwf_aifs025",
}

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DWD_ALERTS_URL = "https://api.brightsky.dev/alerts"

REQUEST_TIMEOUT = 20

# Schwellenwerte für den ⚠️-Hinweis (heute/morgen, höchster Wert über alle Modelle)
ALERT_PRECIP_MM = 15.0
ALERT_GUST_KMH = 70.0

# Trefferquote-Konfiguration
# Bewusst konservativ (kleine Werte): der erste Live-Test zeigte "Keine
# auswertbaren Tage" für alle Modelle - vermutlich unterstützt Open-Meteos
# Previous-Runs-API nicht so viele "previous_dayN"-Spalten zurück, wie die
# ursprüngliche Kombination (Latenz 6 + Fenster 5 + Vorlauf 2 = bis zu
# previous_day12) gebraucht hätte. Mit kleineren Werten (max. previous_day7)
# steigt die Chance, dass die Spalten wirklich existieren. Zusätzlich gibt
# es jetzt eine Diagnose-Ausgabe der tatsächlich vorhandenen Spalten, falls
# es wieder leer bleibt - dann sieht man im Log genau, wie tief die API geht.
VERIFY_LEAD_DAYS = 1        # "wie gut war die Vorhersage X Tage vorher"
VERIFY_WINDOW_DAYS = 2      # über wie viele Tage gemittelt wird
VERIFY_LATENCY_DAYS = 5     # Sicherheitsabstand zur ERA5-Verzögerung (~5 Tage)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _find_key(daily: dict, var: str, model_id: str):
    """Sucht die Spalte für (Variable, Modell) im 'daily'-Objekt der
    Open-Meteo-Forecast-Antwort, ohne die exakte Reihenfolge der
    Namens-Bestandteile vorauszusetzen."""
    for cand in (f"{var}_{model_id}", f"{model_id}_{var}"):
        if cand in daily:
            return cand
    for k in daily:
        if k.startswith(var) and model_id in k:
            return k
    return None


def _find_prev_run_key(daily: dict, var: str, model_id: str, day_n: int):
    """Wie _find_key, aber zusätzlich für eine bestimmte 'previous_dayN'-Spalte
    der Previous-Runs-API (archivierter Modelllauf von vor N Tagen)."""
    marker = f"previous_day{day_n}"
    for k in daily:
        if k.startswith(var) and model_id in k and marker in k:
            return k
    return None


# ---------------------------------------------------------------------------
# 1. DWD-Unwetterwarnungen
# ---------------------------------------------------------------------------

def fetch_dwd_warning_text():
    try:
        r = requests.get(
            DWD_ALERTS_URL, params={"lat": EBRINGEN_LAT, "lon": EBRINGEN_LON}, timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [Diagnose/DWD-Warnungen] Anfrage fehlgeschlagen: {exc}")
        return None

    alerts = data.get("alerts", [])
    if not alerts:
        return None  # keine aktive Warnung -> keine Zeile in der Zusammenfassung

    lines = ["🚨 Amtliche DWD-Warnung(en) für Ebringen:"]
    for a in alerts:
        severity = a.get("severity", "?")
        event = a.get("event_de") or a.get("event_en") or "?"
        headline = a.get("headline_de", "")
        expires = a.get("expires", "")
        lines.append(f"- {event} ({severity}): {headline} (bis {expires})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Modellvergleich + 3. Schwellenwert-Hinweis
# ---------------------------------------------------------------------------

def fetch_comparison_data():
    """
    Eine gemeinsame Open-Meteo-Anfrage für den Modellvergleich (Abschnitt 2)
    UND die gemittelte Tagesübersicht (Abschnitt 2b, siehe weiter unten) - spart
    einen zweiten Request. 'daily' liefert die Tageswerte direkt je Modell,
    'hourly' wird für Taupunkt und CAPE gebraucht, da es dafür keine fertigen
    Tageswerte in der Open-Meteo-API gibt - die werden weiter unten selbst aus
    den Stundenwerten pro Tag gemittelt.
    """
    params = {
        "latitude": EBRINGEN_LAT,
        "longitude": EBRINGEN_LON,
        "daily": (
            "precipitation_sum,temperature_2m_max,temperature_2m_min,"
            "wind_gusts_10m_max,sunshine_duration"
        ),
        "hourly": "dew_point_2m,cape",
        "models": ",".join(OPEN_METEO_MODELS.values()),
        "timezone": "Europe/Berlin",
        "forecast_days": 3,
    }
    try:
        r = requests.get(FORECAST_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [Diagnose/Modellvergleich] Open-Meteo-Anfrage fehlgeschlagen: {exc}")
        return None


def build_comparison_and_alert(data: dict):
    """Gibt (vergleichs_text, alarm_text) zurück, beide ggf. None."""
    if not data or "daily" not in data:
        return None, None
    daily = data["daily"]
    times = daily.get("time", [])
    if len(times) < 2:
        return None, None

    rows_precip, rows_temp = [], []
    max_precip = 0.0
    max_gust = 0.0
    any_model_found = False

    for display_name, model_id in OPEN_METEO_MODELS.items():
        k_precip = _find_key(daily, "precipitation_sum", model_id)
        k_tmax = _find_key(daily, "temperature_2m_max", model_id)
        k_gust = _find_key(daily, "wind_gusts_10m_max", model_id)
        if not (k_precip or k_tmax):
            print(f"  [Diagnose/Modellvergleich] Keine Spalten für {display_name} gefunden.")
            continue
        any_model_found = True

        precip_vals = (daily.get(k_precip) or [None, None])[:2]
        tmax_vals = (daily.get(k_tmax) or [None, None])[:2]
        gust_vals = (daily.get(k_gust) or [None, None])[:2] if k_gust else [None, None]

        def fmt(v):
            return "?" if not isinstance(v, (int, float)) else f"{v:.0f}"

        rows_precip.append(f"{display_name}: {fmt(precip_vals[0])}/{fmt(precip_vals[1])}mm")
        rows_temp.append(f"{display_name}: {fmt(tmax_vals[0])}/{fmt(tmax_vals[1])}°C")

        for v in precip_vals:
            if isinstance(v, (int, float)):
                max_precip = max(max_precip, v)
        for v in gust_vals:
            if isinstance(v, (int, float)):
                max_gust = max(max_gust, v)

    if not any_model_found:
        return None, None

    comparison_text = (
        "📊 Modellvergleich Ebringen (heute/morgen)\n"
        f"Niederschlag: {', '.join(rows_precip)}\n"
        f"Max. Temp.: {', '.join(rows_temp)}"
    )

    alert_lines = []
    if max_precip >= ALERT_PRECIP_MM:
        alert_lines.append(f"⚠️ Mind. ein Modell erwartet ≥{ALERT_PRECIP_MM:.0f}mm Niederschlag (heute/morgen).")
    if max_gust >= ALERT_GUST_KMH:
        alert_lines.append(f"⚠️ Mind. ein Modell erwartet Böen ≥{ALERT_GUST_KMH:.0f}km/h (heute/morgen).")
    alert_text = "\n".join(alert_lines) if alert_lines else None

    return comparison_text, alert_text


# ---------------------------------------------------------------------------
# 2b. Gemittelte Tagesübersicht (heute + morgen, Mittel über alle Modelle)
# ---------------------------------------------------------------------------

DAY_LABELS = ["Heute", "Morgen"]


def _day_hour_indices(hourly_times, day_date: str):
    """Indizes aller Stundenwerte, die zu einem bestimmten Kalendertag gehören
    (hourly_times sind ISO-Zeitstempel wie '2026-08-25T14:00')."""
    return [i for i, t in enumerate(hourly_times) if t.startswith(day_date)]


def _avg(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def build_daily_overview_text(data: dict):
    """
    Baut eine zusätzliche, kompakte Übersicht für heute + morgen - jeweils
    über alle 4 Modelle GEMITTELT (nicht je Modell einzeln wie beim
    Modellvergleich oben): Min/Max-Temperatur, Niederschlag, Sonnenscheindauer,
    Taupunkt, Windböen, CAPE. Taupunkt und CAPE gibt es bei Open-Meteo nur als
    Stundenwerte (nicht als fertigen Tageswert) - werden hier je Modell über
    den Tag gemittelt und dann über die Modelle gemittelt.
    """
    if not data or "daily" not in data or "hourly" not in data:
        return None
    daily = data["daily"]
    hourly = data["hourly"]
    daily_times = daily.get("time", [])
    hourly_times = hourly.get("time", [])
    if len(daily_times) < 2 or not hourly_times:
        return None

    lines = ["🌤️ Tagesübersicht Ebringen (Mittel aller Modelle)"]
    any_day_found = False

    for day_idx in range(min(2, len(daily_times))):
        day_date = daily_times[day_idx]
        tmax_vals, tmin_vals, precip_vals, sun_vals, gust_vals = [], [], [], [], []
        dewpoint_vals, cape_vals = [], []
        day_hour_idx = _day_hour_indices(hourly_times, day_date)

        for model_id in OPEN_METEO_MODELS.values():
            k_tmax = _find_key(daily, "temperature_2m_max", model_id)
            k_tmin = _find_key(daily, "temperature_2m_min", model_id)
            k_precip = _find_key(daily, "precipitation_sum", model_id)
            k_sun = _find_key(daily, "sunshine_duration", model_id)
            k_gust = _find_key(daily, "wind_gusts_10m_max", model_id)
            k_dew = _find_key(hourly, "dew_point_2m", model_id)
            k_cape = _find_key(hourly, "cape", model_id)

            if k_tmax and daily.get(k_tmax) and isinstance(daily[k_tmax][day_idx], (int, float)):
                tmax_vals.append(daily[k_tmax][day_idx])
            if k_tmin and daily.get(k_tmin) and isinstance(daily[k_tmin][day_idx], (int, float)):
                tmin_vals.append(daily[k_tmin][day_idx])
            if k_precip and daily.get(k_precip) and isinstance(daily[k_precip][day_idx], (int, float)):
                precip_vals.append(daily[k_precip][day_idx])
            if k_sun and daily.get(k_sun) and isinstance(daily[k_sun][day_idx], (int, float)):
                sun_vals.append(daily[k_sun][day_idx])
            if k_gust and daily.get(k_gust) and isinstance(daily[k_gust][day_idx], (int, float)):
                gust_vals.append(daily[k_gust][day_idx])

            if k_dew:
                dew_avg = _avg(hourly[k_dew][i] for i in day_hour_idx)
                if dew_avg is not None:
                    dewpoint_vals.append(dew_avg)
            if k_cape:
                cape_avg = _avg(hourly[k_cape][i] for i in day_hour_idx)
                if cape_avg is not None:
                    cape_vals.append(cape_avg)

        tmax, tmin = _avg(tmax_vals), _avg(tmin_vals)
        precip, gust = _avg(precip_vals), _avg(gust_vals)
        sun_h = _avg(sun_vals) / 3600 if _avg(sun_vals) is not None else None
        dew, cape = _avg(dewpoint_vals), _avg(cape_vals)

        if all(v is None for v in (tmax, tmin, precip, sun_h, gust, dew, cape)):
            print(f"  [Diagnose/Tagesübersicht] Keine Werte für Tag {day_date} gefunden.")
            continue
        any_day_found = True

        label = DAY_LABELS[day_idx] if day_idx < len(DAY_LABELS) else day_date
        lines.append(f"\n📅 {label}")
        if tmin is not None and tmax is not None:
            lines.append(f"🌡️ Min/Max: {tmin:.0f}°C / {tmax:.0f}°C")
        if precip is not None:
            lines.append(f"☔ Niederschlag: {precip:.0f}mm")
        if sun_h is not None:
            lines.append(f"☀️ Sonnenschein: {sun_h:.1f}h")
        if dew is not None:
            lines.append(f"💧 Taupunkt (Ø): {dew:.0f}°C")
        if gust is not None:
            lines.append(f"💨 Böen (max.): {gust:.0f}km/h")
        if cape is not None:
            lines.append(f"⛈️ CAPE (Ø): {cape:.0f} J/kg")

    if not any_day_found:
        return None
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Modell-Trefferquote
# ---------------------------------------------------------------------------

def fetch_verification_text():
    today = date.today()
    target_dates = [today - timedelta(days=VERIFY_LATENCY_DAYS + i) for i in range(VERIFY_WINDOW_DAYS)]
    max_n = max((today - d).days + VERIFY_LEAD_DAYS for d in target_dates)

    params_prev = {
        "latitude": EBRINGEN_LAT,
        "longitude": EBRINGEN_LON,
        "daily": "precipitation_sum,temperature_2m_max",
        "models": ",".join(OPEN_METEO_MODELS.values()),
        "past_days": max_n,
        "forecast_days": 1,
        "timezone": "Europe/Berlin",
    }
    params_arch = {
        "latitude": EBRINGEN_LAT,
        "longitude": EBRINGEN_LON,
        "daily": "precipitation_sum,temperature_2m_max",
        "start_date": min(target_dates).isoformat(),
        "end_date": max(target_dates).isoformat(),
        "timezone": "Europe/Berlin",
    }

    try:
        r_prev = requests.get(PREVIOUS_RUNS_URL, params=params_prev, timeout=REQUEST_TIMEOUT)
        r_prev.raise_for_status()
        prev = r_prev.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [Diagnose/Trefferquote] Previous-Runs-Anfrage fehlgeschlagen: {exc}")
        return None
    try:
        r_arch = requests.get(ARCHIVE_URL, params=params_arch, timeout=REQUEST_TIMEOUT)
        r_arch.raise_for_status()
        arch = r_arch.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [Diagnose/Trefferquote] Archiv-Anfrage fehlgeschlagen: {exc}")
        return None

    prev_daily = prev.get("daily", {})
    prev_times = prev_daily.get("time", [])
    arch_daily = arch.get("daily", {})
    arch_times = arch_daily.get("time", [])
    print(
        f"  [Diagnose/Trefferquote] angefragt: past_days={max_n}, Ziel-Daten={[d.isoformat() for d in target_dates]}, "
        f"prev_times={prev_times[:1]}..{prev_times[-1:]} ({len(prev_times)}), "
        f"arch_times={arch_times[:1]}..{arch_times[-1:]} ({len(arch_times)})"
    )
    if not prev_times or not arch_times:
        print("  [Diagnose/Trefferquote] Keine Zeitreihe in der Antwort - übersprungen.")
        return None

    lines = []
    for display_name, model_id in OPEN_METEO_MODELS.items():
        temp_errors, precip_errors = [], []
        missing_key_logged = False
        for D in target_dates:
            n = (today - D).days + VERIFY_LEAD_DAYS
            d_str = D.isoformat()
            if d_str not in prev_times or d_str not in arch_times:
                continue
            idx_prev = prev_times.index(d_str)
            idx_arch = arch_times.index(d_str)

            k_tmax = _find_prev_run_key(prev_daily, "temperature_2m_max", model_id, n)
            if k_tmax:
                fc_temp = prev_daily[k_tmax][idx_prev]
                actual_temp = (arch_daily.get("temperature_2m_max") or [None] * len(arch_times))[idx_arch]
                if isinstance(fc_temp, (int, float)) and isinstance(actual_temp, (int, float)):
                    temp_errors.append(abs(fc_temp - actual_temp))
            elif not missing_key_logged:
                model_keys = sorted(k for k in prev_daily if model_id in k)
                print(f"  [Diagnose/Trefferquote] Keine Spalte 'previous_day{n}' für {display_name} - vorhandene Spalten: {model_keys}")
                missing_key_logged = True

            k_precip = _find_prev_run_key(prev_daily, "precipitation_sum", model_id, n)
            if k_precip:
                fc_precip = prev_daily[k_precip][idx_prev]
                actual_precip = (arch_daily.get("precipitation_sum") or [None] * len(arch_times))[idx_arch]
                if isinstance(fc_precip, (int, float)) and isinstance(actual_precip, (int, float)):
                    precip_errors.append(abs(fc_precip - actual_precip))

        if not temp_errors and not precip_errors:
            print(f"  [Diagnose/Trefferquote] Keine auswertbaren Tage für {display_name} gefunden.")
            continue

        parts = []
        if temp_errors:
            parts.append(f"Ø {sum(temp_errors)/len(temp_errors):.1f}°C daneben")
        if precip_errors:
            parts.append(f"Ø {sum(precip_errors)/len(precip_errors):.1f}mm daneben")
        n_used = max(len(temp_errors), len(precip_errors))
        lines.append(f"{display_name}: {' · '.join(parts)} (n={n_used}/{VERIFY_WINDOW_DAYS})")

    if not lines:
        print("  [Diagnose/Trefferquote] Keine auswertbaren Modelle - übersprungen.")
        return None

    return (
        f"🎯 Modell-Trefferquote ({VERIFY_LEAD_DAYS}-Tage-Vorhersage vs. tatsächliches "
        f"Wetter, Ø letzte {VERIFY_WINDOW_DAYS} auswertbare Tage)\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# 5. DWD-Regenradar (aktuelles Komposit-Bild, kein var-Schema wie bei
#    wetterzentrale.de, sondern ein einzelnes fertiges Bild für ganz
#    Deutschland - eigener Fetch, kein Modellvergleich).
# ---------------------------------------------------------------------------

DWD_RADAR_URL = "https://www.dwd.de/DWD/wetter/radar/rad_brd_akt.jpg"
RADAR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def fetch_dwd_radar_bytes(cache_buster: int):
    """Lädt das aktuelle DWD-Regenradar-Kompositbild (ganz Deutschland).
    cache_buster: eindeutiger Wert (z.B. Unix-Timestamp) gegen zwischen-
    gespeicherte alte Kopien, da der Dateiname fest ist ("_akt" = aktuell,
    wird täglich am selben Pfad überschrieben)."""
    url = f"{DWD_RADAR_URL}?_cb={cache_buster}"
    try:
        r = requests.get(url, headers=RADAR_HEADERS, timeout=20)
    except requests.RequestException as exc:
        print(f"  [Diagnose/Radar] Fehler beim Laden: {exc}")
        return None
    content_type = r.headers.get("Content-Type", "")
    if r.status_code != 200 or not content_type.startswith("image"):
        print(f"  [Diagnose/Radar] nicht verfügbar (status={r.status_code}, content-type='{content_type}')")
        return None
    return r.content


# ---------------------------------------------------------------------------
# Gesamt-Zusammenbau
# ---------------------------------------------------------------------------

def build_summary_message():
    """Baut die komplette Text-Zusammenfassung aus allen Abschnitten, die
    gerade verfügbar sind. Gibt None zurück, falls gar nichts ermittelt
    werden konnte (dann verschickt send_weather.py keine Zusatznachricht)."""
    sections = []

    warning_text = fetch_dwd_warning_text()
    if warning_text:
        sections.append(warning_text)

    comparison_data = fetch_comparison_data()

    overview_text = build_daily_overview_text(comparison_data)
    if overview_text:
        sections.append(overview_text)

    comparison_text, alert_text = build_comparison_and_alert(comparison_data)
    if comparison_text:
        sections.append(comparison_text)
    if alert_text:
        sections.append(alert_text)

    verification_text = fetch_verification_text()
    if verification_text:
        sections.append(verification_text)

    if not sections:
        return None
    return "\n\n".join(sections)
