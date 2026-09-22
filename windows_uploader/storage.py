"""Per-user files and Windows Credential Manager access."""
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path


SERVICE = "YongsanLibraryUploader"


def app_home():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA 경로를 찾을 수 없습니다.")
    return Path(base) / SERVICE


class LocalStore:
    def __init__(self, home=None):
        self.home = Path(home) if home is not None else app_home()
        self.logs = self.home / "logs"
        self.backups = self.home / "backups"
        self.temp = self.home / "temp"
        for directory in (self.home, self.logs, self.backups, self.temp):
            directory.mkdir(parents=True, exist_ok=True)
        self.records = self.home / "records.json"
        self.config = self.home / "config.json"
        self.state = self.home / "state.json"

    @staticmethod
    def _write_json(path, value):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".write-", suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read_config(self):
        return json.loads(self.config.read_text(encoding="utf-8")) if self.config.exists() else {}

    def save_config(self, endpoint):
        self._write_json(self.config, {"endpoint": endpoint})

    def last_success(self):
        if not self.state.exists():
            return None
        return json.loads(self.state.read_text(encoding="utf-8")).get("last_success")

    def save_success(self, when=None):
        value = (when or datetime.now().astimezone()).isoformat(timespec="seconds")
        self._write_json(self.state, {"last_success": value})
        return value

    def backup(self, source):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.backups / f"records-{stamp}.json"
        try:
            with open(source, "rb") as reader, open(target, "xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def log(self, message):
        # Caller supplies fixed operational messages; reject credential-like text defensively.
        safe = re.sub(r"(?i)authorization\s*[:=]\s*\S+(?:\s+\S+)?", "Authorization: [마스킹]", str(message))
        safe = re.sub(r"(?i)bearer\s+\S+", "Bearer [마스킹]", safe)
        safe = re.sub(r"(?i)token\s*[:=]\s*\S+", "token: [마스킹]", safe)
        path = self.logs / "uploader.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            rotated = self.logs / "uploader.log.1"
            rotated.unlink(missing_ok=True)
            os.replace(path, rotated)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {safe}\n")


class Credentials:
    """Require the Windows Credential Manager backend; never fall back to file storage."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager가 필요합니다.")
        import keyring
        backend = keyring.get_keyring()
        if not backend.__class__.__module__.startswith("keyring.backends.Windows"):
            raise RuntimeError("Windows Credential Manager를 사용할 수 없습니다.")
        self.backend = backend

    def set(self, token):
        if not token or not token.strip():
            raise ValueError("토큰을 입력하세요.")
        self.backend.set_password(SERVICE, "upload-token", token)

    def get(self):
        return self.backend.get_password(SERVICE, "upload-token")

    def exists(self):
        return bool(self.get())
