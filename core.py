import ctypes as C
import hashlib
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import qrcode


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.qr_dir = self.directory / 'credentials'
        self.qr_dir.mkdir(exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(self.directory / 'checkin.sqlite3'))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS people (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, employee TEXT NOT NULL,
                department TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL,
                last_seen TEXT, qr_path TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS employee_unique
                ON people(employee) WHERE employee <> '';
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, happened TEXT NOT NULL,
                person_id TEXT REFERENCES people(id), name TEXT NOT NULL,
                employee TEXT NOT NULL, result TEXT NOT NULL, reason TEXT NOT NULL);
        ''')
        self.db.commit()
        os.chmod(str(self.directory / 'checkin.sqlite3'), 0o600)

    def enroll(self, name, employee='', department=''):
        name, employee, department = name.strip(), employee.strip(), department.strip()
        if not name or len(name) > 40:
            raise ValueError('姓名不能为空，且不能超过 40 个字。')
        if len(employee) > 32 or len(department) > 40:
            raise ValueError('编号最多 32 个字符，部门最多 40 个字。')
        person_id = uuid.uuid4().hex
        token = 'LMC1.' + secrets.token_urlsafe(32)
        path = self.qr_dir / (person_id + '.png')
        qrcode.make(token).save(str(path))
        os.chmod(str(path), 0o600)
        try:
            with self.db:
                self.db.execute('INSERT INTO people VALUES (?,?,?,?,?,1,?,NULL,?)',
                    (person_id, name, employee, department,
                     hashlib.sha256(token.encode()).hexdigest(), now(), str(path)))
        except Exception:
            path.unlink()
            raise
        return person_id

    def people(self):
        return self.db.execute('SELECT * FROM people ORDER BY created DESC, rowid DESC').fetchall()

    def person(self, person_id):
        return self.db.execute('SELECT * FROM people WHERE id=?', (person_id,)).fetchone()

    def set_enabled(self, person_id, enabled):
        with self.db:
            self.db.execute('UPDATE people SET enabled=? WHERE id=?', (int(enabled), person_id))

    def authorize(self, payload):
        person = None
        if re.fullmatch(r'LMC1\.[A-Za-z0-9_-]{43}', payload):
            digest = hashlib.sha256(payload.encode()).hexdigest()
            person = self.db.execute('SELECT * FROM people WHERE token_hash=?', (digest,)).fetchone()
        passed = bool(person and person['enabled'])
        reason = '验证成功' if passed else ('凭证已撤销' if person else '凭证未登记')
        stamp = now()
        with self.db:
            self.db.execute('INSERT INTO events(happened,person_id,name,employee,result,reason) VALUES(?,?,?,?,?,?)',
                (stamp, person['id'] if person else None, person['name'] if person else '未知人员',
                 person['employee'] if person else '', '通过' if passed else '未授权', reason))
            if passed:
                self.db.execute('UPDATE people SET last_seen=? WHERE id=?', (stamp, person['id']))
        return passed, (person['name'] if passed else ''), reason

    def events(self, limit=200):
        return self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()

    def all_events(self):
        return self.db.execute('SELECT * FROM events ORDER BY id DESC')

    def counts(self):
        return (self.db.execute('SELECT COUNT(*) FROM people WHERE enabled=1').fetchone()[0],
                self.db.execute("SELECT COUNT(*) FROM events WHERE result='通过'").fetchone()[0])


class QRScanner:
    def __init__(self):
        self.z = C.CDLL('libzbar.so.0')
        signatures = {
            'zbar_image_scanner_create': ([], C.c_void_p),
            'zbar_image_scanner_destroy': ([C.c_void_p], None),
            'zbar_image_scanner_set_config': ([C.c_void_p, C.c_int, C.c_int, C.c_int], C.c_int),
            'zbar_image_create': ([], C.c_void_p),
            'zbar_image_destroy': ([C.c_void_p], None),
            'zbar_image_set_format': ([C.c_void_p, C.c_ulong], None),
            'zbar_image_set_size': ([C.c_void_p, C.c_uint, C.c_uint], None),
            'zbar_image_set_data': ([C.c_void_p, C.c_void_p, C.c_ulong, C.c_void_p], None),
            'zbar_scan_image': ([C.c_void_p, C.c_void_p], C.c_int),
            'zbar_image_first_symbol': ([C.c_void_p], C.c_void_p),
            'zbar_symbol_next': ([C.c_void_p], C.c_void_p),
            'zbar_symbol_get_data': ([C.c_void_p], C.c_void_p),
            'zbar_symbol_get_data_length': ([C.c_void_p], C.c_uint),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.z, name)
            fn.argtypes, fn.restype = args, result
        self.scanner = self.z.zbar_image_scanner_create()
        self.z.zbar_image_scanner_set_config(self.scanner, 0, 0, 0)
        self.z.zbar_image_scanner_set_config(self.scanner, 64, 0, 1)

    def decode(self, gray):
        gray = np.ascontiguousarray(gray, dtype=np.uint8)
        image = self.z.zbar_image_create()
        try:
            self.z.zbar_image_set_format(image, int.from_bytes(b'Y800', 'little'))
            self.z.zbar_image_set_size(image, gray.shape[1], gray.shape[0])
            self.z.zbar_image_set_data(image, gray.ctypes.data, gray.nbytes, None)
            self.z.zbar_scan_image(self.scanner, image)
            symbol = self.z.zbar_image_first_symbol(image)
            found = []
            while symbol:
                size = self.z.zbar_symbol_get_data_length(symbol)
                if size <= 512:
                    found.append(C.string_at(self.z.zbar_symbol_get_data(symbol), size).decode('utf-8', 'replace'))
                symbol = self.z.zbar_symbol_next(symbol)
            return found
        finally:
            self.z.zbar_image_destroy(image)

    def close(self):
        if self.scanner:
            self.z.zbar_image_scanner_destroy(self.scanner)
            self.scanner = None
