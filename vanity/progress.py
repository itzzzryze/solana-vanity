"""Transactional checkpoints and recoverable JSON result files."""
from contextlib import closing, contextmanager
import errno
from hashlib import sha256
import json
import os
from secrets import token_hex
import sqlite3
import sys
from tempfile import NamedTemporaryFile

CHECKPOINT_SECONDS = 5


@contextmanager
def exclusive_search(path):
    with path.open("a+b") as file:
        try:
            if sys.platform == "win32":
                import msvcrt

                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise RuntimeError("This search is already open in another process") from error
            raise
        yield


def write_result(path, document):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                            suffix=".tmp", delete=False) as file:
        temp = file.name
        try:
            json.dump(document, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
            file.close()
            os.replace(temp, path)
        finally:
            file.close()
            if os.path.exists(temp):
                os.unlink(temp)


class Progress:
    def __init__(self, database, folder, key, base, patterns):
        self.database, self.folder, self.key = database, folder, key
        self.base, self.patterns = str(base), patterns
        self.folder.mkdir(parents=True, exist_ok=True)
        with database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    key TEXT PRIMARY KEY, tag TEXT NOT NULL, next_offset TEXT NOT NULL
                )
            """)
            database.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    key TEXT NOT NULL, pattern INTEGER NOT NULL, seed TEXT NOT NULL, address TEXT NOT NULL,
                    UNIQUE (key, pattern, seed)
                )
            """)
            database.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, '0')", (key, token_hex(8)))
        self.tag, offset = database.execute("SELECT tag, next_offset FROM sessions WHERE key = ?", (key,)).fetchone()
        self.offset = int(offset)
        if len(self.tag) != 16 or any(c not in "0123456789abcdef" for c in self.tag) or not 0 <= self.offset <= 2**64:
            raise RuntimeError("Invalid search checkpoint")
        self.hits = {index: [] for index in range(len(patterns))}
        for index, seed, address in database.execute(
            "SELECT pattern, seed, address FROM results WHERE key = ? ORDER BY rowid", (key,)
        ):
            self.hits[index].append((seed, address))
        for index, entries in self.hits.items():
            if entries:
                self.export(index)

    def export(self, index):
        label = self.patterns[index].label
        slug = "".join(c if c.isascii() and c.isalnum() else "_" for c in label).strip("_")[:48]
        path = self.folder / f"{index + 1:03d}-{slug or 'pattern'}.json"
        write_result(path, {
            "pattern": label,
            "base_address": self.base,
            "owner": "11111111111111111111111111111111",
            "matches": [{"receiving_address": address, "public_derivation_seed": seed}
                        for seed, address in self.hits[index]],
        })

    def save(self, offset, pending):
        with self.database:
            self.database.execute("UPDATE sessions SET next_offset = ? WHERE key = ?", (str(offset), self.key))
            self.database.executemany("INSERT INTO results VALUES (?, ?, ?, ?)", (
                (self.key, index, seed, address)
                for index, entries in pending.items() for seed, address in entries
            ))
        self.offset = offset
        # The database is authoritative. A later launch recreates exports if writing one is interrupted.
        for index, entries in pending.items():
            self.hits[index].extend(entries)
            self.export(index)


@contextmanager
def open_progress(directory, base, patterns):
    config = json.dumps([str(base), [pattern.label for pattern in patterns]], separators=(",", ":"))
    key = sha256(("seeded-solana-v1:" + config).encode()).hexdigest()
    folder = directory / "results" / f"{base}-{key[:12]}"
    with exclusive_search(directory / f".vanity-{key[:16]}.lock"):
        with closing(sqlite3.connect(directory / "vanity-progress.sqlite3")) as database:
            database.execute("PRAGMA synchronous = FULL")
            yield Progress(database, folder, key, base, patterns)
