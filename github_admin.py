from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


OWNER = "erisonfavoritos"
REPOSITORY = "youtube-downloader-pro-releases"
API_ROOT = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}"
MANIFEST_PATH = "update.json"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, object]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "YouTubeDownloaderPRO Admin", None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def unprotect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


class TokenStore:
    def __init__(self):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.path = local / "YouTubeDownloaderPRO" / "admin_token.bin"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, token: str) -> None:
        self.path.write_bytes(protect(token.strip().encode("utf-8")))

    def load(self) -> str:
        try:
            return unprotect(self.path.read_bytes()).decode("utf-8")
        except Exception:
            return ""

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True)
class PublishedRelease:
    version: str
    url: str
    download_url: str
    sha256: str


class GitHubAdmin:
    def __init__(self, token: str):
        self.token = token.strip()

    def _request(self, method: str, url: str, body=None, headers=None, raw=False):
        request_headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "YouTubeDownloaderPRO-Manager",
        }
        request_headers.update(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read()
                if raw:
                    return payload
                return json.loads(payload or b"{}")
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"GitHub respondeu {error.code}: {details[:800]}") from error

    def validate(self) -> str:
        repo = self._request("GET", API_ROOT)
        permissions = repo.get("permissions") or {}
        if not permissions.get("push"):
            raise RuntimeError("o token não possui permissão de gravação neste repositório")
        return str(repo.get("full_name") or "")

    def get_manifest(self) -> tuple[dict, str]:
        data = self._request("GET", f"{API_ROOT}/contents/{MANIFEST_PATH}?ref=main")
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), str(data["sha"])

    def get_release_by_version(self, version: str) -> dict | None:
        tag = "v" + version.strip().lstrip("v")
        try:
            return self._request("GET", f"{API_ROOT}/releases/tags/{urllib.parse.quote(tag)}")
        except RuntimeError as error:
            if "GitHub respondeu 404" in str(error):
                return None
            raise

    def update_manifest(self, manifest: dict, sha: str, message: str) -> None:
        encoded = base64.b64encode(
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii")
        self._request("PUT", f"{API_ROOT}/contents/{MANIFEST_PATH}", {
            "message": message, "content": encoded, "sha": sha, "branch": "main",
        })

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def publish_release(self, executable: Path, version: str, notes: list[str], prerelease=False, progress=None) -> PublishedRelease:
        version = version.strip().lstrip("v")
        tag = f"v{version}"
        if not executable.is_file() or executable.suffix.lower() != ".exe":
            raise RuntimeError("selecione um executável válido")
        digest = self.sha256(executable)
        release = self._request("POST", f"{API_ROOT}/releases", {
            "tag_name": tag,
            "target_commitish": "main",
            "name": f"YouTube Downloader PRO {tag}",
            "body": "\n".join(f"- {note}" for note in notes if note.strip()),
            "draft": True,
            "prerelease": bool(prerelease),
        })
        release_id = release["id"]
        upload_url = str(release["upload_url"]).split("{", 1)[0]
        try:
            content = executable.read_bytes()
            if progress:
                progress(30)
            asset = self._request(
                "POST", upload_url + "?" + urllib.parse.urlencode({"name": "YouTubeDownloaderPRO.exe"}),
                content, headers={"Content-Type": "application/octet-stream"},
            )
            if int(asset.get("size") or 0) != executable.stat().st_size:
                raise RuntimeError("o tamanho do arquivo publicado não confere")
            if progress:
                progress(75)
            release = self._request("PATCH", f"{API_ROOT}/releases/{release_id}", {"draft": False})
            return PublishedRelease(
                version=version,
                url=str(release["html_url"]),
                download_url=f"https://github.com/{OWNER}/{REPOSITORY}/releases/download/{tag}/YouTubeDownloaderPRO.exe",
                sha256=digest,
            )
        except Exception:
            try:
                self._request("DELETE", f"{API_ROOT}/releases/{release_id}")
            except Exception:
                pass
            raise
