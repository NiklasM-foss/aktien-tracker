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
| `PLANSPIEL_USER` / `PLANSPIEL_PASSWORD` | leer | Login des Planspiel-Börse-Kontos fest vorgeben (sonst über den Knopf **🔗 Planspiel-Login**) |
| `PLANSPIEL_FILE` | `planspiel.json` neben `DEPOT_FILE` | wo die per Knopf eingegebenen Zugangsdaten liegen (Rechte 600) |
| `PLANSPIEL_DEPOT` | erstes Depot        | Depot-ID im Planspiel (sonst das Wettbewerbsdepot)     |
| `PLANSPIEL_INTERVAL` | `30`             | Sekunden zwischen zwei Abfragen beim Planspiel         |

### Mit dem Planspiel-Konto verknüpfen

Oben rechts auf **🔗 Planspiel-Login** klicken, Benutzername und Passwort des
Planspiel-Kontos eingeben, **Verbinden**. Ist `EDIT_PASSWORD` gesetzt, fragt die
Seite vorher das Tracker-Passwort ab, damit nicht jeder Besucher ein Konto
verknüpfen kann. Der Server prüft die Daten direkt beim Planspiel und speichert
sie nur bei Erfolg in `PLANSPIEL_FILE` (nur für den Dienst lesbar, nie zurück an
den Browser). **Abmelden** im selben Fenster löscht die Datei wieder.
Alternativ lassen sich die Zugangsdaten fest über `PLANSPIEL_USER` und
`PLANSPIEL_PASSWORD` vorgeben (z.B. in `/etc/aktien-tracker.env`).

Solange ein Konto verknüpft ist, loggt sich der Server bei
trading.planspiel-boerse.de ein und übernimmt alle 30 Sekunden:

- Positionen, Kaufkurse, Kaufzeiten und Gebühren aus den Buchungen
  (`transaction/getTransactions`), Verkäufe, Dividenden und sonstige Buchungen,
- den Geldkurs der Börse Stuttgart je Position als Bewertungskurs
  (`portfolio/getPortfolioWithItems`), Tradegate nur noch als Ersatz,
- das Nachhaltigkeits-Kennzeichen (*/**) je Wertpapier.

Bearbeiten, **+ Aktie** und **Verkauf** sind dann ausgeblendet, das Depot
kommt nur noch aus dem Planspiel. In der Statuszeile steht **🔗 Planspiel**,
der Tooltip zeigt den letzten Abgleich oder den Fehler. Bei falschem Passwort
versucht der Server es nur alle 15 Minuten erneut, damit das Konto nicht gesperrt wird.

### Depot bearbeiten

Aktienliste, Stückzahl, Kaufpreis, Währung und Gebühren liegen zentral auf dem
Server (`DEPOT_FILE`), alle Besucher sehen dieselben Werte. Fehlt die Datei,
startet das Depot mit den fünf Ausgangswerten und 50.000 € Startkapital.

Im Bearbeiten-Modus gibt es den Tab **+ Aktie**: ISIN oder Yahoo-Kürzel
eingeben (z.B. `DE0007164600` oder `SAP.DE`), dazu Stückzahl, Gesamtpreis und
Kaufgebühr. Name und Kürzel kommen von Yahoo, bei einer ISIN wird der deutsche
Handelsplatz bevorzugt und in Euro über Tradegate bewertet. **Aktie entfernen**
steht in der Depot-Karte der jeweiligen Aktie.

**Verkauf** (ebenfalls in der Depot-Karte): Stückzahl, Kurs je Aktie, Gebühr und
Zeitpunkt. Die Position wird anteilig verkleinert (Einstand nach
Durchschnittskosten), die Differenz aus Erlös minus Gebühr minus anteiligem
Einstand ist der realisierte Gewinn. Die Kaufgebühr bleibt wie im Planspiel
komplett bei der Restposition und wird erst mit dem letzten Stück realisiert. Er fließt ins verfügbare Geld und in den
Gesamtgewinn und steht als eigene Kachel *Realisiert* in der Übersicht. Die
Gebühr wird nach der Planspiel-Regel vorgeschlagen (Aktien, Fonds, ETFs: 0,3 % vom
Kurswert, mindestens 15 €, ungerundet wie im Handelstagebuch), beim Hinzufügen
einer Aktie genauso die Kaufgebühr.

Weitere Planspiel-Regeln in der Anzeige: Jede Position zeigt ihren Anteil am
Depotgesamtwert und wie viel noch nachgekauft werden darf (Bestand plus Kauf
höchstens 20 %), das Formular **+ Aktie** warnt bei Käufen darüber. Aktien mit
Häkchen **nachhaltig** (im Planspiel * oder **) gehen in die Kachel
*Nachhaltigkeitsertrag* ein (Kursgewinne/-verluste ohne Gebühren, offen und verkauft).
Wird alles verkauft, verschwindet die Aktie aus der Liste, der Verkauf bleibt
im Depot gespeichert. Ist `EDIT_PASSWORD`
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
| `/api/summary` | GET | Depot ausgewertet: Gesamtvermögen, Depotwert, Gewinn/Verlust, Tagesveränderung, Nachhaltigkeitsertrag (`green_pl`), je Aktie Kurs, Wert, Gewinn, Anteil (`share_pct`) und Nachkauf-Spielraum (`buy_room`), alles in Euro; `planspiel` = Stand des Kontoabgleichs. Gedacht für Home Assistant |
| `/api/depot` | GET | Aktienliste mit Kaufdaten |
| `/api/stock/add` | POST | `{"query": ISIN oder Kürzel, "values": {...}}`, Header `X-Password` |
| `/api/stock/delete` | POST | `{"symbol": ...}`, Header `X-Password` |
| `/api/stock/sell` | POST | `{"symbol", "qty", "price", "fee", "time"}`, Header `X-Password` |
| `/api/depot` | POST | `{"symbol": ..., "values": {...}}`, Header `X-Password` |

Für Home Assistant gibt es die Integration
[ha-aktien-tracker](https://github.com/NiklasM-foss/ha-aktien-tracker).

## Laufende Instanz

Container 124 `planspiel-boerse` auf dem Proxmox-Host `.200`,
`http://192.168.177.28:8080`, Git-Klon unter `/opt/planspiel/aktien-tracker`
(Remote GitHub), Depotwerte in `/opt/planspiel/depot.json`, Dienst
`aktien-tracker`. Öffentlich über einen Cloudflare-Tunnel.
