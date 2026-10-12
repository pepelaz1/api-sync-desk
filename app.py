"""Loopback-only dashboard and deterministic local mock supplier API."""
import argparse
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from sync import connect, export_csv, fetch_products, snapshot, sync

ROOT = Path(__file__).parent
SAMPLES = [
    {'sku': 'K-100', 'name': 'Mechanical keyboard', 'price': '89.90', 'currency': 'USD'},
    {'sku': 'M-200', 'name': 'Wireless mouse', 'price': '24.50', 'currency': 'USD'},
    {'sku': 'D-300', 'name': 'USB-C dock', 'price': '69.00', 'currency': 'EUR'},
    {'sku': 'BAD', 'name': 'Invalid sample', 'price': '-5.00', 'currency': 'USD'},
    {'sku': 'K-100', 'name': 'Duplicate sample', 'price': '89.90', 'currency': 'USD'},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--db', default=str(ROOT / 'demo.sqlite'))
    args = parser.parse_args()
    mock = HTTPServer(('127.0.0.1', 0), Mock)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    supplier = f'http://127.0.0.1:{mock.server_port}/products'
    cache = {}

    class Dashboard(BaseHTTPRequestHandler):
        def send(self, status, data, mime='application/json'):
            raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/':
                self.send(200, (ROOT / 'index.html').read_text(encoding='utf-8'), 'text/html')
            elif self.path in ('/api/state', '/export.csv'):
                with connect(args.db) as db:
                    if self.path == '/api/state':
                        self.send(200, snapshot(db))
                    else:
                        self.send(200, export_csv(db), 'text/csv')
            else:
                self.send(404, {'error': 'not found'})

        def do_POST(self):
            # Local demo: reject browser cross-origin writes and arbitrary targets.
            origin = self.headers.get('Origin')
            if origin and origin != f'http://127.0.0.1:{args.port}':
                self.send(403, {'error': 'origin rejected'})
                return
            if self.path != '/api/sync':
                self.send(404, {'error': 'not found'})
                return
            import time
            try:
                hit = cache.get('expires', 0) > time.monotonic()
                rows = cache['rows'] if hit else fetch_products(supplier)
                if not hit:
                    cache.update(rows=rows, expires=time.monotonic() + 10)
                with connect(args.db) as db:
                    result = sync(db, rows)
                self.send(200, {**result, 'cache_hit': hit, 'mock_requests': Mock.calls})
            except (ValueError, urllib.error.URLError) as exc:
                self.send(502, {'error': str(exc)})

    try:
        server = HTTPServer(('127.0.0.1', args.port), Dashboard)
        print(f'Dashboard: http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
    finally:
        mock.shutdown()


class Mock(BaseHTTPRequestHandler):
    calls = 0
    def do_GET(self):
        Mock.calls += 1
        if Mock.calls == 1:
            self.send_response(503)
            self.end_headers()
            return
        raw = json.dumps(SAMPLES).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == '__main__':
    main()
