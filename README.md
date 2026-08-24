# Wetterzentrale → Telegram (3x täglich, per GitHub Actions)

Schickt automatisch 3x täglich drei Dinge von wetterzentrale.de an deinen Telegram-Chat,
jeweils für **ECMWF, GFS, ICON und AIFS**, jeweils als eigene Nachricht:

1. Die Niederschlags-Gesamtkarten (Mitteleuropa, so weit wie möglich in die Zukunft).
2. Die 850-hPa-Temperaturkarten (Mitteleuropa, aktueller Lauf).
3. Das Ensemble-Diagramm für die Station **Ebringen** ("850 hPa Temp. & Niederschlag",
   alle Ensemble-Member + Mittelwert/AVG + operationeller Lauf).

**Lauf-Auswahl:** Es wird immer der **18Z-Lauf** verwendet - der heutige, sobald
wetterzentrale.de ihn bereitgestellt hat (meist ab dem späten Abend), bis dahin
der von gestern. Das heißt: die früheren der 3 täglichen Sendungen (z.B. um 06/12
Uhr UTC) zeigen noch gestern Abends 18Z-Lauf, die Abend-Sendung dann den heutigen.
So ist immer klar, welcher Lauf gemeint ist, statt bei jeder Sendung zwischen den
4 Tagesläufen zu springen.

## Wichtiger Hinweis zum "Mittel aller Berechnungen"

Ich habe auf wetterzentrale.de geprüft, welche Modelle dort ein echtes
Ensemble-Mittel ("Member: AVG") für die Niederschlags-Karte anbieten:

- **GFS**: Ensemble-Mittel (AVG) ist verfügbar → wird verwendet.
- **ECMWF, ICON, AIFS**: Auf dieser Karte gibt es dort **kein** Ensemble-Mittel
  (Member-Auswahl zeigt nur "OP"/"PARA"). Für diese drei wird deshalb automatisch
  der reguläre (operationelle) Lauf verwendet.

Das Skript versucht bei jedem Lauf zuerst das bevorzugte Member und meldet im
Log bzw. per Telegram-Hinweis, falls für ein Modell gerade nichts verfügbar war.

Falls du das anders haben möchtest (z.B. immer nur den operationellen Lauf für
alle 4, oder andere Kartentypen/Vorhersagezeiträume), sag einfach Bescheid – das
lässt sich in `send_weather.py` oben in der Liste `MAP_PRODUCTS` leicht anpassen
oder erweitern (jeder Eintrag = eine eigene Telegram-Nachricht mit 4 Karten).

## Das Ensemble-Diagramm (Ebringen)

Die interaktive Diagrammseite (`show_diagrams.php`) zeichnet den reinen
Niederschlags-Chart per JavaScript – dafür gibt es kein direktes Bild zum Verlinken.
Die Kombi-Ansicht **"850 hPa Temp. & Niederschlag"** liefert wetterzentrale.de dagegen
über einen eigenen Bild-Endpunkt (`ens_image.php`) als fertiges PNG aus – genau wie bei
den Karten in Teil 1. Deshalb verwendet der Bot diese Kombi-Ansicht: einfacher, schneller
und ohne Cookie-Banner-Ärger, da kein Browser mehr nötig ist. Sie zeigt weiterhin
Niederschlag inkl. aller Ensemble-Member und der weißen AVG-Linie ("Mittel aller
Berechnungen"), zusätzlich aber auch die 850-hPa-Temperatur.

Verwendet wird `member=ENS` (alle Ensemble-Member + AVG), mit automatischem Rückfall auf
`member=OP` (nur der operationelle Lauf), falls ein Modell kein Ensemble anbietet –
vermutlich der Fall bei AIFS.

Station und Parameter stehen in `send_weather.py` ganz oben:
- `DIAGRAM_GEOID = 141668` (Ebringen 79285)
- `DIAGRAM_VAR = 201` (Kombi: 850 hPa Temp. & Niederschlag)

Für eine andere Station: die geoid bekommst du, indem du auf wetterzentrale.de bei
"Diagramme" den gewünschten Ort auswählst; die Zahl steht dann in der Bild-Adresse
(Rechtsklick auf den Chart → "Grafikadresse kopieren") hinter `geoid=`.

## Einrichtung (einmalig, ca. 5 Minuten)

1. **Neues GitHub-Repository anlegen** (kann privat sein, z.B. `wetter-telegram-bot`).
2. Die Dateien aus diesem Paket in das Repo hochladen, mit genau dieser Struktur:
   ```
   .github/workflows/wetter-telegram.yml
   send_weather.py
   ```
   (Einfachste Methode: im Repo auf "Add file" → "Upload files" und die Dateien/den
   Ordner per Drag & Drop hochladen – GitHub behält die Ordnerstruktur bei.)
3. **Secrets hinterlegen:** Im Repo unter *Settings → Secrets and variables → Actions
   → New repository secret* zwei Secrets anlegen:
   - Name: `TELEGRAM_BOT_TOKEN` → Wert: dein Bot-Token
   - Name: `TELEGRAM_CHAT_ID` → Wert: `1039642345`

   (Bitte das Bot-Token nicht im Klartext im Code speichern – nur als Secret, damit es
   nicht öffentlich sichtbar ist. Da du es bereits im Chat geteilt hast: falls dir das
   unangenehm ist, kannst du es jederzeit über @BotFather mit `/revoke` bzw. neu
   generieren lassen.)
4. Fertig. Der Workflow läuft automatisch 3x täglich (06/12/18 Uhr UTC).

## Zum Testen

Im Repo unter dem Tab **Actions** den Workflow "Wetterkarten an Telegram senden"
auswählen und rechts auf **Run workflow** klicken, um ihn sofort einmal manuell
auszulösen (statt auf den nächsten Cron-Zeitpunkt zu warten). Danach in Telegram
prüfen, ob nacheinander drei Nachrichten mit je 4 Bildern ankommen (Niederschlag,
850hPa-Temperatur, Ebringen-Diagramm) – dauert insgesamt nur wenige Sekunden.

## Zeiten ändern

Die Sendezeiten stehen in `.github/workflows/wetter-telegram.yml` in der `cron`-Zeile.
GitHub Actions rechnet **immer in UTC**, nicht in deiner Zeitzone. Aktuell:

```
cron: "0 6,12,18 * * *"   →  06:00, 12:00, 18:00 UTC
```

Das entspricht gerade (Sommerzeit, UTC+2): **08:00, 14:00, 20:00 Uhr**.
Im Winter (UTC+1) verschiebt sich das automatisch auf 07:00, 13:00, 19:00 Uhr –
falls du es dann wieder auf die "gefühlt gleiche" Uhrzeit zurückstellen willst,
musst du die Zahlen im Cron-Ausdruck einmal von Hand anpassen.

## Was die Karten zeigen

Alle Karten: Region Mitteleuropa, immer der 18Z-Lauf (siehe oben), notfalls bis zu
`MAX_DAY_FALLBACKS` Tage zurück, falls wetterzentrale.de die Datei mal nicht neu
erzeugt hat (per Last-Modified-Header geprüft, siehe `MAX_STALENESS_HOURS`).

- **Gesamt-Niederschlag** (`var=18`): Vorhersagezeitraum so weit wie möglich - das
  Skript probiert der Reihe nach +384h, +360h, +240h, +180h, +168h, +144h und nimmt
  die längste, die für das jeweilige Modell/Lauf tatsächlich existiert (steht auch
  in der Bildunterschrift, z.B. "bis +240h"). ECMWFs 18Z-Lauf ist typischerweise ein
  "Kurzläufer" und geht meist nur bis 144h, GFS/ICON/AIFS oft deutlich weiter.
- **850 hPa Temperatur** (`var=2`): aktueller Lauf (Analyse, `time=0`, kein
  Vorhersage-Offset).

Anpassbar in `send_weather.py`: `TARGET_RUN_HOUR` (welcher Lauf, Standard 18),
`time_candidates` je Produkt in `MAP_PRODUCTS`, `MAX_STALENESS_HOURS`.

## Fix: 850-hPa-Temperaturkarten zeigten alte Daten (trotz Freshness-Check)

Beobachtung: Die Niederschlagskarten und das Ebringen-Diagramm zeigten korrekt den
aktuellen Lauf, die 850-hPa-Temperaturkarten (`var=2`) blieben aber bei allen 4
Modellen tagelang auf demselben alten Lauf stehen ("Init: Fr,21.08. 18Z"), obwohl
die HTML-Übersichtsseite von wetterzentrale.de für dieselbe Karte bereits den
aktuellen Lauf anzeigte. Das deutet darauf hin, dass eine zwischengespeicherte
(gecachte) alte Kopie der Bild-Datei ausgeliefert wurde, nicht auf einen Fehler in
der Datumsberechnung.

Fix: Jede Bild-URL bekommt jetzt beim Abruf einen eindeutigen Cache-Busting-
Parameter (`&_cb=<Zeitstempel>`) sowie `Cache-Control: no-cache`/`Pragma: no-cache`
mitgeschickt – sowohl beim eigenen Verfügbarkeits-Check als auch bei der URL, die
an Telegram geschickt wird (damit auch Telegrams eigener Abruf nicht an einer
alten zwischengespeicherten Version hängen bleibt). Zusätzlich gibt das Skript
jetzt bei jeder Karten-Prüfung eine Diagnosezeile im Log aus
(`[Last-Modified-Check] status=... Last-Modified='...' -> ...`), damit sich ein
eventuelles erneutes Auftreten anhand des Actions-Logs sofort erkennen lässt.

Falls die 850-hPa-Karten nach diesem Update immer noch veraltet ankommen: bitte
einmal den Workflow manuell auslaufen lassen ("Run workflow") und mir die
`[Last-Modified-Check]`-Zeilen aus dem Log für `temp850` schicken – daran lässt
sich dann genau sehen, ob wetterzentrale.de für diese Karte gar keinen
Last-Modified-Header sendet (dann bräuchte es einen anderen Freshness-Trick).

---

# Zusatz: Zusammenfassung, neue Kartentypen, Radar, Warnungen, Trefferquote

Alles läuft im bestehenden 3x-täglichen Rhythmus (06/12/18 UTC) mit, kein
neuer Cron-Eintrag nötig. Zwei neue Bausteine:

- `weather_summary.py` – neues Modul, wird von `send_weather.py` importiert.
- 3 neue Karten-Produkte in `MAP_PRODUCTS` (`send_weather.py`).

## Neue Kartentypen (wetterzentrale.de, gleiches Schema wie bisher)

- **Windböen** (`var=19`): ECMWF, GFS, ICON. AIFS bietet diese Karte auf
  wetterzentrale.de nicht an.
- **Schneehöhe** (`var=25`, Gesamt-Schneehöhe, nicht Neuschnee-Zuwachs):
  GFS, ICON. ECMWF und AIFS bieten sie nicht an.
- **Gewitterindex/CAPE** (`var=11`, CAPE/LI kombiniert): GFS, ICON. ECMWF
  und AIFS bieten sie nicht an.

**Nicht umgesetzt: Bodendruck/Frontenkarte.** Die läuft auf wetterzentrale.de
über ein komplett anderes Tool (`fax.php` statt `topkarten.php`) mit anderem
Modell-Angebot (KNMI/DWD/NWS/UKMO statt ECMWF/GFS/ICON/AIFS) und ganz anderem
URL-Schema (kein `var=`, kein Member/Lauf wie gewohnt). Passt nicht in das
bestehende Muster - falls gewünscht, sag Bescheid, das würde eine eigene
kleine Fetch-Funktion brauchen.

## DWD-Regenradar

Aktuelles Regenradar-Kompositbild für ganz Deutschland von
`dwd.de/DWD/wetter/radar/rad_brd_akt.jpg` (mit Cache-Busting wie bei den
850hPa-Karten). Wird als eigenes Bild verschickt. Nicht garantiert: dieser
Pfad liegt außerhalb des offiziellen `opendata.dwd.de`-Datenangebots und ist
aus der Sandbox heraus nicht testbar gewesen - falls das 403/leer liefert,
kommt einfach "DWD-Regenradar" in der Hinweis-Nachricht als nicht verfügbar.

## Textzusammenfassung (neue Nachricht, `weather_summary.py`)

Eine zusätzliche Textnachricht pro Sendung mit bis zu 3 Abschnitten:

1. **DWD-Unwetterwarnungen** für Ebringen, über die freie, offene
   [Bright Sky API](https://brightsky.dev) (Wrapper um DWD-CAP-Daten, kein
   Key nötig, hat Ebringen direkt über Koordinaten der passenden DWD-Warnzelle
   zugeordnet). Erscheint nur, wenn gerade eine Warnung aktiv ist.
2. **Zahlen-Modellvergleich** (Niederschlag + Max.-Temp. heute/morgen, alle 4
   Modelle nebeneinander) über die freie [Open-Meteo API](https://open-meteo.com)
   (kein Key nötig). wetterzentrale.de liefert nur Bilder, keine Zahlen -
   dafür wird hier eine zweite, unabhängige Datenquelle verwendet.
3. **Schwellenwert-Hinweis** (⚠️): erscheint automatisch, wenn mind. ein
   Modell ≥15mm Niederschlag oder ≥70km/h Böen für heute/morgen zeigt
   (Schwellen ganz oben in `weather_summary.py` anpassbar:
   `ALERT_PRECIP_MM`, `ALERT_GUST_KMH`).
4. **Modell-Trefferquote**: wie weit lagen ECMWF/GFS/ICON/AIFS in der
   Vergangenheit tatsächlich daneben? Vergleicht die Vorhersage, die ein
   Modell vor 2 Tagen für ein bestimmtes Datum gemacht hat
   (Open-Meteo "Previous Runs API" - ein Archiv echter, damals gemachter
   Vorhersagen), mit dem tatsächlich eingetroffenen Wetter an dem Tag
   (Open-Meteo "Historical Weather API", ERA5-Reanalyse als Referenz),
   gemittelt über die letzten 5 auswertbaren Tage. Läuft ca. 6 Tage
   "hinterher" (ERA5 braucht ~5 Tage, bis die Referenzdaten feststehen) -
   das ist also immer ein Rückblick, keine Live-Bewertung von heute.
   Anpassbar: `VERIFY_LEAD_DAYS` (wie viele Tage im Voraus geprüft wird),
   `VERIFY_WINDOW_DAYS` (Mittelungszeitraum).

**Wichtig - noch nicht live getestet:** Ich konnte aus dieser Sandbox heraus
keine echten Anfragen an Open-Meteo/Bright Sky/DWD schicken (Netzwerk dort
generell blockiert). Die URLs und Feldnamen stammen aus deren Dokumentation,
die Datums-/Index-Logik habe ich lokal ohne Netzwerk durchgetestet - aber ob
z.B. die genauen Spaltennamen der Open-Meteo-Antwort exakt stimmen, zeigt
erst ein echter Lauf. Jeder Abschnitt ist einzeln fehlertolerant: fehlt ein
Feld, wird nur dieser Abschnitt übersprungen (mit `[Diagnose/...]`-Zeile im
Log), nichts stürzt ab. **Bitte einmal "Run workflow" testen und mir das Log
schicken**, falls die Zusammenfassung fehlt oder komisch aussieht - dann
lässt sich das gezielt anhand der Diagnose-Zeilen nachbessern.

## Timing der neuen Features

Alles (Warnungen, Modellvergleich, Trefferquote, Radar, neue Karten) wird nur
zu den 3 festen Sendezeiten (06/12/18 UTC) geprüft und verschickt - keine
sofortige Warnung bei neu auftretenden Unwetterwarnungen zwischendurch. Falls
du das doch willst (z.B. Warnungen sofort statt erst zur nächsten der 3
Sendungen), bräuchte es einen häufigeren Cron (z.B. alle 15-30 Minuten nur
für den Warnungs-Check) - sag Bescheid, das lässt sich ergänzen.

---

## Verworfen: Kachelmannwetter-Anbindung

Es gab einen Versuch, zusätzlich 2x täglich eine Freiburg-Temperaturkarte von
kachelmannwetter.de zu holen (`send_kachelmann.py` +
`.github/workflows/kachelmann-telegram.yml`). kachelmannwetter.de blockiert
automatisierten Zugriff aber konsequent mit HTTP 403 (Bot-/Zugriffsschutz) –
das wurde nicht umgangen, die Idee wurde daher verworfen. Falls du diese
beiden Dateien schon in dein Repo hochgeladen hattest: bitte dort löschen,
sonst schlägt der Workflow 2x täglich fehl und du bekommst wiederholt
"nicht verfügbar"-Nachrichten in Telegram.
