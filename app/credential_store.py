from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .models import Credential

_UNCHECKED = object()


class CredentialStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path.home() / ".config" / "nju-hpc-sync" / "credentials.json").expanduser()

    def load(self) -> dict[str, Credential]:
        return self.load_state()[0]

    def load_state(self) -> tuple[dict[str, Credential], str | None]:
        try:
            raw = self.path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return {}, None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("无法读取凭据文件，请检查文件权限、UTF-8 编码和 JSON 格式。") from exc
        if not isinstance(data, dict):
            raise RuntimeError("凭据文件格式必须是 JSON 对象")
        result: dict[str, Credential] = {}
        for name, value in data.items():
            if not isinstance(value, dict):
                continue
            try:
                result[name] = Credential(
                    name=name,
                    static_password=str(value.get("password", "")),
                    totp_secret=str(value.get("totp_secret", "")),
                    totp_algorithm=str(value.get("totp_algorithm", "SHA1")).upper(),
                    totp_period=int(value.get("totp_period", 30)),
                    totp_digits=int(value.get("totp_digits", 6)),
                )
                result[name].validate()
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(f"凭据“{name}”的参数格式无效") from exc
        return result, hashlib.sha256(raw).hexdigest()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _check_snapshot(self, expected: str | None | object) -> None:
        if expected is not _UNCHECKED and self.load_state()[1] != expected:
            raise RuntimeError("凭据文件已被其他窗口修改，内容发生变化；请重新打开凭据管理后再保存。当前编辑内容已保留。")

    def save(self, credentials: dict[str, Credential], *, expected_snapshot: str | None | object = _UNCHECKED) -> str:
        with self._locked():
            self._check_snapshot(expected_snapshot)
            return self._write(credentials)

    def rename(
        self, credentials: dict[str, Credential], previous_name: str,
        update_references: Callable[[], None], *, expected_snapshot: str | None,
    ) -> tuple[dict[str, Credential], str, str]:
        """Keep both names valid while Profile references move to the new name."""
        with self._locked():
            self._check_snapshot(expected_snapshot)
            original = self.load()
            staged = {**credentials, previous_name: original[previous_name]}
            snapshot = self._write(staged)
            try:
                update_references()
            except Exception:
                self._write(original)
                raise
            try:
                snapshot = self._write(credentials)
            except OSError:
                return staged, snapshot, "凭据改名及 Profile 更新已完成，但旧名称未能清理；两个名称均已保留，可稍后删除旧名称。"
            return credentials, snapshot, ""

    def _write(self, credentials: dict[str, Credential]) -> str:
        for credential in credentials.values():
            credential.validate()
        payload = {
            name: {
                "password": credential.static_password,
                "totp_secret": credential.totp_secret,
                "totp_algorithm": credential.totp_algorithm.upper(),
                "totp_period": int(credential.totp_period),
                "totp_digits": int(credential.totp_digits),
            }
            for name, credential in credentials.items()
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix="credentials.", suffix=".tmp", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            # The temporary file is already private; replacement commits the save.
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def delete(self, name: str) -> None:
        credentials, snapshot = self.load_state()
        credentials.pop(name, None)
        self.save(credentials, expected_snapshot=snapshot)
