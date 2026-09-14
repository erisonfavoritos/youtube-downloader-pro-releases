from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from updater import MANIFEST_URL
from license_client import LicenseAPIError, LicenseClient
from pathlib import Path


TRIAL_SECONDS = 24 * 60 * 60
TRIAL_DOWNLOADS = 2
TRIAL_MEDIA_SECONDS = 30 * 60
TRIAL_TRACKS = 10
OFFLINE_GRACE_SECONDS = 6 * 60 * 60
TIME_ENDPOINTS = (
    "https://www.google.com/generate_204",
    "https://www.microsoft.com/",
    "https://github.com/",
)
SALT = b"YouTubeDownloaderPRO-v3-auth"
PASSWORD_HASHES = {
    "admin": "1067c76ac7b475c8d5df43f3d7c58b0bf7c23643ef87a25630e84a5dec7a0a73",
    "user": "3bb3a3b48ac5f5013d02f20306dfb5a196aa621a26fd96ff3267894bea136594",
}


@dataclass(frozen=True)
class AccessResult:
    allowed: bool
    username: str = ""
    remaining: int = 0
    message: str = ""
    permanent: bool = False
    downloads_used: int = 0
    media_seconds_used: int = 0
    tracks_used: int = 0


class AccessControl:
    def __init__(self):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.directory = local / "YouTubeDownloaderPRO"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_file = self.directory / "access_state.json"

    @staticmethod
    def _hash(username: str, password: str) -> str:
        return hashlib.sha256(SALT + username.encode("utf-8") + password.encode("utf-8")).hexdigest()

    @staticmethod
    def machine_id() -> str:
        parts = [platform.node(), os.environ.get("SYSTEMDRIVE", "C:")]
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                    parts.append(str(winreg.QueryValueEx(key, "MachineGuid")[0]))
            except OSError:
                pass
        digest = hashlib.sha256("|".join(parts).encode("utf-8", "replace")).hexdigest().upper()
        return "-".join(digest[index:index + 4] for index in range(0, 20, 4))

    def remote_license(self) -> AccessResult | None:
        client = LicenseClient()
        if client.load_token():
            try:
                result = client.license(self.machine_id())
                if result.get("active"):
                    expires = int(result.get("expires_at") or 0)
                    return AccessResult(True, username="licensed", remaining=max(0, expires - int(time.time())), message="Licença PRO ativa", permanent=True)
                return None
            except LicenseAPIError:
                pass
        try:
            # O parâmetro variável evita que proxies/CDNs devolvam uma cópia
            # antiga logo depois de o Gerenciador publicar um bloqueio.
            manifest_url = f"{MANIFEST_URL}?license_check={int(time.time())}"
            request = urllib.request.Request(
                manifest_url,
                headers={
                    "User-Agent": "YouTubeDownloaderPRO-License",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                manifest = json.load(response)
            machine = self.machine_id()
            blocked = set(manifest.get("licenses", {}).get("blocked", []))
            permanent = set(manifest.get("licenses", {}).get("permanent", []))
            if machine in blocked:
                return AccessResult(False, username="user", message="Esta licença foi bloqueada remotamente.")
            if machine in permanent:
                return AccessResult(True, username="licensed", message="Licença permanente ativa", permanent=True)
        except Exception:
            pass
        return None

    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(self.state_file)

    def trial_usage(self) -> dict:
        state = self._load()
        return {
            "downloads": int(state.get("trial_downloads", 0) or 0),
            "media_seconds": int(state.get("trial_media_seconds", 0) or 0),
            "tracks": int(state.get("trial_tracks", 0) or 0),
        }

    def can_consume(self, downloads: int = 0, media_seconds: float = 0, tracks: int = 0) -> tuple[bool, str]:
        usage = self.trial_usage()
        if usage["downloads"] + downloads > TRIAL_DOWNLOADS:
            return False, "A avaliação permite no máximo 2 downloads concluídos."
        if usage["media_seconds"] + int(media_seconds) > TRIAL_MEDIA_SECONDS:
            remaining = max(0, TRIAL_MEDIA_SECONDS - usage["media_seconds"])
            return False, f"A avaliação possui apenas {remaining // 60} minuto(s) de conteúdo restante(s)."
        if usage["tracks"] + tracks > TRIAL_TRACKS:
            remaining = max(0, TRIAL_TRACKS - usage["tracks"])
            return False, f"A avaliação permite gerar somente mais {remaining} faixa(s)."
        return True, ""

    def consume(self, downloads: int = 0, media_seconds: float = 0, tracks: int = 0) -> dict:
        state = self._load()
        state["trial_downloads"] = int(state.get("trial_downloads", 0) or 0) + max(0, int(downloads))
        state["trial_media_seconds"] = int(state.get("trial_media_seconds", 0) or 0) + max(0, int(media_seconds))
        state["trial_tracks"] = int(state.get("trial_tracks", 0) or 0) + max(0, int(tracks))
        self._save(state)
        return self.trial_usage()

    @staticmethod
    def _read_https_time(url: str) -> int:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "YouTubeDownloaderPRO/2.4"})
        with urllib.request.urlopen(request, timeout=4) as response:
            date_header = response.headers.get("Date")
        if not date_header:
            raise RuntimeError("servidor não forneceu horário")
        return int(parsedate_to_datetime(date_header).timestamp())

    def trusted_time(self) -> int | None:
        """Returns the median HTTPS server time, rejecting inconsistent sources."""
        values: list[int] = []
        with ThreadPoolExecutor(max_workers=len(TIME_ENDPOINTS)) as pool:
            futures = [pool.submit(self._read_https_time, url) for url in TIME_ENDPOINTS]
            for future in as_completed(futures):
                try:
                    values.append(future.result())
                except Exception:
                    pass
        if not values:
            return None
        values.sort()
        median = values[len(values) // 2]
        consistent = [value for value in values if abs(value - median) <= 120]
        return median if consistent else None

    def _evaluate_trial(self, require_started: bool) -> AccessResult:
        local_now = int(time.time())
        state = self._load()
        started = int(state.get("trial_started", 0) or 0)
        last_seen = int(state.get("last_seen", 0) or 0)
        trusted = self.trusted_time()

        if trusted is not None:
            now = trusted
            if abs(local_now - trusted) > 300:
                state["clock_mismatch_detected"] = True
            state["last_trusted"] = trusted
            state["last_trusted_local"] = local_now
        else:
            last_trusted = int(state.get("last_trusted", 0) or 0)
            last_trusted_local = int(state.get("last_trusted_local", 0) or 0)
            if not started and not require_started:
                return AccessResult(False, username="user", message="Conecte este computador à internet para iniciar a avaliação.")
            if not last_trusted or not last_trusted_local:
                return AccessResult(False, username="user", message="Não foi possível validar a hora. Conecte-se à internet.")
            local_elapsed = local_now - last_trusted_local
            if local_elapsed < -300:
                state["clock_rollback"] = True
                self._save(state)
                return AccessResult(False, username="user", message="Data ou relógio retrocedido. Avaliação bloqueada.")
            if local_elapsed > OFFLINE_GRACE_SECONDS:
                return AccessResult(False, username="user", message="Validação offline expirada. Conecte-se à internet para continuar.")
            now = max(last_seen, last_trusted + max(0, local_elapsed))

        if state.get("clock_rollback"):
            return AccessResult(False, username="user", message="Avaliação bloqueada por alteração do relógio.")
        if not started:
            if require_started:
                return AccessResult(False, username="user", message="Avaliação não iniciada.")
            started = now
            state["trial_started"] = started
        if last_seen and now + 300 < last_seen:
            state["clock_rollback"] = True
            self._save(state)
            return AccessResult(False, username="user", message="Data ou relógio retrocedido. Avaliação bloqueada.")

        expires = started + TRIAL_SECONDS
        remaining = max(0, expires - now)
        usage = self.trial_usage()
        state["last_seen"] = max(now, last_seen)
        state["trial_expires"] = expires
        state["trial_expires_text"] = datetime.fromtimestamp(expires).isoformat(timespec="seconds")
        self._save(state)
        if remaining <= 0:
            return AccessResult(False, username="user", message="O período de avaliação de 24 horas terminou.")
        if usage["downloads"] >= TRIAL_DOWNLOADS:
            return AccessResult(False, username="user", message="O limite de 2 downloads da avaliação foi atingido.")
        if usage["media_seconds"] >= TRIAL_MEDIA_SECONDS:
            return AccessResult(False, username="user", message="O limite de 30 minutos da avaliação foi atingido.")
        if usage["tracks"] >= TRIAL_TRACKS:
            return AccessResult(False, username="user", message="O limite de 10 faixas da avaliação foi atingido.")
        source = "hora da internet" if trusted is not None else "modo offline temporário"
        return AccessResult(
            True, username="user", remaining=remaining, message=f"Avaliação ativa — {source}",
            downloads_used=usage["downloads"], media_seconds_used=usage["media_seconds"],
            tracks_used=usage["tracks"],
        )

    def authenticate(self, username: str, password: str) -> AccessResult:
        username = username.strip().lower()
        if "@" in username:
            try:
                LicenseClient().login(username, password, self.machine_id())
                licensed = self.remote_license()
                if licensed and licensed.allowed:
                    return licensed
                return self._evaluate_trial(require_started=False)
            except LicenseAPIError as error:
                return AccessResult(False, message=str(error))
        expected = PASSWORD_HASHES.get(username)
        if not expected or self._hash(username, password) != expected:
            return AccessResult(False, message="Usuário ou senha inválidos.")
        if username == "admin":
            return AccessResult(True, username="admin", message="Acesso administrativo", permanent=True)
        remote = self.remote_license()
        if remote is not None:
            return remote
        return self._evaluate_trial(require_started=False)

    def current_trial(self) -> AccessResult:
        """Revalida uma avaliação iniciada sem reutilizar a senha."""
        return self._evaluate_trial(require_started=True)


def format_remaining(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}h {minutes:02d}min {secs:02d}s"
