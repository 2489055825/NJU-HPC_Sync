from pathlib import Path

from app.models import Direction, SyncMode, SyncRequest
from app.paths import redact_text
from app.rsync_command import build_command


def test_upload_uses_directory_contents_and_argv_paths():
    request = SyncRequest("/tmp/my data/[中文]", "nju", "/fsb/home/user/project", Direction.UPLOAD, SyncMode.NORMAL)
    assert build_command(request) == ["rsync", "-avzP", "--stats", "--itemize-changes", "--", "/tmp/my data/[中文]/", "nju:/fsb/home/user/project/"]


def test_download_mirror_dry_run_does_not_swap_fields():
    request = SyncRequest("/tmp/destination", "nju", "/fsb/source", Direction.DOWNLOAD, SyncMode.MIRROR, True)
    assert build_command(request) == ["rsync", "-avzP", "--stats", "--itemize-changes", "--delete", "--dry-run", "--", "nju:/fsb/source/", "/tmp/destination/"]


def test_sync_counts_use_rsync_stats_and_include_empty_files():
    from app.main_window import MainWindow

    output = "空白文档.txt\nNumber of regular files transferred: 1\nNumber of deleted files: 2\n"
    assert MainWindow._sync_counts(output) == (1, 2)
    assert MainWindow._total_entries("Number of files: 3 (reg: 2, dir: 1)\n") == 3
    assert MainWindow._entry_breakdown("Number of files: 3 (reg: 2, dir: 1)\n") == (2, 1)
    assert MainWindow._changes(">f+++++++++ empty.txt\n>f.st...... updated.txt\n*deleting   old.txt\n") == [("add", "empty.txt"), ("update", "updated.txt"), ("delete", "old.txt")]


def test_redaction_handles_secrets_and_password_prompt():
    assert redact_text("Password: static 123456", ["static 123456"]) == "Password: [REDACTED]"
    assert redact_text("static 123456", ["static 123456"]) == "[REDACTED]"
