"""Expiring sessions shared by workers, without a database."""
import hashlib
import json
import os
import time
from pathlib import Path
from library_etl.locking import data_lock


class SessionStore:
    TTL = 8 * 60 * 60

    def __init__(self, records_path):
        self.path = Path(records_path).resolve().with_name('admin-sessions.json')

    def _key(self, token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _load(self):
        try:
            values = json.loads(self.path.read_text())
            return {key: expiry for key, expiry in values.items() if expiry > time.time()}
        except FileNotFoundError:
            return {}

    def _save(self, values):
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(values, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def add(self, token):
        with data_lock(self.path, wait=True):
            values = self._load()
            values[self._key(token)] = time.time() + self.TTL
            self._save(values)

    def valid(self, token):
        with data_lock(self.path, wait=True):
            return self._key(token) in self._load()

    def discard(self, token):
        with data_lock(self.path, wait=True):
            values = self._load()
            values.pop(self._key(token), None)
            self._save(values)
