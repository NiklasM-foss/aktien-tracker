#!/usr/bin/env python3
"""Kleiner Proxy fuer Yahoo-Finance-Kurse (Browser darf Yahoo wegen CORS nicht direkt abfragen)."""
import json, os, time, urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8766))
HOST = os.environ.get("HOST", "127.0.0.1")
HERE = Path(__file__).parent
CACHE = {}  # url -> (zeit, daten)
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
            elif u.path == "/api/movers":
                self.send(200, json.dumps(movers()).encode(), "application/json")
            else:
                self.send(404, b"not found", "text/plain")
        except Exception as e:
            self.send(502, json.dumps({"error": str(e)}).encode(), "application/json")

if __name__ == "__main__":
    import threading, webbrowser
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"Aktien-Tracker laeuft auf http://localhost:{PORT}  (Fenster offen lassen, Strg+C beendet)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()
