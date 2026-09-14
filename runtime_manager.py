from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


APP_DIR_NAME = "YouTubeDownloaderPRO"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    bin: Path
    temp: Path
    state: Path
    logs: Path

    @classmethod
    def for_current_user(cls) -> "RuntimePaths":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        root = local / APP_DIR_NAME
        paths = cls(root, root / "bin", root / "temp", root / "state.json", root / "logs")
        for directory in (paths.root, paths.bin, paths.temp, paths.logs):
            directory.mkdir(parents=True, exist_ok=True)
        return paths


class RuntimeManager:
    """Installs and updates mutable download components outside the frozen app."""

    CHECK_INTERVAL = 24 * 60 * 60
    # FFmpeg and Deno are large downloads. They are refreshed automatically once
    # a week, while yt-dlp (the component that changes most often) is checked
    # daily. The manual command refreshes everything immediately.
    FULL_UPDATE_INTERVAL = 7 * 24 * 60 * 60

    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self.paths = RuntimePaths.for_current_user()
        self.log = log or (lambda _message: None)
        self._state = self._load_state()

    @property
    def ytdlp(self) -> Path:
        return self.paths.bin / "yt-dlp.exe"

    @property
    def ffmpeg(self) -> Optional[Path]:
        return self._find_binary("ffmpeg.exe")

    @property
    def ffprobe(self) -> Optional[Path]:
        return self._find_binary("ffprobe.exe")

    @property
    def deno(self) -> Optional[Path]:
        return self._find_binary("deno.exe")

    def _find_binary(self, name: str) -> Optional[Path]:
        local = self.paths.bin / name
        if local.is_file():
            return local
        found = shutil.which(name)
        return Path(found) if found else None

    def _load_state(self) -> dict:
        try:
            return json.loads(self.paths.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        temp = self.paths.state.with_suffix(".tmp")
        temp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        temp.replace(self.paths.state)

    @staticmethod
    def _works(command: list[str], timeout: int = 20) -> bool:
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def ready(self) -> bool:
        ffmpeg, ffprobe = self.ffmpeg, self.ffprobe
        return bool(
            self.ytdlp.is_file()
            and ffmpeg and ffprobe
            and self._works([str(self.ytdlp), "--version"])
            and self._works([str(ffmpeg), "-version"])
            and self._works([str(ffprobe), "-version"])
        )

    def ensure(self, force_update: bool = False) -> bool:
        """Prepare the runtime. A failed update never removes a working tool."""
        now = time.time()
        due = now - float(self._state.get("last_check", 0)) >= self.CHECK_INTERVAL
        full_due = now - float(self._state.get("last_full_check", 0)) >= self.FULL_UPDATE_INTERVAL
        if not self.ytdlp.is_file() or force_update or due:
            self._install_ytdlp(required=not self.ytdlp.is_file())

        if not self.ffmpeg or not self.ffprobe or force_update or full_due:
            self._install_ffmpeg()
        if not self.deno or force_update or full_due:
            self._install_deno()

        self._state["last_check"] = now
        if force_update or full_due:
            self._state["last_full_check"] = now
        self._save_state()
        return self.ready()

    def _download(self, url: str, suffix: str) -> Path:
        fd, raw_path = tempfile.mkstemp(suffix=suffix, dir=self.paths.temp)
        os.close(fd)
        path = Path(raw_path)
        request = urllib.request.Request(url, headers={"User-Agent": f"{APP_DIR_NAME}/2.0"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response, path.open("wb") as output:
                shutil.copyfileobj(response, output)
            if path.stat().st_size < 100_000:
                raise RuntimeError("arquivo baixado é pequeno demais")
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _install_ytdlp(self, required: bool) -> None:
        self.log("🔄 Verificando atualização do motor de download...")
        try:
            downloaded = self._download(YTDLP_URL, ".exe")
            if not self._works([str(downloaded), "--version"]):
                raise RuntimeError("o executável baixado não passou na validação")
            backup = self.ytdlp.with_suffix(".previous.exe")
            if self.ytdlp.exists():
                shutil.copy2(self.ytdlp, backup)
            downloaded.replace(self.ytdlp)
            self.log("✅ Motor de download atualizado")
        except Exception as exc:
            if required:
                self.log(f"❌ Não foi possível instalar o motor de download: {exc}")
            else:
                self.log(f"⚠️ Atualização indisponível; mantendo a versão atual: {exc}")

    def _install_ffmpeg(self) -> None:
        self.log("⬇️ Verificando/atualizando FFmpeg e FFprobe...")
        archive: Optional[Path] = None
        extract_dir = Path(tempfile.mkdtemp(prefix="ffmpeg-", dir=self.paths.temp))
        try:
            archive = self._download(FFMPEG_URL, ".zip")
            with zipfile.ZipFile(archive) as package:
                members = {
                    Path(name).name.lower(): name
                    for name in package.namelist()
                    if Path(name).name.lower() in {"ffmpeg.exe", "ffprobe.exe"}
                }
                if set(members) != {"ffmpeg.exe", "ffprobe.exe"}:
                    raise RuntimeError("pacote do FFmpeg incompleto")
                for filename, member in members.items():
                    package.extract(member, extract_dir)
                    source = extract_dir / member
                    candidate = self.paths.bin / f"{filename}.new"
                    shutil.copy2(source, candidate)
                    if not self._works([str(candidate), "-version"]):
                        raise RuntimeError(f"{filename} não passou na validação")
                    candidate.replace(self.paths.bin / filename)
            self.log("✅ FFmpeg pronto")
        except Exception as exc:
            self.log(f"❌ Não foi possível instalar o FFmpeg: {exc}")
        finally:
            if archive:
                archive.unlink(missing_ok=True)
            shutil.rmtree(extract_dir, ignore_errors=True)

    def _install_deno(self) -> None:
        self.log("⬇️ Verificando/atualizando suporte JavaScript para o YouTube...")
        archive: Optional[Path] = None
        try:
            archive = self._download(DENO_URL, ".zip")
            with zipfile.ZipFile(archive) as package:
                member = next((name for name in package.namelist() if Path(name).name == "deno.exe"), None)
                if not member:
                    raise RuntimeError("pacote do Deno incompleto")
                package.extract(member, self.paths.temp)
                source = self.paths.temp / member
                candidate = self.paths.bin / "deno.exe.new"
                shutil.copy2(source, candidate)
                if not self._works([str(candidate), "--version"]):
                    raise RuntimeError("Deno não passou na validação")
                candidate.replace(self.paths.bin / "deno.exe")
            self.log("✅ Suporte JavaScript pronto")
        except Exception as exc:
            self.log(f"❌ Não foi possível instalar o suporte JavaScript: {exc}")
        finally:
            if archive:
                archive.unlink(missing_ok=True)

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = str(self.paths.bin) + os.pathsep + env.get("PATH", "")
        return env
