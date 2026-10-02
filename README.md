# Aktien-Tracker

Live-Kursanzeige für ein Planspiel-Börse-Depot (NVIDIA, Micron, MSCI World,
Marvell, Siemens Energy). Eine einzelne HTML-Seite plus ein kleiner Python-Server
ohne Abhängigkeiten, der die Kurse abholt, weil der Browser Yahoo und Tradegate
wegen CORS nicht direkt abfragen darf.

- Charts und Laufband: Yahoo Finance
- Depotbewertung in Euro: Tradegate (Echtzeit)
- Tagesgewinner USA: Yahoo-Screener, nur Firmen ab 2 Mrd. $ Börsenwert
- Aktualisierung alle 5 Sekunden, Stückzahl, Kaufpreis und Gebühren lassen sich
  auf der Seite ändern

## Lokal starten

Voraussetzung ist Python 3. Doppelklick auf `Start (Windows).bat` bzw.
`Start (Mac).command`, der Browser öffnet dann `http://localhost:8766`.
Details in `ANLEITUNG.txt`.

## Auf einem Server betreiben

Der Server liest diese Umgebungsvariablen:

| Variable        | Standard              | Bedeutung                                              |
|-----------------|-----------------------|--------------------------------------------------------|
| `HOST`          | `127.0.0.1`           | Adresse, auf der gelauscht wird                        |
| `PORT`          | `8766`                | Port                                                   |
| `NO_BROWSER`    | leer                  | gesetzt = keinen Browser öffnen (headless)             |
| `EDIT_PASSWORD` | leer                  | Passwort zum Bearbeiten des Depots; leer = frei        |
| `DEPOT_FILE`    | `depot.json` daneben  | wo die Depotwerte gespeichert werden                   |
| `ALLOW_UPDATE`  | leer                  | gesetzt = Update-Knopf aktiv (braucht `EDIT_PASSWORD`) |
| `UPDATE_BRANCH` | `main`                | Branch, den der Update-Knopf holt                      |

### Depot bearbeiten

Aktienliste, Stückzahl, Kaufpreis, Währung und Gebühren liegen zentral auf dem
Server (`DEPOT_FILE`), alle Besucher sehen dieselben Werte. Fehlt die Datei,
startet das Depot mit den fünf Ausgangswerten und 50.000 € Startkapital.

Im Bearbeiten-Modus gibt es den Tab **+ Aktie**: ISIN oder Yahoo-Kürzel
eingeben (z.B. `DE0007164600` oder `SAP.DE`), dazu Stückzahl, Gesamtpreis und
Kaufgebühr. Name und Kürzel kommen von Yahoo, bei einer ISIN wird der deutsche
Handelsplatz bevorzugt und in Euro über Tradegate bewertet. **Aktie entfernen**
steht in der Depot-Karte der jeweiligen Aktie. Ist `EDIT_PASSWORD`
gesetzt, sind die Felder gesperrt; über **🔒 Bearbeiten** fragt die Seite das
Passwort ab und schaltet sie frei. Änderungen werden automatisch gespeichert.
Der Kursalarm bleibt pro Browser und braucht kein Passwort.

### Update-Knopf

Läuft der Server in einem Git-Klon dieses Repos und ist `ALLOW_UPDATE` gesetzt,
erscheint oben rechts **⟳ Update** (Tooltip zeigt den aktuellen Stand). Nach
Passwort-Abfrage holt er den neuesten Stand von `origin/main`
(`git fetch` + `reset --hard`, lokale Änderungen im Deploy-Ordner gehen also
verloren) und beendet den Server, systemd startet ihn mit dem neuen Code neu.
Ablauf zum Deployen: Änderung auf `main` pushen, dann auf der Seite
**⟳ Update** drücken.

### systemd

`aktien-tracker.service` nach `/etc/systemd/system/` kopieren, Pfad und User
anpassen, das Passwort in `/etc/aktien-tracker.env` ablegen
(`EDIT_PASSWORD=...`, Rechte 600), dann

    systemctl daemon-reload
    systemctl enable --now aktien-tracker

Die Unit startet den Server auf `0.0.0.0:8080` mit `Restart=always`. HTTPS
übernimmt ein vorgeschalteter Proxy bzw. Tunnel.

## API

| Pfad | Methode | Beschreibung |
|------|---------|--------------|
| `/api/summary` | GET | Depot ausgewertet: Gesamtvermögen, Depotwert, Gewinn/Verlust, Tagesveränderung, je Aktie Kurs, Wert und Gewinn (alles in Euro). Gedacht für Home Assistant |
| `/api/depot` | GET | Aktienliste mit Kaufdaten |
| `/api/stock/add` | POST | `{"query": ISIN oder Kürzel, "values": {...}}`, Header `X-Password` |
| `/api/stock/delete` | POST | `{"symbol": ...}`, Header `X-Password` |
| `/api/depot` | POST | `{"symbol": ..., "values": {...}}`, Header `X-Password` |

Für Home Assistant gibt es die Integration
[ha-aktien-tracker](https://github.com/NiklasM-foss/ha-aktien-tracker).

## Laufende Instanz

Container 124 `planspiel-boerse` auf dem Proxmox-Host `.200`,
`http://192.168.177.28:8080`, Git-Klon unter `/opt/planspiel/aktien-tracker`
(Remote GitHub), Depotwerte in `/opt/planspiel/depot.json`, Dienst
`aktien-tracker`. Öffentlich über einen Cloudflare-Tunnel.
