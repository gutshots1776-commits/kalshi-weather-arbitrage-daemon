import json
import os
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "ok": True,
    "service": "kalshi-weather-arbitrage-daemon",
    "started_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "bot_thread_alive": False,
    "last_error": None,
    "last_error_utc": None,
}

def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def run_bot():
    try:
        import kalshi_unified
        kalshi_unified.main()
    except Exception as exc:
        STATE["ok"] = False
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        STATE["last_error_utc"] = utc_now()
        traceback.print_exc()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path not in {"/", "/health"}:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "not_found"}).encode())
            return

        STATE["bot_thread_alive"] = bool(BOT_THREAD.is_alive())
        if not STATE["bot_thread_alive"]:
            STATE["ok"] = False

        body = json.dumps(STATE, sort_keys=True).encode()
        self.send_response(200 if STATE.get("ok") else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return

if __name__ == "__main__":
    BOT_THREAD = threading.Thread(target=run_bot, name="kalshi-daemon", daemon=True)
    BOT_THREAD.start()

    port = int(os.environ.get("PORT", "10000"))
    print(f"render_web listening on port {port}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
