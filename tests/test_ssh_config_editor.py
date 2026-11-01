import os
import stat

import pytest


def test_missing_config_loads_as_empty_and_has_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    from app.ssh_config_editor import config_snapshot, load_ssh_config, ssh_config_path

    assert ssh_config_path() == tmp_path / ".ssh" / "config"
    assert load_ssh_config() == ""
    assert config_snapshot() is None


def test_validation_accepts_open_ssh_directives_and_rejects_malformed_lines():
    from app.ssh_config_editor import validate_ssh_config

    assert validate_ssh_config(
        "# comment\nHost alpha # inline comment\nHostName cluster.example\n"
        "Include conf.d/*.conf\nUnknownDirective arbitrary value\n"
    ) == []

    errors = validate_ssh_config("Host\nHost \"unterminated\n")

    assert len(errors) == 2
    assert "第 1 行" in errors[0]
    assert "第 2 行" in errors[1]


def test_save_creates_backup_preserves_existing_mode_and_updates_snapshot(tmp_path):
    from app.ssh_config_editor import config_snapshot, save_ssh_config

    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    config = ssh_dir / "config"
    config.write_text("Host old\n", encoding="utf-8")
    os.chmod(config, 0o640)
    old_snapshot = config_snapshot(config)

    backup = save_ssh_config("Host new\n", config)

    assert backup == ssh_dir / "config.bak"
    assert backup.read_text(encoding="utf-8") == "Host old\n"
    assert config.read_text(encoding="utf-8") == "Host new\n"
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert stat.S_IMODE(backup.stat().st_mode) == 0o640
    assert config_snapshot(config) != old_snapshot


def test_save_creates_private_config_and_ssh_directory(tmp_path):
    from app.ssh_config_editor import save_ssh_config

    config = tmp_path / ".ssh" / "config"

    assert save_ssh_config("Host new\n", config) is None

    assert config.read_text(encoding="utf-8") == "Host new\n"
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_invalid_text_is_rejected_without_creating_or_changing_file(tmp_path):
    from app.ssh_config_editor import save_ssh_config

    config = tmp_path / ".ssh" / "config"
    with pytest.raises(ValueError, match="第 1 行"):
        save_ssh_config("Host\n", config)
    assert not config.exists()

    config.parent.mkdir()
    config.write_text("Host existing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="UTF-8"):
        save_ssh_config("bad\ud800", config)
    assert config.read_text(encoding="utf-8") == "Host existing\n"


def test_replace_failure_leaves_original_file_untouched(tmp_path, monkeypatch):
    from app import ssh_config_editor

    config = tmp_path / ".ssh" / "config"
    config.parent.mkdir()
    config.write_text("Host original\n", encoding="utf-8")

    original_replace = ssh_config_editor.os.replace

    def fail_replace(source, destination):
        if destination == config:
            raise OSError("replace failed")
        return original_replace(source, destination)

    monkeypatch.setattr(ssh_config_editor.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        ssh_config_editor.save_ssh_config("Host replacement\n", config)
    assert config.read_text(encoding="utf-8") == "Host original\n"
    assert (config.parent / "config.bak").read_text(encoding="utf-8") == "Host original\n"


def test_backup_symlink_does_not_write_to_its_target(tmp_path):
    from app.ssh_config_editor import save_ssh_config

    config = tmp_path / "config"
    config.write_text("Host original\n", encoding="utf-8")
    unrelated = tmp_path / "untouched.txt"
    unrelated.write_text("unrelated data", encoding="utf-8")
    backup = tmp_path / "config.bak"
    backup.symlink_to(unrelated)

    save_ssh_config("Host changed\n", config)

    assert unrelated.read_text(encoding="utf-8") == "unrelated data"
    assert not backup.is_symlink()
    assert backup.read_text(encoding="utf-8") == "Host original\n"


def test_external_change_during_save_is_rejected(tmp_path, monkeypatch):
    from app import ssh_config_editor

    config = tmp_path / "config"
    config.write_text("Host original\n", encoding="utf-8")
    snapshot = ssh_config_editor.config_snapshot(config)
    original_fsync = ssh_config_editor.os.fsync

    def change_after_write(descriptor):
        original_fsync(descriptor)
        config.write_text("Host external-change\n", encoding="utf-8")

    monkeypatch.setattr(ssh_config_editor.os, "fsync", change_after_write)

    with pytest.raises(ValueError, match="变化"):
        ssh_config_editor.save_ssh_config("Host changed\n", config, expected_snapshot=snapshot)
    assert config.read_text(encoding="utf-8") == "Host external-change\n"


def test_chmod_failure_cleans_temporary_files(tmp_path, monkeypatch):
    from app import ssh_config_editor

    config = tmp_path / "config"
    descriptors = []

    def fail_chmod(descriptor, _mode):
        descriptors.append(descriptor)
        raise OSError("chmod failed")

    monkeypatch.setattr(ssh_config_editor.os, "fchmod", fail_chmod)
    with pytest.raises(OSError, match="chmod failed"):
        ssh_config_editor.save_ssh_config("Host new\n", config)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_config_symlink_is_not_replaced(tmp_path):
    from app.ssh_config_editor import save_ssh_config

    target = tmp_path / "actual.conf"
    target.write_text("Host actual\n", encoding="utf-8")
    config = tmp_path / "config"
    config.symlink_to(target)

    with pytest.raises(ValueError, match="符号链接"):
        save_ssh_config("Host changed\n", config)
    assert config.is_symlink()
    assert target.read_text(encoding="utf-8") == "Host actual\n"


def test_config_created_since_load_cannot_be_overwritten(tmp_path):
    from app.ssh_config_editor import save_ssh_config

    config = tmp_path / "config"
    config.write_text("Host externally-created\n", encoding="utf-8")

    with pytest.raises(ValueError, match="变化"):
        save_ssh_config("Host from-editor\n", config, expected_snapshot=None)
    assert config.read_text(encoding="utf-8") == "Host externally-created\n"


def test_backup_failure_preserves_config_and_cleans_temporary_files(tmp_path, monkeypatch):
    from app import ssh_config_editor

    config = tmp_path / "config"
    config.write_text("Host original\n", encoding="utf-8")
    original_replace = ssh_config_editor.os.replace

    def fail_backup(source, destination):
        if destination == config.with_name("config.bak"):
            raise PermissionError("backup failed")
        return original_replace(source, destination)

    monkeypatch.setattr(ssh_config_editor.os, "replace", fail_backup)

    with pytest.raises(PermissionError):
        ssh_config_editor.save_ssh_config("Host new\n", config)
    assert config.read_text(encoding="utf-8") == "Host original\n"
    assert list(tmp_path.iterdir()) == [config]


def test_null_bytes_are_rejected_before_saving(tmp_path):
    from app.ssh_config_editor import save_ssh_config

    config = tmp_path / "config"
    with pytest.raises(ValueError, match="第 1 行"):
        save_ssh_config("Host bad\x00name\n", config)
    assert not config.exists()
