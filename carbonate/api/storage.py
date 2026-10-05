"""Private, content-addressed evidence storage and pilot application database."""
import hashlib
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def sha256(data):
    return hashlib.sha256(data).hexdigest()

class PrivateStorage:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(exist_ok=True, mode=0o700)
        os.chmod(self.objects, 0o700)

    def put(self, raw):
        digest = sha256(raw)
        target = self.objects / digest
        # Atomic hard-link publication: readers never observe a partial object.
        import tempfile
        fd, temporary = tempfile.mkstemp(dir=self.objects, prefix=".intake-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o400)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != raw:
                    raise ValueError("private object hash collision or corruption")
        finally:
            os.unlink(temporary)
        return digest

    def get(self, digest):
        import re
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("invalid private object digest")
        raw = (self.objects / digest).read_bytes()
        if sha256(raw) != digest:
            raise ValueError("private evidence integrity check failed")
        return raw

    def connect(self):
        target = self.root / "application.sqlite3"
        # Establish restrictive permissions before SQLite first opens the file.
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(target, 0o600)
        db = sqlite3.connect(target, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((ROOT / "database/migrations/001_generation.sql").read_text())
        return db
