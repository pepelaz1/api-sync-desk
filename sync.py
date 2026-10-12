"""Small, bounded supplier API -> SQLite integration demo (Python stdlib)."""
import csv
import io
import json
import sqlite3
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation


def fetch_products(url, attempts=3, sleep=time.sleep):
    """Retry only transient failures, with bounded waits and response sizes."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError('API response exceeds 1 MB')
            data = json.loads(raw)
            if not isinstance(data, list) or len(data) > 5000:
                raise ValueError('Expected a list with at most 5000 products')
            return data
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt + 1 == attempts:
                raise
            sleep(min(2 ** attempt, 4))
        except urllib.error.URLError:
            if attempt + 1 == attempts:
                raise
            sleep(min(2 ** attempt, 4))
    raise ValueError('attempts must be positive')


def validate(rows):
    valid, errors, seen = [], [], set()
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValueError('expected an object')
            sku, name = row.get('sku'), row.get('name')
            if not isinstance(sku, str) or not sku.strip() or len(sku) > 80:
                raise ValueError('invalid sku')
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise ValueError('invalid name')
            if sku in seen:
                raise ValueError('duplicate sku in batch')
            price = Decimal(str(row.get('price')))
            if not price.is_finite() or price < 0 or price > 1_000_000 or price.as_tuple().exponent < -2:
                raise ValueError('invalid price (maximum two decimals)')
            currency = row.get('currency')
            if currency not in ('USD', 'EUR', 'GBP'):
                raise ValueError('unsupported currency')
            seen.add(sku)
            valid.append((sku, name, format(price, '.2f'), currency))
        except (ValueError, InvalidOperation) as exc:
            errors.append({'row': index, 'error': str(exc)})
    return valid, errors


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY,name TEXT NOT NULL,
        price TEXT NOT NULL,currency TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,created TEXT DEFAULT CURRENT_TIMESTAMP,
        inserted INTEGER,updated INTEGER,unchanged INTEGER,rejected INTEGER,errors TEXT);
    ''')
    return db


def sync(db, rows):
    valid, errors = validate(rows)
    counts = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'rejected': len(errors)}
    with db:
        for sku, name, price, currency in valid:
            old = db.execute('SELECT name,price,currency FROM products WHERE sku=?', (sku,)).fetchone()
            if old is None:
                db.execute('INSERT INTO products VALUES(?,?,?,?)', (sku, name, price, currency))
                counts['inserted'] += 1
            elif tuple(old) == (name, price, currency):
                counts['unchanged'] += 1
            else:
                db.execute('UPDATE products SET name=?,price=?,currency=? WHERE sku=?', (name, price, currency, sku))
                counts['updated'] += 1
        db.execute('INSERT INTO runs(inserted,updated,unchanged,rejected,errors) VALUES(?,?,?,?,?)',
                   (*counts.values(), json.dumps(errors)))
    return {**counts, 'errors': errors}


def snapshot(db):
    return {'products': [dict(x) for x in db.execute('SELECT * FROM products ORDER BY sku')],
            'runs': [dict(x) for x in db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 10')]}


def export_csv(db):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['sku', 'name', 'price', 'currency'])
    for row in db.execute('SELECT * FROM products ORDER BY sku'):
        writer.writerow([("'" + v if v.lstrip().startswith(('=', '+', '-', '@')) else v) for v in row])
    return out.getvalue()
