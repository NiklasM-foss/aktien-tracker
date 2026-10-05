#!/usr/bin/env python3
"""Kleiner Proxy fuer Yahoo-Finance-Kurse (Browser darf Yahoo wegen CORS nicht direkt abfragen)."""
import copy, hmac, json, os, re, subprocess, threading, time, urllib.request, urllib.parse
from datetime import date, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8766))
HOST = os.environ.get("HOST", "127.0.0.1")
# Passwort zum Bearbeiten des Depots; leer = Bearbeiten ohne Passwort (lokale Nutzung)
PASSWORD = os.environ.get("EDIT_PASSWORD", "")
# Update-Knopf: holt den neuesten Stand aus dem Git-Repo und startet neu (nur mit systemd o.ae. sinnvoll)
ALLOW_UPDATE = bool(os.environ.get("ALLOW_UPDATE")) and bool(PASSWORD)
BRANCH = os.environ.get("UPDATE_BRANCH", "main")
HERE = Path(__file__).parent
CACHE = {}  # url -> (zeit, daten)
DEPOT_FILE = Path(os.environ.get("DEPOT_FILE", HERE / "depot.json"))
DEPOT_LOCK = threading.Lock()
DEPOT_FIELDS = {"dQty", "dCost", "dCur", "dFeeBuy", "dFeeSell"}
EDIT_FIELDS = DEPOT_FIELDS | {"short"}
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
MAX_STOCKS = 30
START_CAPITAL = 50000   # fiktives Startgeld in Euro
# Ausgangsdepot (Werte laut Kaufabrechnung, dCost = Gesamtpreis ohne Gebuehren)
DEFAULT_STOCKS = {
    "NVDA": {"isin": "US67066G1040", "name": "NVIDIA Corp.", "bought": "2026-10-02T10:26:53",
             "dQty": "18", "dCost": "3724.20", "dCur": "EUR", "dFeeBuy": "15", "dFeeSell": "0"},
    "MU": {"isin": "US5951121038", "name": "Micron Technology", "bought": "2026-10-02T10:41:59",
           "dQty": "5", "dCost": "4922.00", "dCur": "EUR", "dFeeBuy": "15", "dFeeSell": "0"},
    "ELFW.DE": {"isin": "DE000ETFL508", "name": "Deka MSCI World UCITS ETF", "short": "MSCI World",
                "bought": "2026-10-02T10:53:47",
                "dQty": "120", "dCost": "5358.00", "dCur": "EUR", "dFeeBuy": "16.08", "dFeeSell": "0"},
    "MRVL": {"isin": "US5738741041", "name": "Marvell Technology", "bought": "2026-10-02T11:22:24",
             "dQty": "30", "dCost": "7266.00", "dCur": "EUR", "dFeeBuy": "21.80", "dFeeSell": "0"},
    "ENR.DE": {"isin": "DE000ENER6Y0", "name": "Siemens Energy AG", "short": "Siemens Energy",
               "bought": "2026-10-02T11:38:13",
               "dQty": "30", "dCost": "4374.60", "dCur": "EUR", "dFeeBuy": "15", "dFeeSell": "0"},
}
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=-]{1,15}$")
RANGES = {"1d": "1m", "5d": "5m", "1mo": "30m", "6mo": "1d", "1y": "1d", "5y": "1wk"}

def fetch(url, max_age):
    hit = CACHE.get(url)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    if len(CACHE) > 500:  # beliebige Symbole sollen den Speicher nicht volllaufen lassen
        CACHE.clear()
    CACHE[url] = (time.time(), data)
    return data

def chart(symbol, rng):
    interval = RANGES.get(rng, "1m")
    q = urllib.parse.urlencode({"interval": interval, "range": rng, "includePrePost": "true"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{q}"
    res = fetch(url, 3 if rng == "1d" else 60)["chart"]["result"][0]
    ind = res["indicators"]["quote"][0]
    pts = [[t, c] for t, c in zip(res.get("timestamp", []), ind.get("close", [])) if c is not None]
    if rng == "1d" and not pts:
        # vor Handelsbeginn (z.B. Xetra/Mailand) liefert Yahoo für heute nichts -> letzten Handelstag zeigen
        pts = chart(symbol, "5d")["points"]
        if pts:
            last = datetime.fromtimestamp(pts[-1][0]).date()
            pts = [p for p in pts if datetime.fromtimestamp(p[0]).date() == last]
    return {"meta": res["meta"], "points": pts}

def movers(count=10):
    """Aktuelle Tagesgewinner (USA), nur Firmen ab 2 Mrd. $ Börsenwert, damit keine Zockerwerte auftauchen."""
    url = ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
           "?scrIds=day_gainers&count=50&lang=de-DE&region=DE")
    quotes = fetch(url, 60)["finance"]["result"][0]["quotes"]
    out = []
    for q in quotes:
        if (q.get("marketCap") or 0) < 2e9:
            continue
        out.append({k: q.get(k) for k in ("symbol", "shortName", "regularMarketPrice", "regularMarketChangePercent",
                                           "marketCap", "currency", "marketState", "preMarketChangePercent",
                                           "postMarketChangePercent")})
        if len(out) >= count:
            break
    return out

def tradegate(isin):
    """Echtzeit-Kurs in EUR von Tradegate (so rechnet auch die Depot-App)."""
    url = "https://www.tradegatebsx.com/refresh.php?isin=" + urllib.parse.quote(isin)
    d = fetch(url, 3)
    num = lambda v: float(v.replace(".", "").replace(",", ".")) if isinstance(v, str) else v
    return {k: num(d.get(k)) for k in ("bid", "ask", "last", "close", "high", "low")}

def eurusd():
    try:
        return chart("EURUSD=X", "1d")["meta"]["regularMarketPrice"]
    except Exception:
        return None

def search(query):
    """Yahoo-Suche nach ISIN, Kuerzel oder Name; deutsche Handelsplaetze zuerst."""
    q = urllib.parse.urlencode({"q": query, "quotesCount": 10, "newsCount": 0, "lang": "de-DE", "region": "DE"})
    quotes = fetch("https://query1.finance.yahoo.com/v1/finance/search?" + q, 300).get("quotes", [])
    quotes = [x for x in quotes if x.get("symbol") and x.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")]
    return sorted(quotes, key=lambda x: not x["symbol"].endswith(".DE"))

# ---------- Depot (Aktienliste + Kaufdaten) ----------
def _write(depot):
    tmp = DEPOT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(depot, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DEPOT_FILE)

def load_depot():
    try:
        depot = json.loads(DEPOT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        depot = {}
    if depot.get("version") != 2:
        # altes Format {symbol: kaufdaten} bzw. noch nichts gespeichert: Ausgangsdepot plus Aenderungen
        stocks = copy.deepcopy(DEFAULT_STOCKS)
        for sym, values in depot.items():
            if sym in stocks and isinstance(values, dict):
                stocks[sym].update({k: v for k, v in values.items() if k in DEPOT_FIELDS})
        depot = {"version": 2, "start_capital": START_CAPITAL, "stocks": stocks}
    return depot

def save_depot(symbol, values):
    """Aendert die Kaufdaten einer vorhandenen Aktie; nur bekannte Felder, kurze Werte."""
    if not isinstance(values, dict):
        raise ValueError("ungueltige Daten")
    clean = {}
    for k, v in values.items():
        if k not in EDIT_FIELDS or not isinstance(v, str) or len(v) > 30:
            raise ValueError("ungueltiges Feld " + str(k))
        if k == "dCur" and v not in ("EUR", "USD"):
            raise ValueError("ungueltige Waehrung")
        clean[k] = v.strip() if k == "short" else v
    with DEPOT_LOCK:
        depot = load_depot()
        if symbol not in depot["stocks"]:
            raise ValueError("Aktie nicht im Depot")
        depot["stocks"][symbol].update(clean)
        _write(depot)
    return depot

def add_stock(query, values):
    """Neue Aktie per ISIN oder Kuerzel; Name und Kuerzel kommen von Yahoo."""
    query = (query or "").strip().upper()
    if not query or len(query) > 15:
        raise ValueError("ISIN oder Kuerzel fehlt")
    isin = query if ISIN_RE.match(query) else ""
    if isin:
        hits = search(isin)
        if not hits:
            raise ValueError(f"Keine Aktie zur ISIN {isin} gefunden")
        symbol = hits[0]["symbol"]
    elif SYMBOL_RE.match(query):
        symbol = query
        hits = [x for x in search(symbol) if x["symbol"].upper() == symbol]
    else:
        raise ValueError("Weder ISIN noch gueltiges Kuerzel")
    try:
        meta = chart(symbol, "1d")["meta"]
    except Exception:
        raise ValueError(f"Keine Kurse fuer {symbol} gefunden")
    name = ((hits[0].get("longname") or hits[0].get("shortname")) if hits else None) \
        or meta.get("longName") or meta.get("shortName") or symbol
    stock = {"isin": isin, "name": " ".join(name.split())[:60],
             "bought": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "dQty": "0", "dCost": "0", "dCur": "EUR", "dFeeBuy": "0", "dFeeSell": "0"}
    with DEPOT_LOCK:
        depot = load_depot()
        if symbol in depot["stocks"]:
            raise ValueError(f"{symbol} ist schon im Depot")
        if len(depot["stocks"]) >= MAX_STOCKS:
            raise ValueError("Zu viele Aktien im Depot")
        depot["stocks"][symbol] = stock
        _write(depot)
    if values:
        depot = save_depot(symbol, values)
    return symbol, depot

def sell_stock(symbol, qty, price, fee, when):
    """Teilverkauf/Verkauf: Position anteilig verkleinern (Durchschnittskosten), Gewinn realisieren.

    Verfuegbares Geld = Startkapital - Bezahltes der offenen Positionen + realisierter Gewinn,
    deshalb reicht es, je Verkauf den realisierten Gewinn (in Euro) mitzuschreiben.
    """
    qty, price, fee = _num(qty), _num(price), _num(fee)
    try:
        when = datetime.fromisoformat(when).strftime("%Y-%m-%dT%H:%M:%S") if when else None
    except ValueError:
        raise ValueError("ungueltiger Zeitpunkt")
    when = when or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if qty <= 0 or price <= 0 or fee < 0:
        raise ValueError("Stueckzahl und Kurs muessen groesser 0 sein")
    with DEPOT_LOCK:
        depot = load_depot()
        st = depot["stocks"].get(symbol)
        if st is None:
            raise ValueError("Aktie nicht im Depot")
        have = _num(st.get("dQty"))
        if qty > have + 1e-9:
            raise ValueError(f"Nur {have:g} Stueck im Depot")
        frac = qty / have
        basis, buy_fee = _num(st.get("dCost")) * frac, _num(st.get("dFeeBuy")) * frac
        realized = qty * price - fee - basis - buy_fee
        fx = 1.0
        if st.get("dCur", "EUR") != "EUR":
            fx = eurusd()
            if not fx:
                raise ValueError("kein EUR/USD-Kurs, bitte spaeter erneut versuchen")
        eur = lambda v: round(v / fx, 2)
        depot.setdefault("sales", []).append({
            "symbol": symbol, "name": st.get("name"), "time": when, "qty": qty, "price": price,
            "cur": st.get("dCur", "EUR"), "fee": fee, "basis": round(basis, 2), "buy_fee": round(buy_fee, 2),
            "realized_eur": eur(realized), "basis_eur": eur(basis + buy_fee), "fees_eur": eur(fee + buy_fee),
        })
        if have - qty < 1e-9:
            del depot["stocks"][symbol]   # alles verkauft
        else:
            fmt = lambda v: f"{v:.2f}".rstrip("0").rstrip(".") if v % 1 else f"{v:.0f}"
            st["dQty"] = fmt(have - qty)
            st["dCost"] = f"{_num(st.get('dCost')) - basis:.2f}"
            st["dFeeBuy"] = f"{_num(st.get('dFeeBuy')) - buy_fee:.2f}"
        _write(depot)
    return depot

def realized_totals(depot):
    sales = depot.get("sales", [])
    return {
        "pl": round(sum(x["realized_eur"] for x in sales), 2),
        "basis": round(sum(x["basis_eur"] for x in sales), 2),
        "fees": round(sum(x["fees_eur"] for x in sales), 2),
        "count": len(sales),
    }

def delete_stock(symbol):
    with DEPOT_LOCK:
        depot = load_depot()
        if depot["stocks"].pop(symbol, None) is None:
            raise ValueError("Aktie nicht im Depot")
        _write(depot)
    return depot

# ---------- Auswertung (gleiche Rechnung wie auf der Seite, fuer Home Assistant) ----------
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def position(sym, st, fx):
    d = chart(sym, "1d")
    meta, pts = d["meta"], d["points"]
    ccy, cur = meta.get("currency"), st.get("dCur", "EUR")
    if cur != ccy and not fx:
        raise ValueError("kein EUR/USD-Kurs")
    conv = lambda v: v if ccy == cur else (v / fx if cur == "EUR" else v * fx)
    px = pts[-1][1] if pts else meta["regularMarketPrice"]
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    qty, cost = _num(st.get("dQty")), _num(st.get("dCost"))
    fees = _num(st.get("dFeeBuy")) + _num(st.get("dFeeSell"))
    t = None
    if st.get("isin"):
        try:
            t = tradegate(st["isin"])
            t = t if t.get("last") else None
        except Exception:
            t = None
    use_tg = bool(t and cur == "EUR")
    value = t["last"] * qty if use_tg else conv(px) * qty
    try:
        today = datetime.fromisoformat(st.get("bought") or "").date() == date.today()
    except ValueError:
        today = False
    if today:
        day = value - cost
    elif use_tg and t.get("close"):
        day = (t["last"] - t["close"]) * qty
    else:
        day = conv(px - prev) * qty if prev else None
    to_eur = lambda v: None if v is None else (v if cur == "EUR" else v / fx)
    regular = meta.get("regularMarketPrice")
    rprev = meta.get("chartPreviousClose") or prev
    pl = value - cost - fees
    return {
        "symbol": sym, "name": st.get("name"), "short": st.get("short") or sym, "isin": st.get("isin") or None,
        "currency": ccy, "price": regular,
        "price_eur": t["last"] if t else (px if ccy == "EUR" else (px / fx if fx and ccy == "USD" else None)),
        "change_pct": (regular - rprev) / rprev * 100 if regular and rprev else None,
        "qty": qty, "value": to_eur(value), "cost": to_eur(cost), "fees": to_eur(fees),
        "pl": to_eur(pl), "pl_pct": pl / (cost + fees) * 100 if cost + fees else 0.0,
        "day": to_eur(day), "bought": st.get("bought"), "bought_today": today,
        "source": "tradegate" if use_tg else "yahoo",
    }

def summary():
    depot = load_depot()
    fx = eurusd()
    stocks, errors = [], {}
    for sym, st in depot["stocks"].items():
        try:
            stocks.append(position(sym, st, fx))
        except Exception as e:
            errors[sym] = str(e)
    value = sum(p["value"] for p in stocks)
    paid = sum(p["cost"] + p["fees"] for p in stocks)
    fees = sum(p["fees"] for p in stocks)
    real = realized_totals(depot)
    pl = sum(p["pl"] for p in stocks) + real["pl"]   # offene Positionen plus realisierte Verkaeufe
    cash = depot["start_capital"] - paid + real["pl"]
    invested = paid + real["basis"]
    r2 = lambda v: None if v is None else round(v, 2)
    day = sum(p["day"] for p in stocks if p["day"] is not None)
    for p in stocks:
        for k in ("value", "cost", "fees", "pl", "pl_pct", "day", "price_eur", "change_pct"):
            p[k] = r2(p[k])
    return {
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete": not errors, "errors": errors, "eurusd": fx,
        "start_capital": depot["start_capital"], "cash": r2(cash), "value": r2(value), "total": r2(cash + value),
        "paid": r2(paid), "fees": r2(fees + real["fees"]), "pl": r2(pl),
        "pl_pct": r2(pl / invested * 100 if invested else 0.0),
        "pl_ex_fees": r2(pl + fees + real["fees"]), "day": r2(day),
        "realized": real["pl"], "sales": depot.get("sales", []),
        "stocks": stocks,
    }

def git(*args):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip()

def version():
    try:
        return git("log", "-1", "--format=%h|%cd|%s", "--date=format:%d.%m.%Y %H:%M").split("|", 2)
    except Exception:
        return None

def update():
    """Neuesten Stand holen; lokale Aenderungen im Deploy-Ordner werden verworfen."""
    old = git("rev-parse", "--short", "HEAD")
    git("fetch", "--quiet", "origin", BRANCH)
    git("reset", "--hard", "--quiet", "origin/" + BRANCH)
    new = git("rev-parse", "--short", "HEAD")
    if new != old:   # neu starten, systemd (Restart=always) holt den Dienst mit neuem Code zurueck
        threading.Timer(0.5, os._exit, [0]).start()
    return {"old": old, "new": new, "restart": new != old}

def password_ok(given):
    return not PASSWORD or hmac.compare_digest((given or "").strip().encode(), PASSWORD.strip().encode())

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self.send(200, (HERE / "nvidia.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/quote":
                rng = qs.get("range", ["1d"])[0]
                data = chart(qs.get("symbol", ["NVDA"])[0], rng)
                try:
                    fx = chart("EURUSD=X", "1d")["meta"]["regularMarketPrice"]
                except Exception:
                    fx = None
                data["eurusd"] = fx
                self.send(200, json.dumps(data).encode(), "application/json")
            elif u.path == "/api/tg":
                self.send(200, json.dumps(tradegate(qs.get("isin", [""])[0])).encode(), "application/json")
            elif u.path == "/api/depot":
                d = load_depot()
                self.send(200, json.dumps({"protected": bool(PASSWORD), "start_capital": d["start_capital"],
                                           "stocks": d["stocks"], "realized": realized_totals(d),
                                           "sales": d.get("sales", [])}).encode(), "application/json")
            elif u.path == "/api/summary":
                self.send(200, json.dumps(summary()).encode(), "application/json")
            elif u.path == "/api/version":
                v = version()
                self.send(200, json.dumps({"update": ALLOW_UPDATE, "commit": v and v[0], "date": v and v[1],
                                           "message": v and v[2]}).encode(), "application/json")
            elif u.path == "/api/movers":
                self.send(200, json.dumps(movers()).encode(), "application/json")
            else:
                self.send(404, b"not found", "text/plain")
        except Exception as e:
            self.send(502, json.dumps({"error": str(e)}).encode(), "application/json")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path not in ("/api/login", "/api/depot", "/api/update", "/api/stock/add", "/api/stock/delete",
                          "/api/stock/sell"):
            return self.send(404, b"not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 10000:
                return self.send(413, b"zu gross", "text/plain")
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send(400, b"kein JSON", "text/plain")
        if not password_ok(self.headers.get("X-Password")):
            given = self.headers.get("X-Password") or ""
            odd = sorted({hex(ord(c)) for c in given if not c.isascii() or not c.isalnum()})
            print(f"Login fehlgeschlagen ({u.path}): Laenge {len(given)}, Sonderzeichen {odd or 'keine'}", flush=True)
            time.sleep(1)   # Raten bremsen
            return self.send(401, json.dumps({"error": "Passwort falsch"}).encode(), "application/json")
        if u.path == "/api/login":
            return self.send(200, b'{"ok": true}', "application/json")
        if u.path == "/api/update":
            if not ALLOW_UPDATE:
                return self.send(403, json.dumps({"error": "Update ist hier abgeschaltet"}).encode(), "application/json")
            try:
                return self.send(200, json.dumps(update()).encode(), "application/json")
            except Exception as e:
                return self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        try:
            symbol = body.get("symbol")
            if u.path == "/api/stock/add":
                symbol, depot = add_stock(body.get("query"), body.get("values"))
            elif u.path == "/api/stock/delete":
                depot = delete_stock(symbol)
            elif u.path == "/api/stock/sell":
                depot = sell_stock(symbol, body.get("qty"), body.get("price"), body.get("fee"), body.get("time"))
            else:
                depot = save_depot(symbol, body.get("values"))
        except ValueError as e:
            return self.send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        self.send(200, json.dumps({"symbol": symbol, "stocks": depot["stocks"], "start_capital": depot["start_capital"],
                                   "realized": realized_totals(depot), "sales": depot.get("sales", [])}).encode(),
                  "application/json")

if __name__ == "__main__":
    import threading, webbrowser
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"Aktien-Tracker laeuft auf http://localhost:{PORT}  (Fenster offen lassen, Strg+C beendet)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()
