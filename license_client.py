from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from github_admin import protect, unprotect


DEFAULT_API_URL = os.environ.get(
    "YTDPRO_API_URL",
    "https://app--youtubedownloaderpro30.base44.app/api/apps/6a7cdb84921168fc9ad9fa33/functions/license-api",
).rstrip("/")


class LicenseAPIError(RuntimeError):
    pass


class LicenseClient:
    def __init__(self, base_url: str = DEFAULT_API_URL):
        self.base_url = base_url.rstrip("/")
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.session_file = local / "YouTubeDownloaderPRO3" / "session.bin"
        self.credentials_file = self.session_file.with_name("saved_login.bin")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)

    def _request(self, method: str, path: str, payload: dict | None = None, authenticated: bool = False) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "YouTubeDownloaderPRO/3.0"}
        if authenticated:
            token = self.load_token()
            if not token:
                raise LicenseAPIError("Faça login para continuar.")
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read().decode("utf-8")).get("error")
            except Exception:
                message = None
            raise LicenseAPIError(message or f"Servidor respondeu {error.code}.") from error
        except (OSError, ValueError) as error:
            raise LicenseAPIError("Não foi possível conectar ao servidor de licenças.") from error

    def save_token(self, token: str) -> None:
        self.session_file.write_bytes(protect(token.encode("utf-8")))

    def load_token(self) -> str:
        try:
            return unprotect(self.session_file.read_bytes()).decode("utf-8")
        except Exception:
            return ""

    def save_login(self, email: str, password: str) -> None:
        """Guarda o login protegido pelo Windows e somente para este usuário."""
        payload = json.dumps({"email": email.strip().lower(), "password": password}).encode("utf-8")
        self.credentials_file.write_bytes(protect(payload))

    def load_login(self) -> tuple[str, str]:
        try:
            payload = json.loads(unprotect(self.credentials_file.read_bytes()).decode("utf-8"))
            return str(payload.get("email", "")), str(payload.get("password", ""))
        except Exception:
            return "", ""

    def clear_saved_login(self) -> None:
        try:
            self.credentials_file.unlink()
        except FileNotFoundError:
            pass

    def register(self, name: str, email: str, password: str, machine_id: str) -> dict:
        result = self._request("POST", "", {"action": "register", "name": name, "email": email, "password": password, "machine_id": machine_id})
        self.save_token(result["token"])
        return result

    def login(self, email: str, password: str, machine_id: str) -> dict:
        result = self._request("POST", "", {"action": "login", "email": email, "password": password, "machine_id": machine_id})
        self.save_token(result["token"])
        return result

    def request_password_reset(self, email: str) -> dict:
        return self._request("POST", "", {"action": "request_password_reset", "email": email})

    def reset_password(self, email: str, code: str, password: str) -> dict:
        return self._request("POST", "", {"action": "reset_password", "email": email, "code": code, "password": password})

    def license(self, machine_id: str) -> dict:
        return self._request("POST", "", {"action": "license", "machine_id": machine_id}, authenticated=True)

    def create_pix(self, machine_id: str) -> dict:
        return self._request("POST", "", {"action": "create_pix", "machine_id": machine_id}, authenticated=True)

    def payment(self, payment_id: str) -> dict:
        return self._request("POST", "", {"action": "payment", "payment_id": payment_id}, authenticated=True)
