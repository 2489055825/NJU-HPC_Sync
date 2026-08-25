from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import Credential


class CredentialStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path.home() / ".config" / "nju-hpc-sync" / "credentials.json").expanduser()

    def load(self) -> dict[str, Credential]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取凭据文件：{exc}") from exc
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
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"凭据“{name}”的参数格式无效") from exc
        return result

    def save(self, credentials: dict[str, Credential]) -> None:
        for credential in credentials.values():
            credential.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
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
        fd, temporary = tempfile.mkstemp(prefix="credentials.", suffix=".tmp", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def delete(self, name: str) -> None:
        credentials = self.load()
        credentials.pop(name, None)
        self.save(credentials)
