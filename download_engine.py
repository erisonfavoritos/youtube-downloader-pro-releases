from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from runtime_manager import RuntimeManager


AUTH_ERROR_MARKERS = (
    "sign in to confirm",
    "cookies-from-browser",
    "login required",
    "age-restricted",
    "not a bot",
)


@dataclass
class DownloadResult:
    files: list[Path]
    output: list[str]


class DownloadCancelled(Exception):
    pass


class DownloadEngine:
    PATH_PREFIX = "FINAL_FILE:"
    ID_PREFIX = "MEDIA_ID:"

    def __init__(self, runtime: RuntimeManager, log: Callable[[str], None]):
        self.runtime = runtime
        self.log = log
        self.process: Optional[subprocess.Popen] = None

    @staticmethod
    def installed_browsers() -> list[str]:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        roaming = Path(os.environ.get("APPDATA", ""))
        candidates = [
            ("chrome", local / "Google/Chrome/User Data"),
            ("edge", local / "Microsoft/Edge/User Data"),
            ("firefox", roaming / "Mozilla/Firefox/Profiles"),
            ("brave", local / "BraveSoftware/Brave-Browser/User Data"),
            ("opera", roaming / "Opera Software/Opera Stable"),
        ]
        return [name for name, path in candidates if path.exists()]

    def _base_command(self) -> list[str]:
        command = [
            str(self.runtime.ytdlp),
            "--newline",
            "--no-colors",
            "--windows-filenames",
            "--ignore-config",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "http:linear=2::10",
            "--retry-sleep", "fragment:linear=1::5",
            "--sleep-requests", "0.75",
        ]
        if self.runtime.deno:
            command += ["--js-runtimes", f"deno:{self.runtime.deno}", "--remote-components", "ejs:github"]
        return command

    def probe(self, url: str, browser: Optional[str] = None) -> dict:
        command = self._base_command() + ["--dump-single-json", "--skip-download"]
        if browser:
            command += ["--cookies-from-browser", browser]
        command.append(url)
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=self.runtime.environment(), timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[-1000:])
        return json.loads(result.stdout)

    def download(
        self,
        url: str,
        output_dir: Path,
        audio: bool,
        quality: str,
        browser: Optional[str],
        cancelled: Callable[[], bool],
        status: Callable[[str], None],
    ) -> DownloadResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        template = str(output_dir / "%(title).180B [%(id)s].%(ext)s")
        command = self._base_command() + [
            "--output", template,
            "--print", f"after_move:{self.PATH_PREFIX}%(filepath)s",
            "--print", f"before_dl:{self.ID_PREFIX}%(id)s",
            "--no-overwrites",
        ]
        ffmpeg = self.runtime.ffmpeg
        if ffmpeg:
            command += ["--ffmpeg-location", str(ffmpeg.parent)]
        if browser:
            command += ["--cookies-from-browser", browser]

        if audio:
            command += [
                "--format", "bestaudio/best",
                "--extract-audio", "--audio-format", "mp3", "--audio-quality", "192K",
                "--embed-metadata", "--embed-thumbnail",
            ]
        else:
            height = re.sub(r"\D", "", quality)
            format_value = "bestvideo+bestaudio/best"
            if height:
                format_value = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
            command += ["--format", format_value, "--merge-output-format", "mp4"]
        command.append(url)

        lines: list[str] = []
        files: list[Path] = []
        media_ids: list[str] = []
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=self.runtime.environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                if cancelled():
                    self.cancel()
                    raise DownloadCancelled("Download interrompido pelo usuário")
                line = raw.strip()
                if not line:
                    continue
                lines.append(line)
                if self.PATH_PREFIX in line:
                    candidate = Path(line.split(self.PATH_PREFIX, 1)[1].strip())
                    if candidate.is_file():
                        files.append(candidate)
                elif self.ID_PREFIX in line:
                    media_ids.append(line.split(self.ID_PREFIX, 1)[1].strip())
                elif line.startswith("[download]") and "%" in line:
                    status(line.replace("[download]", "").strip())
                elif "ERROR:" in line or "WARNING:" in line:
                    self.log(line)
            return_code = self.process.wait()
        finally:
            self.process = None
        if return_code:
            raise RuntimeError("\n".join(lines[-12:]) or f"yt-dlp terminou com código {return_code}")
        if not files:
            # O caminho pode não ser impresso quando o arquivo já existe. O ID
            # faz a recuperação sem confundir com outros downloads da pasta.
            for media_id in media_ids:
                for extension in (("mp3",) if audio else ("mp4", "mkv", "webm")):
                    matches = sorted(
                        (path for path in output_dir.glob(f"*.{extension}") if f"[{media_id}]" in path.name),
                        key=lambda p: p.stat().st_mtime,
                    )
                    if matches:
                        files.append(matches[-1])
            files = list(dict.fromkeys(files))
        if not files:
            raise RuntimeError("Download terminou sem produzir um arquivo final")
        return DownloadResult(files, lines)

    def cancel(self) -> None:
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    @staticmethod
    def looks_like_auth_error(error: Exception) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in AUTH_ERROR_MARKERS)


def ordered_browsers(preferred: str, installed: Iterable[str]) -> list[str]:
    values = list(dict.fromkeys([preferred, *installed]))
    return [value for value in values if value]
