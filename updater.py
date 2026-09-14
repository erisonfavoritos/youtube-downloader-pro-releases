from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


MANIFEST_URL = "https://raw.githubusercontent.com/erisonfavoritos/youtube-downloader-pro-releases/main/update.json"


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    required: bool
    notes: str


class UpdateManager:
    def __init__(self, current_version: str):
        self.current_version = current_version

    def fetch_manifest(self) -> dict:
        request = urllib.request.Request(
            MANIFEST_URL,
            headers={"User-Agent": f"YouTubeDownloaderPRO/{self.current_version}", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def check(self, channel: str = "stable") -> UpdateInfo | None:
        manifest = self.fetch_manifest()
        data = manifest.get("channels", {}).get(channel) or manifest.get("channels", {}).get("stable") or {}
        version = str(data.get("version") or "0")
        if _version_tuple(version) <= _version_tuple(self.current_version):
            return None
        return UpdateInfo(
            version=version,
            download_url=str(data.get("download_url") or ""),
            sha256=str(data.get("sha256") or "").lower(),
            required=bool(data.get("required", False)),
            notes="\n".join(data.get("notes") or []),
        )

    def download(self, info: UpdateInfo, progress=None) -> Path:
        if not info.download_url.startswith("https://") or len(info.sha256) != 64:
            raise RuntimeError("manifesto de atualização inválido")
        destination = Path(tempfile.gettempdir()) / f"YouTubeDownloaderPRO-{info.version}.new.exe"
        request = urllib.request.Request(info.download_url, headers={"User-Agent": "YouTubeDownloaderPRO-Updater"})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while True:
                block = response.read(1024 * 256)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                received += len(block)
                if progress and total:
                    progress(min(100, received * 100 / total))
        if digest.hexdigest().lower() != info.sha256:
            destination.unlink(missing_ok=True)
            raise RuntimeError("a assinatura SHA-256 do arquivo não confere")
        if destination.stat().st_size < 1_000_000:
            destination.unlink(missing_ok=True)
            raise RuntimeError("arquivo de atualização incompleto")
        return destination

    @staticmethod
    def install_and_restart(new_executable: Path) -> None:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("a instalação automática só funciona no executável compilado")
        current = Path(sys.executable).resolve()
        backup = current.with_suffix(".previous.exe")
        script = Path(tempfile.gettempdir()) / "YouTubeDownloaderPRO_update.ps1"
        update_log = current.with_name("YouTubeDownloaderPRO_update_log.txt")
        ready_file = Path(tempfile.gettempdir()) / f"YouTubeDownloaderPRO_{os.getpid()}_ready.txt"
        content = f'''$ErrorActionPreference = "Stop"
$pidToWait = {os.getpid()}
$current = '{str(current).replace("'", "''")}'
$new = '{str(new_executable).replace("'", "''")}'
$backup = '{str(backup).replace("'", "''")}'
$log = '{str(update_log).replace("'", "''")}'
$ready = '{str(ready_file).replace("'", "''")}'
function Write-UpdateLog([string]$text) {{
  Add-Content -LiteralPath $log -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $text"
}}
Write-UpdateLog "Iniciando atualização. Atual=$current Novo=$new"
for($i=0; $i -lt 60; $i++) {{
  if(-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
  Start-Sleep -Milliseconds 500
}}
if(Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Force }}
Move-Item -LiteralPath $current -Destination $backup -Force
try {{
  Move-Item -LiteralPath $new -Destination $current -Force
  Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
  $newProcess = Start-Process -FilePath $current -ArgumentList "--update-ready=$ready" -PassThru
  Write-UpdateLog "Nova versão iniciada. PID=$($newProcess.Id)"
  for($i=0; $i -lt 40; $i++) {{
    if(Test-Path -LiteralPath $ready) {{ break }}
    if($newProcess.HasExited) {{ break }}
    Start-Sleep -Milliseconds 500
  }}
  if(-not (Test-Path -LiteralPath $ready)) {{
    Write-UpdateLog "Nova versão não confirmou inicialização; restaurando anterior."
    if(-not $newProcess.HasExited) {{ Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue }}
    Remove-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $backup -Destination $current -Force
    Start-Process -FilePath $current
    exit 2
  }}
  Write-UpdateLog "Atualização confirmada com sucesso."
  Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
  if(Test-Path -LiteralPath $backup) {{
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "Cópia temporária da versão anterior removida."
  }}
}} catch {{
  Write-UpdateLog "Erro: $($_.Exception.Message)"
  if(Test-Path -LiteralPath $backup) {{ Move-Item -LiteralPath $backup -Destination $current -Force }}
  Start-Process -FilePath $current -ErrorAction SilentlyContinue
  throw
}}
Remove-Item -LiteralPath $PSCommandPath -Force
'''
        script.write_text(content, encoding="utf-8-sig")
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script)],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
