from pathlib import Path

import pytest


@pytest.fixture
def ssh_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    directory = tmp_path / ".ssh"
    directory.mkdir()
    return directory


def test_reads_explicit_aliases_in_declaration_order(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "config").write_text(
        '# Host commented\n'
        'Host hpc-alias backup # trailing comment\n'
        '  HostName server.example.com\n'
        '  User researcher\n'
        'hOsT = "quoted-alias"\n'
        'Host=backup third\n'
        'Host * *.example.com node-? !excluded\n'
        'Host -unsafe invalid/path invalid:port "two words" " padded "\n',
        encoding="utf-8",
    )

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["hpc-alias", "backup", "quoted-alias", "third"]
    assert warnings == []


def test_include_supports_globs_nested_paths_and_multiple_files(ssh_dir, tmp_path):
    from app.ssh_config import read_ssh_hosts

    parts = ssh_dir / "conf.d"
    parts.mkdir()
    (parts / "20-second.conf").write_text("Host second\n", encoding="utf-8")
    (parts / "10-first.conf").write_text("Host first\nInclude nested.conf\n", encoding="utf-8")
    (ssh_dir / "nested.conf").write_text("Host nested\n", encoding="utf-8")
    extra = tmp_path / "with space.conf"
    extra.write_text("Host external\n", encoding="utf-8")
    (ssh_dir / "config").write_text(
        f'Host primary\nInclude = conf.d/*.conf "{extra}" ~/.ssh/nested.conf\nHost last\n',
        encoding="utf-8",
    )

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["primary", "first", "nested", "second", "external", "last"]
    assert warnings == []


def test_include_cycle_does_not_hide_remaining_hosts(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "config").write_text("Host root\nInclude child.conf\nHost tail\n", encoding="utf-8")
    (ssh_dir / "child.conf").write_text("Host child\nInclude config\n", encoding="utf-8")

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["root", "child", "tail"]


@pytest.mark.parametrize("problem", ["missing", "directory", "invalid-encoding"])
def test_unreadable_config_returns_feedback_without_crashing(ssh_dir, problem):
    from app.ssh_config import read_ssh_hosts

    config = ssh_dir / "config"
    if problem == "directory":
        config.mkdir()
    elif problem == "invalid-encoding":
        config.write_bytes(b"Host \xff\n")

    hosts, warnings = read_ssh_hosts()

    assert hosts == []
    assert warnings
    assert str(config) in warnings[0]


def test_malformed_relevant_line_does_not_echo_contents(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "config").write_text(
        'Host before\nInclude "private-value\nHost after\n', encoding="utf-8"
    )

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["before", "after"]
    assert warnings
    assert "private-value" not in "\n".join(warnings)


def test_discovery_does_not_execute_commands_or_modify_config(ssh_dir, tmp_path):
    from app.ssh_config import read_ssh_hosts

    marker = tmp_path / "must-not-exist"
    config = ssh_dir / "config"
    content = f'Match exec "touch {marker}"\nHost safe\n  ProxyCommand touch {marker}\n'
    config.write_text(content, encoding="utf-8")

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["safe"]
    assert warnings == []
    assert not marker.exists()
    assert config.read_text(encoding="utf-8") == content


def test_unmatched_optional_include_is_ignored(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "config").write_text("Include optional/*.conf\nHost safe\n", encoding="utf-8")

    assert read_ssh_hosts() == (["safe"], [])


def test_permission_error_in_include_is_reported(ssh_dir, monkeypatch):
    from app.ssh_config import read_ssh_hosts

    config = ssh_dir / "config"
    config.write_text("Include blocked.conf\nHost safe\n", encoding="utf-8")
    blocked = ssh_dir / "blocked.conf"
    blocked.write_text("Host hidden\n", encoding="utf-8")
    original_read = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("private detail")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["safe"]
    assert warnings
    assert str(blocked) in warnings[0]
    assert "private detail" not in warnings[0]


def test_embedded_hash_is_preserved_in_alias_and_include_filename(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "part#one.conf").write_text("Host included#alias\n", encoding="utf-8")
    (ssh_dir / "config").write_text(
        "Host root#alias # actual comment\nInclude part#one.conf # actual comment\n",
        encoding="utf-8",
    )

    assert read_ssh_hosts() == (["root#alias", "included#alias"], [])


def test_backslashes_cannot_manufacture_a_different_alias(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "config").write_text("Host bad\\name safe\n", encoding="utf-8")

    hosts, warnings = read_ssh_hosts()

    assert hosts == ["safe"]
    assert warnings == []


def test_include_handles_openssh_quoting_and_basic_escapes(ssh_dir):
    from app.ssh_config import read_ssh_hosts

    (ssh_dir / "with space.conf").write_text("Host space-alias\n", encoding="utf-8")
    (ssh_dir / 'with"quote.conf').write_text("Host quote-alias\n", encoding="utf-8")
    (ssh_dir / "literal\\name.conf").write_text("Host slash-alias\n", encoding="utf-8")
    (ssh_dir / "config").write_text(
        'Host \'single-quoted\'\nInclude with\\ space.conf "with\\"quote.conf" literal\\name.conf\n',
        encoding="utf-8",
    )

    assert read_ssh_hosts() == (["single-quoted", "space-alias", "quote-alias", "slash-alias"], [])
