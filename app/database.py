from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import HistoryRecord, Profile, utc_now


DEFAULT_LOG_RETENTION_DAYS = 60
LOG_RETENTION_KEY = "log_retention_days"


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path.home() / ".local" / "share" / "nju-hpc-sync" / "nju-hpc-sync.sqlite3").expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                local_path TEXT NOT NULL,
                remote_host TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                credential_name TEXT NOT NULL DEFAULT '',
                default_direction TEXT NOT NULL DEFAULT 'upload',
                default_mode TEXT NOT NULL DEFAULT 'normal',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL DEFAULT '',
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                local_path TEXT NOT NULL,
                remote_host TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                direction TEXT NOT NULL,
                mode TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                exit_code INTEGER,
                duration REAL NOT NULL DEFAULT 0,
                log TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_start ON history(start_time DESC);
            """
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (LOG_RETENTION_KEY, str(DEFAULT_LOG_RETENTION_DAYS)),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def list_profiles(self) -> list[Profile]:
        rows = self._connection.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE").fetchall()
        return [self._profile(row) for row in rows]

    def get_profile(self, profile_id: int) -> Profile | None:
        row = self._connection.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return self._profile(row) if row else None

    def save_profile(self, profile: Profile) -> Profile:
        now = utc_now()
        if profile.id is None:
            cursor = self._connection.execute(
                "INSERT INTO profiles(name, local_path, remote_host, remote_path, credential_name, default_direction, default_mode, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (profile.name.strip(), profile.local_path, profile.remote_host, profile.remote_path, profile.credential_name, profile.default_direction, profile.default_mode, now, now),
            )
            profile.id = int(cursor.lastrowid)
            profile.created_at = profile.updated_at = now
        else:
            self._connection.execute(
                "UPDATE profiles SET name=?, local_path=?, remote_host=?, remote_path=?, credential_name=?, default_direction=?, default_mode=?, updated_at=? WHERE id=?",
                (profile.name.strip(), profile.local_path, profile.remote_host, profile.remote_path, profile.credential_name, profile.default_direction, profile.default_mode, now, profile.id),
            )
            profile.updated_at = now
        self._connection.commit()
        return profile

    def delete_profile(self, profile_id: int) -> None:
        self._connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self._connection.commit()

    def add_history(self, record: HistoryRecord) -> int:
        cursor = self._connection.execute(
            "INSERT INTO history(profile_name,start_time,end_time,local_path,remote_host,remote_path,direction,mode,dry_run,status,exit_code,duration,log) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.profile_name, record.start_time, record.end_time, record.local_path, record.remote_host, record.remote_path, record.direction, record.mode, int(record.dry_run), record.status, record.exit_code, record.duration, record.log),
        )
        self._connection.commit()
        record.id = int(cursor.lastrowid)
        return record.id

    def list_history(self, limit: int = 200) -> list[HistoryRecord]:
        rows = self._connection.execute("SELECT * FROM history ORDER BY start_time DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [self._history(row) for row in rows]

    def get_history(self, record_id: int) -> HistoryRecord | None:
        row = self._connection.execute("SELECT * FROM history WHERE id = ?", (record_id,)).fetchone()
        return self._history(row) if row else None

    def get_log_retention_days(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (LOG_RETENTION_KEY,)
        ).fetchone()
        try:
            days = int(row["value"]) if row else DEFAULT_LOG_RETENTION_DAYS
        except (TypeError, ValueError):
            return DEFAULT_LOG_RETENTION_DAYS
        return days if days > 0 else DEFAULT_LOG_RETENTION_DAYS

    def set_log_retention_days(self, days: int) -> None:
        days = int(days)
        if days < 1:
            raise ValueError("日志保留天数必须大于 0")
        self._connection.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (LOG_RETENTION_KEY, str(days)),
        )
        self._connection.commit()

    def cleanup_history(self, now: datetime | None = None) -> int:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        cutoff = reference.astimezone(timezone.utc) - timedelta(days=self.get_log_retention_days())
        cursor = self._connection.execute(
            "DELETE FROM history WHERE start_time <> '' AND start_time < ?",
            (cutoff.isoformat(timespec="seconds"),),
        )
        self._connection.commit()
        return max(cursor.rowcount, 0)

    @staticmethod
    def _profile(row: sqlite3.Row) -> Profile:
        return Profile(**dict(row))

    @staticmethod
    def _history(row: sqlite3.Row) -> HistoryRecord:
        values = dict(row)
        values["dry_run"] = bool(values["dry_run"])
        return HistoryRecord(**values)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
