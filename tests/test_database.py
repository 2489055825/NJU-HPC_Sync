from datetime import datetime, timezone

from app.database import Database
from app.models import Direction, HistoryRecord, Profile, RunStatus, SyncMode


def test_default_database_path_uses_nju_hpc_sync_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("app.database.Path.home", lambda: tmp_path)
    db = Database()
    assert db.path == tmp_path / ".local" / "share" / "nju-hpc-sync" / "nju-hpc-sync.sqlite3"
    db.close()


def test_profiles_and_history_round_trip(tmp_path):
    db = Database(tmp_path / "app.sqlite3")
    profile = db.save_profile(Profile("NJU test", "/tmp/a", "nju", "/fsb/a", "nju"))
    assert db.get_profile(profile.id).name == "NJU test"
    record = HistoryRecord("NJU test", "/tmp/a", "nju", "/fsb/a", Direction.UPLOAD.value, SyncMode.NORMAL.value, False, RunStatus.SUCCESS.value, 0, 1.25, "safe log", start_time="2026-01-01T00:00:00+00:00", end_time="2026-01-01T00:00:01+00:00")
    db.add_history(record)
    assert db.list_history()[0].log == "safe log"
    db.close()


def test_log_retention_defaults_to_60_days_and_cleans_expired_history(tmp_path):
    path = tmp_path / "app.sqlite3"
    db = Database(path)
    assert db.get_log_retention_days() == 60
    for start_time in ("2026-06-23T00:00:00+00:00", "2026-06-25T00:00:00+00:00"):
        db.add_history(HistoryRecord("", "/tmp/a", "host", "/remote/a", Direction.UPLOAD.value, SyncMode.NORMAL.value, False, RunStatus.SUCCESS.value, 0, 1.0, "safe log", start_time=start_time, end_time=start_time))

    deleted = db.cleanup_history(datetime(2026, 8, 23, tzinfo=timezone.utc))

    assert deleted == 1
    assert [record.start_time for record in db.list_history()] == ["2026-06-25T00:00:00+00:00"]
    db.set_log_retention_days(30)
    db.close()
    reopened = Database(path)
    assert reopened.get_log_retention_days() == 30
    reopened.close()
