"""One-shot Kite OAuth helper.

Run this script, open the printed URL in a browser, log in to Zerodha,
and the access token is written to .env automatically.
"""
import os
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_FILE)

API_KEY = os.environ["KITE_API_KEY"]
API_SECRET = os.environ["KITE_API_SECRET"]
PORT = 5050

kite = KiteConnect(api_key=API_KEY)
login_url = kite.login_url()

print(f"\nOpening browser for Kite login...")
print(f"URL: {login_url}\n")
webbrowser.open(login_url)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        request_token = params.get("request_token", [None])[0]

        if not request_token:
            self._respond(400, "No request_token in callback.")
            return

        try:
            session = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = session["access_token"]
        except Exception as e:
            self._respond(500, f"Token exchange failed: {e}")
            raise SystemExit(1)

        set_key(ENV_FILE, "KITE_ACCESS_TOKEN", access_token)
        print(f"Access token saved to .env: {access_token[:8]}...")
        self._respond(200, "Login successful. You can close this tab and restart the server.")
        raise SystemExit(0)

    def _respond(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode())

    def log_message(self, *args):
        pass


print(f"Waiting for Zerodha redirect on http://127.0.0.1:{PORT} ...")
HTTPServer(("127.0.0.1", PORT), Handler).handle_request()
