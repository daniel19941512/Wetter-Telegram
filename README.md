# Wetterzentrale → Telegram (3x täglich, per GitHub Actions)

Schickt automatisch 3x täglich die aktuellen Niederschlags-Gesamtkarten (Mitteleuropa,
+144h) von wetterzentrale.de für **ECMWF, GFS, ICON und AIFS** an deinen Telegram-Chat.

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
alle 4, oder einen anderen Vorhersagezeitraum als +144h), sag einfach Bescheid –
das lässt sich in `send_weather.py` (oben, `MODELS` / `FORECAST_HOUR`) leicht anpassen.

## Einrichtung (einmalig, ca. 5 Minuten)

1. **Neues GitHub-Repository anlegen** (kann privat sein, z.B. `wetter-telegram-bot`).
2. Die beiden Dateien aus diesem Paket in das Repo hochladen, mit genau dieser Struktur:
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
prüfen, ob die 4 Karten ankommen.

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

- Region: Mitteleuropa
- Parameter: Gesamt-Niederschlagssumme (`var=18`)
- Vorhersagezeitraum: +144h (6 Tage), akkumuliert seit Laufbeginn
- Immer der neueste Lauf, für den wetterzentrale.de das Bild schon bereitgestellt hat
  (das Skript probiert bei Bedarf automatisch bis zu 36h zurück, falls ein Modell mal
  später fertig ist).
