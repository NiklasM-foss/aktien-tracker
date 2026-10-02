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

Der Server liest drei Umgebungsvariablen:

| Variable     | Standard    | Bedeutung                                   |
|--------------|-------------|---------------------------------------------|
| `HOST`       | `127.0.0.1` | Adresse, auf der gelauscht wird             |
| `PORT`       | `8766`      | Port                                        |
| `NO_BROWSER` | leer        | gesetzt = keinen Browser öffnen (headless)  |

Beispiel mit systemd: `aktien-tracker.service` nach `/etc/systemd/system/`
kopieren, Pfad und User anpassen, dann

    systemctl daemon-reload
    systemctl enable --now aktien-tracker

Die Unit startet den Server auf `0.0.0.0:8080`. HTTPS übernimmt ein
vorgeschalteter Proxy bzw. Tunnel.

## Laufende Instanz

Container 124 `planspiel-boerse` auf dem Proxmox-Host `.200`,
`http://192.168.177.28:8080`, Code unter `/opt/planspiel/aktien-tracker`,
Dienst `aktien-tracker`. Öffentlich über einen Cloudflare-Tunnel.

Update: neue Dateien nach `/opt/planspiel/aktien-tracker` kopieren und
`systemctl restart aktien-tracker`.
