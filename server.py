#!/usr/bin/env python3
"""Kleiner Proxy fuer Yahoo-Finance-Kurse (Browser darf Yahoo wegen CORS nicht direkt abfragen)."""
import hmac, json, os, re, subprocess, threading, time, urllib.request, urllib.parse
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

def load_depot():
    try:
        return json.loads(DEPOT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def save_depot(symbol, values):
    """Speichert die Kaufdaten einer Aktie; nur bekannte Felder, kurze Werte."""
    if not SYMBOL_RE.match(symbol or "") or not isinstance(values, dict):
        raise ValueError("ungueltige Daten")
    clean = {}
    for k, v in values.items():
        if k not in DEPOT_FIELDS or not isinstance(v, str) or len(v) > 20:
            raise ValueError("ungueltiges Feld " + str(k))
        if k == "dCur" and v not in ("EUR", "USD"):
            raise ValueError("ungueltige Waehrung")
        clean[k] = v
    with DEPOT_LOCK:
        depot = load_depot()
        if symbol not in depot and len(depot) >= 50:
            raise ValueError("zu viele Aktien")
        depot.setdefault(symbol, {}).update(clean)
        tmp = DEPOT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(depot, indent=1), encoding="utf-8")
        tmp.replace(DEPOT_FILE)
    return depot

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
                self.send(200, json.dumps({"protected": bool(PASSWORD), "depot": load_depot()}).encode(),
                          "application/json")
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
        if u.path not in ("/api/login", "/api/depot", "/api/update"):
            return self.send(404, b"not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 10000:
                return self.send(413, b"zu gross", "text/plain")
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send(400, b"kein JSON", "text/plain")
        if not password_ok(self.headers.get("X-Password")):
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
            depot = save_depot(body.get("symbol"), body.get("values"))
        except ValueError as e:
            return self.send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        self.send(200, json.dumps({"depot": depot}).encode(), "application/json")

if __name__ == "__main__":
    import threading, webbrowser
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"Aktien-Tracker laeuft auf http://localhost:{PORT}  (Fenster offen lassen, Strg+C beendet)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()
