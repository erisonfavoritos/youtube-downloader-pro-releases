#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Downloader PRO v1.3.0
CORRIGIDO - Ordem de inicialização ajustada
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import os
import re
import json
import zipfile
import subprocess
import sys
import tempfile
import pathlib
import shutil
import traceback
from datetime import datetime
from typing import Optional, List
import urllib.request
import urllib.error
from runtime_manager import RuntimeManager
from download_engine import DownloadCancelled, DownloadEngine, ordered_browsers
from chapter_parser import Chapter, chapters_from_info
from access_control import (
    AccessControl, AccessResult, format_remaining,
    TRIAL_DOWNLOADS, TRIAL_MEDIA_SECONDS, TRIAL_TRACKS,
)
from updater import UpdateManager
from license_client import LicenseAPIError, LicenseClient

# ============ VERSION ============
VERSION = "3.0.0"
APP_NAME = "YouTube Downloader PRO"

# ============ CONFIGURAÇÕES PADRÃO ============
DEFAULT_CONFIG = {
    "download_dir": os.path.expanduser("~/Downloads"),
    "cuts_dir": os.path.expanduser("~/Downloads/Cortes_Automaticos"),
    "auth_method": "browser",
    "browser": "chrome",
    "cookie_file": "",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "quality": "720p",
    "silence_threshold": "-35dB",
    "silence_duration": "1.5",
    "auto_retry": True,
    "shutdown": False,
    "auto_cut": False,
    "organize_by_music": True,
    "export_zip": False,
    "download_type": "audio",
}


# ============ UTILS ============
def sanitize_filename(name: str) -> str:
    """Sanitiza nome de arquivo removendo caracteres inválidos."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def get_duration(audio_file: str) -> float:
    """Obtém duração do áudio usando ffprobe."""
    try:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_file],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return float(result.stdout.strip() or "0")
    except Exception:
        return 0.0


def ensure_directory(path: str) -> bool:
    """Garante que o diretório existe."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


def is_valid_youtube_url(url: str) -> bool:
    """Valida URL do YouTube."""
    patterns = [
        r'^https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+',
        r'^https?://(?:www\.)?youtu\.be/[\w-]+',
        r'^https?://(?:www\.)?youtube\.com/playlist\?list=[\w-]+',
        r'^https?://(?:www\.)?youtube\.com/shorts/[\w-]+',
    ]
    return any(re.match(p, url) for p in patterns)


def format_time(seconds: float) -> str:
    """Formata segundos para MM:SS."""
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes:02d}:{seconds:02d}"


def get_file_size(filepath: str) -> str:
    """Retorna tamanho do arquivo formatado."""
    try:
        size = os.path.getsize(filepath)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    except:
        return "0 B"


# ============ COOKIE MANAGER ============
class CookieManager:
    """Gerenciador simplificado de cookies para YouTube."""

    def __init__(self):
        self.temp_files = []

    def get_cookies_auto(self, browser: str = 'chrome') -> Optional[str]:
        """Obtém cookies automaticamente do navegador."""
        try:
            import browser_cookie3
            browsers = {
                'chrome': browser_cookie3.chrome,
                'edge': browser_cookie3.edge,
                'firefox': browser_cookie3.firefox,
                'opera': browser_cookie3.opera,
                'brave': browser_cookie3.brave,
                'chromium': browser_cookie3.chromium
            }

            if browser not in browsers:
                return None

            cookie_jar = browsers[browser](domain_name='.youtube.com')

            if not cookie_jar:
                cookie_jar = browsers[browser](domain_name='.google.com')

            if not cookie_jar:
                return None

            fd, path = tempfile.mkstemp(suffix='.txt', prefix='cookies_')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                for cookie in cookie_jar:
                    if (cookie.domain.endswith('.youtube.com') or
                            cookie.domain == 'youtube.com' or
                            cookie.domain.endswith('.google.com')):
                        secure = 'TRUE' if cookie.secure else 'FALSE'
                        expires = str(int(cookie.expires)) if cookie.expires else '0'
                        line = f"{cookie.domain}\tTRUE\t{cookie.path}\t{secure}\t{expires}\t{cookie.name}\t{cookie.value}\n"
                        f.write(line)

            self.temp_files.append(path)
            return path

        except Exception as e:
            print(f"Erro ao obter cookies: {e}")
            return None

    def cleanup(self):
        """Remove arquivos temporários."""
        for file in self.temp_files:
            try:
                if os.path.exists(file):
                    os.unlink(file)
            except Exception:
                pass
        self.temp_files.clear()


# ============ DEPENDENCY MANAGER ============
class DependencyManager:
    """Gerencia a instalação e atualização de dependências."""

    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.deps_dir = os.path.join(app_dir, "dependencies")
        self.bin_dir = os.path.join(self.deps_dir, "bin")
        self.lib_dir = os.path.join(self.deps_dir, "lib")
        self.temp_dir = os.path.join(app_dir, "temp")

        for d in [self.deps_dir, self.bin_dir, self.lib_dir, self.temp_dir]:
            os.makedirs(d, exist_ok=True)

        self.dependencies = {
            "ffmpeg": {
                "name": "FFmpeg",
                "version": "latest",
                "url_windows": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
                "files": ["ffmpeg.exe", "ffprobe.exe"],
                "dest_dir": self.bin_dir,
                "required": True,
                "hash": None
            },
            "yt-dlp": {
                "name": "yt-dlp",
                "version": "latest",
                "url_windows": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                "files": ["yt-dlp.exe"],
                "dest_dir": self.bin_dir,
                "required": True,
                "hash": None
            }
        }

    def check_dependencies(self) -> dict:
        """Verifica quais dependências estão instaladas."""
        status = {}
        for key, dep in self.dependencies.items():
            status[key] = self._check_binary(dep)
        return status

    def _check_binary(self, dep) -> dict:
        """Verifica se um binário está disponível."""
        result = {"installed": False, "path": None, "version": None}

        for file in dep["files"]:
            file_path = os.path.join(dep["dest_dir"], file)
            if os.path.exists(file_path):
                result["installed"] = True
                result["path"] = file_path
                break

        if not result["installed"]:
            for file in dep["files"]:
                path = shutil.which(file)
                if path:
                    result["installed"] = True
                    result["path"] = path
                    break

        return result

    def download_dependency(self, dep_key, progress_callback=None, log_callback=None) -> bool:
        """Baixa uma dependência específica."""
        dep = self.dependencies.get(dep_key)
        if not dep:
            return False

        return self._download_binary(dep_key, dep, progress_callback, log_callback)

    def _download_binary(self, dep_key, dep, progress_callback, log_callback) -> bool:
        """Baixa um binário."""
        try:
            if log_callback:
                log_callback(f"⬇️ Baixando {dep['name']}...")

            url = dep.get("url_windows")
            if not url:
                return False

            temp_file = os.path.join(self.temp_dir, f"{dep_key}.tmp")

            def report_progress(block_num, block_size, total_size):
                if total_size > 0:
                    percent = min(100, (block_num * block_size * 100) / total_size)
                    if progress_callback:
                        progress_callback(dep_key, percent)

            urllib.request.urlretrieve(url, temp_file, report_progress)

            if dep_key == "ffmpeg":
                return self._extract_ffmpeg(temp_file, dep, log_callback)
            else:
                return self._install_binary(temp_file, dep, log_callback)

        except Exception as e:
            if log_callback:
                log_callback(f"❌ Erro ao baixar {dep['name']}: {e}")
            return False

    def _extract_ffmpeg(self, zip_file, dep, log_callback) -> bool:
        """Extrai o FFmpeg do ZIP."""
        try:
            if log_callback:
                log_callback("📦 Extraindo FFmpeg...")

            extract_dir = os.path.join(self.temp_dir, "ffmpeg_extract")
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(zip_file, 'r') as zf:
                zf.extractall(extract_dir)

            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file in dep["files"]:
                        src = os.path.join(root, file)
                        dst = os.path.join(dep["dest_dir"], file)
                        shutil.copy2(src, dst)
                        if log_callback:
                            log_callback(f"✅ {file} copiado")

            os.environ["PATH"] = dep["dest_dir"] + os.pathsep + os.environ.get("PATH", "")

            shutil.rmtree(extract_dir)
            os.remove(zip_file)

            return True

        except Exception as e:
            if log_callback:
                log_callback(f"❌ Erro ao extrair FFmpeg: {e}")
            return False

    def _install_binary(self, temp_file, dep, log_callback) -> bool:
        """Instala um binário simples."""
        try:
            for file in dep["files"]:
                dst = os.path.join(dep["dest_dir"], file)
                shutil.copy2(temp_file, dst)
                if log_callback:
                    log_callback(f"✅ {file} instalado")

            os.remove(temp_file)
            return True

        except Exception as e:
            if log_callback:
                log_callback(f"❌ Erro ao instalar: {e}")
            return False


# ============ MAIN APPLICATION ============
class YouTubeDownloaderEnhanced:
    """Aplicação principal do YouTube Downloader."""

    CONFIG_DIR = pathlib.Path.home() / ".youtube_downloader"

    def __init__(self, root, access: AccessResult):
        self.root = root
        self.access = access
        self.root.title(f"{APP_NAME} v{VERSION}")
        self.root.geometry("950x820")
        self.root.minsize(850, 750)

        # Ocultar inicialmente para mostrar bootstrap
        self.root.withdraw()

        # Estado
        self.download_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.is_downloading = False
        self.is_cutting = False

        # ===== CARREGA CONFIGURAÇÕES PRIMEIRO =====
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        logs_dir = self.CONFIG_DIR / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        app_location = pathlib.Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else pathlib.Path(__file__).resolve().parent
        preferred_log = app_location / "YouTubeDownloaderPRO_log.txt"
        try:
            with open(preferred_log, "a", encoding="utf-8"):
                pass
            self.session_log_file = preferred_log
        except OSError:
            self.session_log_file = logs_dir / "YouTubeDownloaderPRO_log.txt"
        self._session_log_lock = threading.Lock()
        self.root.report_callback_exception = self._handle_tk_exception
        threading.excepthook = self._handle_thread_exception
        self._write_session_line(f"===== INÍCIO DA SESSÃO | {APP_NAME} v{VERSION} =====\n")
        self._write_session_line(f"Executável: {sys.executable}\n")
        self._write_session_line(f"Python interno: {sys.version.split()[0]} | Plataforma: {sys.platform}\n")
        self.config_file = self.CONFIG_DIR / "config.json"
        self.load_config()  # <--- CHAMADO ANTES DO SETUP_UI

        # Componentes mutáveis ficam no AppData e podem ser atualizados sem
        # substituir o aplicativo inteiro.
        self.runtime = RuntimeManager(log=self.log)
        self.download_engine = DownloadEngine(self.runtime, self.log)
        app_dir = os.path.dirname(os.path.abspath(__file__))
        self.dep_manager = DependencyManager(app_dir)  # compatibilidade da UI antiga

        # ===== SETUP UI =====
        self.setup_ui()  # <--- AGORA AS VARIÁVEIS EXISTEM

        # ===== COOKIE MANAGER =====
        self.cookie_manager = CookieManager()

        # Atualiza PATH com dependências
        deps_bin = str(self.runtime.paths.bin)
        os.environ["PATH"] = deps_bin + os.pathsep + os.environ.get("PATH", "")

        # Verifica FFmpeg
        if not shutil.which("ffmpeg"):
            self.log("⚠️ FFmpeg não encontrado. Use 'Instalar/Atualizar FFmpeg' na aba Corte Local.")

        # Mostra a janela principal
        self.root.deiconify()

        # Log inicial
        self.log(f"🚀 {APP_NAME} v{VERSION} iniciado!")
        if access.permanent:
            self.log(f"🔐 {access.message}; acesso sem prazo")
        else:
            self.log(f"🔐 Login de avaliação realizado; restante: {format_remaining(access.remaining)}")
            self.log(self._trial_summary())
        self.log(f"📁 Configurações em: {self.CONFIG_DIR}")
        self.log(f"📦 Componentes atualizáveis em: {self.runtime.paths.root}")
        self.log("💡 Cookies do navegador serão usados automaticamente somente quando necessários")
        self.log("🎵 Use 'Corte Local' para processar arquivos já baixados")

        # Verifica dependências em background
        self.root.after(2000, self.check_dependencies_background)
        # A licença permanece sendo consultada durante toda a sessão. Assim,
        # um ID bloqueado no Gerenciador perde o acesso sem precisar reiniciar.
        if access.username != "admin":
            self.root.after(1000, self._update_access)
        self.update_manager = UpdateManager(VERSION)
        self.root.after(5000, self.check_app_update_background)

    def _update_access(self):
        """Revalida licença/avaliação fora da thread da interface."""
        threading.Thread(target=self._check_access_worker, daemon=True).start()

    def _check_access_worker(self):
        control = AccessControl()
        remote = control.remote_license()
        if remote is not None:
            result = remote
        elif self.access.permanent:
            # Uma falha temporária de internet não derruba uma licença já
            # validada. Bloqueios publicados são retornados acima.
            result = self.access
        else:
            result = control.current_trial()
        self.root.after(0, lambda: self._apply_access_result(result))

    def _apply_access_result(self, result: AccessResult):
        if not result.allowed:
            self.log(f"⛔ {result.message}")
            messagebox.showerror("Acesso bloqueado", result.message)
            self.save_config()
            self.root.destroy()
            return
        self.access = result
        if result.permanent:
            self.root.title(f"{APP_NAME} v{VERSION} — Licença permanente")
        else:
            self.root.title(f"{APP_NAME} v{VERSION} — Avaliação: {format_remaining(result.remaining)}")
        self.root.after(30_000, self._update_access)

    def _is_trial(self) -> bool:
        return not self.access.permanent and self.access.username == "user"

    def _trial_summary(self) -> str:
        usage = AccessControl().trial_usage()
        minutes_left = max(0, TRIAL_MEDIA_SECONDS - usage["media_seconds"]) // 60
        return (
            f"🎁 Avaliação: {max(0, TRIAL_DOWNLOADS - usage['downloads'])} download(s), "
            f"{minutes_left} minuto(s) e {max(0, TRIAL_TRACKS - usage['tracks'])} faixa(s) restantes"
        )

    def _trial_can(self, downloads=0, media_seconds=0, tracks=0) -> tuple[bool, str]:
        if not self._is_trial():
            return True, ""
        return AccessControl().can_consume(downloads, media_seconds, tracks)

    def _trial_consume(self, downloads=0, media_seconds=0, tracks=0) -> None:
        if self._is_trial():
            AccessControl().consume(downloads, media_seconds, tracks)
            self.log(self._trial_summary())

    def check_dependencies_background(self, force_update=False, manual=False):
        """Prepara componentes após o login, sem travar a interface."""

        def check():
            if self.runtime.ensure(force_update=force_update):
                message = "✅ Componentes atualizados e prontos" if force_update else "✅ Componentes verificados e prontos"
                self.log(message)
                if manual:
                    self._ui(lambda: messagebox.showinfo("Componentes", "Os componentes de download estão atualizados e prontos."))
            else:
                self.log("⚠️ Alguns componentes não puderam ser preparados. Verifique a internet e tente novamente.")
                if manual:
                    self._ui(lambda: messagebox.showwarning("Componentes", "Alguns componentes não puderam ser preparados. Confira sua internet e tente novamente."))

        threading.Thread(target=check, daemon=True).start()

    def load_config(self):
        """Carrega configurações do arquivo. DEVE SER CHAMADO ANTES DO SETUP_UI."""
        config = DEFAULT_CONFIG.copy()

        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    config.update(saved)
            except Exception:
                pass

        # Criar variáveis tkinter (ANTES do setup_ui)
        self.dir_entry_val = tk.StringVar(value=config["download_dir"])
        self.cuts_dir_entry_val = tk.StringVar(value=config["cuts_dir"])
        self.auth_method_var = tk.StringVar(value=config["auth_method"])
        self.browser_var = tk.StringVar(value=config["browser"])
        self.cookie_file_var = tk.StringVar(value=config["cookie_file"])
        self.ua_var = tk.StringVar(value=config["user_agent"])
        self.quality_var = tk.StringVar(value=config["quality"])
        self.silence_threshold_var = tk.StringVar(value=config["silence_threshold"])
        self.silence_duration_var = tk.StringVar(value=config["silence_duration"])
        self.auto_retry_var = tk.BooleanVar(value=config["auto_retry"])
        self.shutdown_var = tk.BooleanVar(value=config["shutdown"])
        self.auto_cut_var = tk.BooleanVar(value=config["auto_cut"])
        self.organize_by_music_var = tk.BooleanVar(value=config["organize_by_music"])
        self.export_zip_var = tk.BooleanVar(value=config["export_zip"])
        self.download_type = tk.StringVar(value=config["download_type"])

    def save_config(self):
        """Salva configurações em arquivo."""
        config = {
            "download_dir": self.dir_entry.get(),
            "cuts_dir": self.cuts_dir_entry.get(),
            "auth_method": self.auth_method_var.get(),
            "browser": self.browser_var.get(),
            "cookie_file": self.cookie_file_var.get(),
            "user_agent": self.ua_var.get(),
            "quality": self.quality_var.get(),
            "silence_threshold": self.silence_threshold_var.get(),
            "silence_duration": self.silence_duration_var.get(),
            "auto_retry": self.auto_retry_var.get(),
            "shutdown": self.shutdown_var.get(),
            "auto_cut": self.auto_cut_var.get(),
            "organize_by_music": self.organize_by_music_var.get(),
            "export_zip": self.export_zip_var.get(),
            "download_type": self.download_type.get(),
        }
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"❌ Erro ao salvar config: {e}")

    def setup_ui(self):
        """Cria a interface do usuário. DEVE SER CHAMADO APÓS load_config."""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Abas
        self.main_tab = ttk.Frame(self.notebook)
        self.local_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.stats_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.main_tab, text="📥 Download")
        self.notebook.add(self.local_tab, text="✂️ Corte Local")
        self.notebook.add(self.settings_tab, text="⚙️ Configurações")
        self.notebook.add(self.stats_tab, text="📊 Estatísticas")

        self.setup_main_tab()
        self.setup_local_tab()
        self.setup_settings_tab()
        self.setup_stats_tab()

    def setup_main_tab(self):
        """Configura a aba principal de download."""
        main = ttk.Frame(self.main_tab, padding="10")
        main.pack(fill="both", expand=True)

        # Título
        title_frame = ttk.Frame(main)
        title_frame.pack(fill="x", pady=(0, 15))

        ttk.Label(
            title_frame,
            text=f"{APP_NAME} v{VERSION}",
            font=("Arial", 18, "bold")
        ).pack(side="left")

        ttk.Label(
            title_frame,
            text="🎬 Downloader de Vídeos do YouTube",
            font=("Arial", 10),
            foreground="gray"
        ).pack(side="left", padx=10)

        # Status de autenticação
        auth_frame = ttk.Frame(main)
        auth_frame.pack(fill="x", pady=5)

        self.auth_status_var = tk.StringVar(value="🔴 Não autenticado")
        ttk.Label(
            auth_frame,
            textvariable=self.auth_status_var,
            font=("Arial", 10, "bold")
        ).pack(side="left")

        ttk.Button(
            auth_frame,
            text="🍪 Configurar Cookies",
            command=self.setup_cookies,
            width=18
        ).pack(side="left", padx=10)

        ttk.Button(
            auth_frame,
            text="🔍 Testar Autenticação",
            command=self.test_connection,
            width=18
        ).pack(side="left", padx=5)

        # URLs
        url_frame = ttk.LabelFrame(main, text="📹 URLs do YouTube", padding="10")
        url_frame.pack(fill="x", pady=10)

        ttk.Label(url_frame, text="Cole uma ou mais URLs (uma por linha):").pack(anchor="w")

        self.url_text = tk.Text(url_frame, height=5, width=80, font=("Consolas", 10))
        sb = ttk.Scrollbar(url_frame, orient="vertical", command=self.url_text.yview)
        self.url_text.configure(yscrollcommand=sb.set)
        self.url_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Configurações rápidas
        settings_frame = ttk.LabelFrame(main, text="⚙️ Configurações Rápidas", padding="10")
        settings_frame.pack(fill="x", pady=10)

        # Linha 1 - Diretório de Download
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill="x", pady=5)

        ttk.Label(row1, text="💾 Salvar em:").pack(side="left")
        self.dir_entry = ttk.Entry(row1, textvariable=self.dir_entry_val, width=50)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(row1, text="📁", width=3, command=self.browse_directory).pack(side="left")

        # Linha 2 - Diretório de Cortes
        row1b = ttk.Frame(settings_frame)
        row1b.pack(fill="x", pady=5)

        ttk.Label(row1b, text="✂️ Cortes em:").pack(side="left")
        self.cuts_dir_entry = ttk.Entry(row1b, textvariable=self.cuts_dir_entry_val, width=50)
        self.cuts_dir_entry.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(row1b, text="📁", width=3, command=self.browse_cuts_directory).pack(side="left")

        # Linha 3 - Tipo e Qualidade
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill="x", pady=5)

        ttk.Label(row2, text="📱 Tipo:").pack(side="left")
        ttk.Radiobutton(row2, text="🎬 Vídeo", variable=self.download_type, value="video").pack(side="left", padx=5)
        ttk.Radiobutton(row2, text="🎵 Áudio MP3", variable=self.download_type, value="audio").pack(side="left", padx=10)

        ttk.Label(row2, text="📊 Qualidade:").pack(side="left", padx=(20, 5))
        quality_combo = ttk.Combobox(
            row2,
            textvariable=self.quality_var,
            values=["Melhor", "1080p", "720p", "480p", "360p"],
            width=10,
            state="readonly"
        )
        quality_combo.pack(side="left")
        quality_combo.set("720p")

        # Linha 4 - Opções
        row3 = ttk.Frame(settings_frame)
        row3.pack(fill="x", pady=5)

        ttk.Checkbutton(row3, text="✂️ Corte Automático", variable=self.auto_cut_var).pack(side="left", padx=5)
        ttk.Checkbutton(row3, text="📁 Organizar por música", variable=self.organize_by_music_var).pack(side="left",
                                                                                                       padx=10)
        ttk.Checkbutton(row3, text="📦 Exportar ZIP", variable=self.export_zip_var).pack(side="left", padx=10)

        # Botões de ação
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=15)

        ttk.Button(btn_frame, text="📋 Analisar URLs", command=self.analyze_urls, width=15).pack(side="left", padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="⏹️ Parar", command=self.stop_download, state="disabled", width=12)
        self.stop_btn.pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🚀 Download em Lote", command=self.batch_download, width=18).pack(side="left",
                                                                                                     padx=5)

        ttk.Button(
            btn_frame,
            text="🔄 Verificar Dependências",
            command=self.show_dependencies,
            width=20
        ).pack(side="right", padx=5)

        # Progresso
        progress_frame = ttk.LabelFrame(main, text="📊 Progresso", padding="10")
        progress_frame.pack(fill="x", pady=10)

        self.progress = ttk.Progressbar(progress_frame, mode="determinate", length=400)
        self.progress.pack(fill="x", pady=5)

        self.status_var = tk.StringVar(value="✅ Pronto para iniciar...")
        ttk.Label(progress_frame, textvariable=self.status_var, font=("Arial", 9)).pack()

        # Log
        log_frame = ttk.LabelFrame(main, text="📝 Log", padding="5")
        log_frame.pack(fill="both", expand=True, pady=10)

        self.log_text = tk.Text(log_frame, height=10, wrap=tk.WORD, font=("Consolas", 9))
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    def setup_local_tab(self):
        """Configura a aba de corte local."""
        local = ttk.Frame(self.local_tab, padding="10")
        local.pack(fill="both", expand=True)

        # Título
        ttk.Label(
            local,
            text="✂️ Corte Local de Áudios",
            font=("Arial", 16, "bold")
        ).pack(pady=(0, 10))

        ttk.Label(
            local,
            text="Processe arquivos de áudio já baixados para cortar automaticamente detectando silêncios",
            font=("Arial", 10),
            foreground="gray"
        ).pack(pady=(0, 20))

        # Botão para instalar FFmpeg
        ffmpeg_frame = ttk.Frame(local)
        ffmpeg_frame.pack(fill="x", pady=5)

        ttk.Button(
            ffmpeg_frame,
            text="📦 Instalar/Atualizar FFmpeg",
            command=self.install_ffmpeg,
            width=25
        ).pack(side="left", padx=5)

        # Frame principal
        main_frame = ttk.LabelFrame(local, text="📁 Selecionar Arquivo", padding="10")
        main_frame.pack(fill="x", pady=10)

        # Seleção de arquivo
        file_frame = ttk.Frame(main_frame)
        file_frame.pack(fill="x", pady=5)

        ttk.Label(file_frame, text="Arquivo de Áudio:").pack(side="left")
        self.local_file_var = tk.StringVar()
        file_entry = ttk.Entry(file_frame, textvariable=self.local_file_var, width=60)
        file_entry.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(file_frame, text="📂", width=3, command=self.browse_local_file).pack(side="left")

        # Informações do arquivo
        info_frame = ttk.LabelFrame(main_frame, text="ℹ️ Informações", padding="10")
        info_frame.pack(fill="x", pady=10)

        self.local_info_var = tk.StringVar(value="Nenhum arquivo selecionado")
        ttk.Label(info_frame, textvariable=self.local_info_var).pack()

        # Opções de corte
        options_frame = ttk.LabelFrame(local, text="⚙️ Opções de Corte", padding="10")
        options_frame.pack(fill="x", pady=10)

        # Configurações de silêncio
        row1 = ttk.Frame(options_frame)
        row1.pack(fill="x", pady=5)

        ttk.Label(row1, text="Limiar de Silêncio:").pack(side="left")
        # Os mesmos valores alimentam tanto o corte local quanto o corte após download.
        self.local_threshold_var = self.silence_threshold_var
        ttk.Combobox(
            row1,
            textvariable=self.local_threshold_var,
            values=["-15dB", "-20dB", "-25dB", "-30dB", "-35dB", "-40dB", "-45dB", "-50dB"],
            width=10,
            state="readonly"
        ).pack(side="left", padx=5)

        ttk.Label(row1, text="Duração do Silêncio (s):").pack(side="left", padx=(20, 5))
        self.local_duration_var = self.silence_duration_var
        ttk.Combobox(
            row1,
            textvariable=self.local_duration_var,
            values=["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "5.0"],
            width=10,
            state="readonly"
        ).pack(side="left", padx=5)

        row2 = ttk.Frame(options_frame)
        row2.pack(fill="x", pady=5)

        self.local_organize_var = tk.BooleanVar(value=True)
        self.local_export_zip_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(row2, text="📁 Organizar por música (criar pasta)", variable=self.local_organize_var).pack(
            side="left", padx=5)
        ttk.Checkbutton(row2, text="📦 Exportar ZIP", variable=self.local_export_zip_var).pack(side="left", padx=20)

        # Pasta de saída
        output_frame = ttk.Frame(options_frame)
        output_frame.pack(fill="x", pady=5)

        ttk.Label(output_frame, text="📂 Pasta de Saída:").pack(side="left")
        self.local_output_var = tk.StringVar(value=self.cuts_dir_entry_val.get())
        output_entry = ttk.Entry(output_frame, textvariable=self.local_output_var, width=50)
        output_entry.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(output_frame, text="📁", width=3, command=self.browse_local_output).pack(side="left")

        # Botões de ação
        btn_frame = ttk.Frame(local)
        btn_frame.pack(fill="x", pady=20)

        ttk.Button(
            btn_frame,
            text="🔍 Detectar Silêncios",
            command=self.detect_silences_local,
            width=20
        ).pack(side="left", padx=5)

        ttk.Button(
            btn_frame,
            text="✂️ Cortar Áudio",
            command=self.cut_audio_local,
            width=20
        ).pack(side="left", padx=5)

        ttk.Button(
            btn_frame,
            text="⏹️ Parar",
            command=self.stop_cut_local,
            width=12
        ).pack(side="left", padx=5)

        # Progresso local
        progress_frame = ttk.LabelFrame(local, text="📊 Progresso do Corte", padding="10")
        progress_frame.pack(fill="x", pady=10)

        self.local_progress = ttk.Progressbar(progress_frame, mode="determinate", length=400)
        self.local_progress.pack(fill="x", pady=5)

        self.local_status_var = tk.StringVar(value="✅ Pronto para cortar...")
        ttk.Label(progress_frame, textvariable=self.local_status_var, font=("Arial", 9)).pack()

        # Log local
        log_frame = ttk.LabelFrame(local, text="📝 Log do Corte", padding="5")
        log_frame.pack(fill="both", expand=True, pady=10)

        self.local_log_text = tk.Text(log_frame, height=8, wrap=tk.WORD, font=("Consolas", 9))
        llsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.local_log_text.yview)
        self.local_log_text.configure(yscrollcommand=llsb.set)
        self.local_log_text.pack(side="left", fill="both", expand=True)
        llsb.pack(side="right", fill="y")

        self.local_log("🔍 Aguardando seleção de arquivo...")

    def setup_settings_tab(self):
        """Configura a aba de configurações."""
        settings = ttk.Frame(self.settings_tab, padding="10")
        settings.pack(fill="both", expand=True)

        # Autenticação
        auth_frame = ttk.LabelFrame(settings, text="🍪 Autenticação", padding="10")
        auth_frame.pack(fill="x", pady=10)

        ttk.Label(auth_frame, text="Método:").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(
            auth_frame,
            textvariable=self.auth_method_var,
            values=["browser", "file", "none"],
            state="readonly",
            width=15
        ).grid(row=0, column=1, sticky="w", padx=5)

        ttk.Label(auth_frame, text="Navegador:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Combobox(
            auth_frame,
            textvariable=self.browser_var,
            values=["chrome", "edge", "firefox", "opera", "brave", "chromium"],
            state="readonly",
            width=15
        ).grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(auth_frame, text="Arquivo Cookie:").grid(row=2, column=0, sticky="w", pady=5)
        cookie_entry = ttk.Entry(auth_frame, textvariable=self.cookie_file_var, width=40)
        cookie_entry.grid(row=2, column=1, sticky="w", padx=5)
        ttk.Button(auth_frame, text="📂", width=3, command=self.browse_cookie_file).grid(row=2, column=2, padx=5)

        # Rede
        network_frame = ttk.LabelFrame(settings, text="🌐 Rede", padding="10")
        network_frame.pack(fill="x", pady=10)

        ttk.Label(network_frame, text="User Agent:").pack(anchor="w")
        ua_entry = ttk.Entry(network_frame, textvariable=self.ua_var, width=80)
        ua_entry.pack(fill="x", pady=5)

        # Comportamento
        behavior_frame = ttk.LabelFrame(settings, text="⚙️ Comportamento", padding="10")
        behavior_frame.pack(fill="x", pady=10)

        ttk.Checkbutton(behavior_frame, text="🔄 Tentativa automática em erro", variable=self.auto_retry_var).pack(
            anchor="w", pady=2)
        ttk.Checkbutton(behavior_frame, text="💻 Desligar PC após downloads", variable=self.shutdown_var).pack(
            anchor="w", pady=2)

        # Botões
        btn_frame = ttk.Frame(settings)
        btn_frame.pack(fill="x", pady=20)

        ttk.Button(btn_frame, text="💾 Salvar Configurações", command=self.save_config, width=20).pack(side="left",
                                                                                                      padx=5)
        ttk.Button(btn_frame, text="🔄 Resetar Padrões", command=self.reset_config, width=20).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="⬆️ Verificar Atualizações", command=self.check_app_update_manual, width=22).pack(
            side="left", padx=5)
        ttk.Button(
            btn_frame, text="🧩 Atualizar Componentes", command=self.update_components_manual, width=25
        ).pack(side="left", padx=5)

    def setup_stats_tab(self):
        """Configura a aba de estatísticas."""
        stats = ttk.Frame(self.stats_tab, padding="10")
        stats.pack(fill="both", expand=True)

        ttk.Label(stats, text="📊 Estatísticas de Download", font=("Arial", 14, "bold")).pack(pady=10)
        ttk.Label(stats, text=f"Log completo: {self.session_log_file}", foreground="gray").pack(pady=(0, 5))

        stats_frame = ttk.LabelFrame(stats, text="Resumo", padding="15")
        stats_frame.pack(fill="x", pady=10)

        grid_frame = ttk.Frame(stats_frame)
        grid_frame.pack()

        ttk.Label(grid_frame, text="📦 Total:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5,
                                                                                padx=10)
        self.total_label = ttk.Label(grid_frame, text="0", font=("Arial", 12, "bold"))
        self.total_label.grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(grid_frame, text="✅ Sucessos:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5,
                                                                                   padx=10)
        self.success_label = ttk.Label(grid_frame, text="0", foreground="green", font=("Arial", 12, "bold"))
        self.success_label.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(grid_frame, text="❌ Falhas:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5,
                                                                                 padx=10)
        self.failed_label = ttk.Label(grid_frame, text="0", foreground="red", font=("Arial", 12, "bold"))
        self.failed_label.grid(row=2, column=1, sticky="w", padx=10)

        ttk.Label(grid_frame, text="📈 Taxa de Sucesso:", font=("Arial", 10, "bold")).grid(row=3, column=0, sticky="w",
                                                                                          pady=5, padx=10)
        self.rate_label = ttk.Label(grid_frame, text="0%", font=("Arial", 12, "bold"))
        self.rate_label.grid(row=3, column=1, sticky="w", padx=10)

        history_frame = ttk.LabelFrame(stats, text="📜 Histórico", padding="10")
        history_frame.pack(fill="both", expand=True, pady=10)

        self.history_text = tk.Text(history_frame, height=8, wrap=tk.WORD, font=("Consolas", 9))
        hsb = ttk.Scrollbar(history_frame, orient="vertical", command=self.history_text.yview)
        self.history_text.configure(yscrollcommand=hsb.set)
        self.history_text.pack(side="left", fill="both", expand=True)
        hsb.pack(side="right", fill="y")

        btn_frame = ttk.Frame(stats)
        btn_frame.pack(fill="x", pady=10)

        ttk.Button(btn_frame, text="🗑️ Limpar Estatísticas", command=self.clear_stats, width=18).pack(side="left",
                                                                                                      padx=5)
        ttk.Button(btn_frame, text="📤 Exportar Log", command=self.export_log, width=18).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="📂 Abrir Local do Log", command=self.open_log_location, width=18).pack(side="left", padx=5)

    # ============ MÉTODOS DE GERENCIAMENTO ============

    def check_app_update_background(self):
        threading.Thread(target=self._check_app_update, args=(False,), daemon=True).start()

    def check_app_update_manual(self):
        self._ui(lambda: self.status_var.set("🔄 Verificando atualização do aplicativo..."))
        threading.Thread(target=self._check_app_update, args=(True,), daemon=True).start()

    def update_components_manual(self):
        """Força uma atualização segura de yt-dlp, FFmpeg e Deno."""
        self.status_var.set("🔄 Atualizando componentes de download...")
        self.check_dependencies_background(force_update=True, manual=True)

    def _check_app_update(self, manual=False):
        try:
            info = self.update_manager.check("stable")
            if not info:
                if manual:
                    self._ui(lambda: messagebox.showinfo("Atualizações", f"Você já está usando a versão mais recente ({VERSION})."))
                return
            notes = info.notes or "Melhorias e correções gerais."
            prompt = f"Nova versão {info.version} disponível.\n\n{notes}\n\nDeseja baixar e instalar agora?"
            if info.required:
                prompt = f"A atualização {info.version} é necessária para continuar.\n\n{notes}\n\nInstalar agora?"
            self._ui(lambda i=info, p=prompt: self._offer_update(i, p))
        except Exception as error:
            self.log(f"⚠️ Verificação de atualização indisponível: {error}")
            if manual:
                self._ui(lambda e=error: messagebox.showwarning("Atualizações", f"Não foi possível verificar agora:\n{e}"))

    def _offer_update(self, info, prompt):
        if messagebox.askyesno("Atualização disponível", prompt):
            threading.Thread(target=self._download_and_install_update, args=(info,), daemon=True).start()

    def _download_and_install_update(self, info):
        try:
            self.log(f"⬇️ Baixando atualização {info.version}...")
            path = self.update_manager.download(
                info, progress=lambda value: self._ui(
                    lambda v=value: self.status_var.set(f"⬇️ Atualização: {v:.0f}%")
                )
            )
            self.log("✅ Atualização validada; reiniciando o aplicativo")
            self.update_manager.install_and_restart(path)
            self._ui(self.root.destroy)
        except Exception as error:
            self.log(f"❌ Falha na atualização do aplicativo: {error}")
            self._ui(lambda e=error: messagebox.showerror("Atualização", f"A atualização não pôde ser instalada:\n{e}"))

    def show_dependencies(self):
        """Mostra o status das dependências."""

        def check():
            status = self.dep_manager.check_dependencies()

            msg = "📦 Status das Dependências:\n\n"
            for key, dep in self.dep_manager.dependencies.items():
                s = status.get(key, {})
                installed = s.get("installed", False)
                icon = "✅" if installed else "❌"
                msg += f"{icon} {dep['name']}\n"

            msg += f"\n📁 Pasta: {self.dep_manager.deps_dir}"

            self._ui(lambda: messagebox.showinfo("Dependências", msg))

        threading.Thread(target=check, daemon=True).start()

    def install_ffmpeg(self):
        """Atualiza o conjunto gerenciado, incluindo o FFmpeg usado nos cortes."""
        self._ui(lambda: self.local_log("📦 Atualizando componentes de corte..."))
        self._ui(lambda: self.local_progress.start())

        def install():
            ready = self.runtime.ensure(force_update=True)
            self._ui(lambda: self.local_progress.stop())
            if ready:
                self._ui(lambda: self.local_log("✅ FFmpeg e componentes prontos!"))
                self._ui(lambda: self.local_status_var.set("✅ Componentes atualizados!"))
            else:
                self._ui(lambda: self.local_log("❌ Não foi possível preparar todos os componentes"))
                self._ui(lambda: self.local_status_var.set("❌ Erro na atualização"))

        threading.Thread(target=install, daemon=True).start()

        threading.Thread(target=install, daemon=True).start()

    # ============ MÉTODOS DO CORTE LOCAL ============

    def browse_local_file(self):
        """Seleciona arquivo de áudio para corte local."""
        filepath = filedialog.askopenfilename(
            title="Selecione um arquivo de áudio",
            filetypes=[
                ("Áudio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                ("MP3", "*.mp3"),
                ("WAV", "*.wav"),
                ("Todos os arquivos", "*.*")
            ]
        )
        if filepath:
            self.local_file_var.set(filepath)
            self.local_info_var.set(f"📁 {os.path.basename(filepath)}")
            self.local_log(f"📂 Arquivo selecionado: {os.path.basename(filepath)}")

            duration = get_duration(filepath)
            if duration > 0:
                self.local_info_var.set(f"📁 {os.path.basename(filepath)} | ⏱️ {format_time(duration)}")
                self.local_log(f"⏱️ Duração: {format_time(duration)}")

    def browse_local_output(self):
        """Seleciona pasta de saída para cortes locais."""
        directory = filedialog.askdirectory()
        if directory:
            self.local_output_var.set(directory)
            self.local_log(f"📁 Pasta de saída: {directory}")

    def detect_silences_local(self):
        """Detecta silêncios no arquivo selecionado."""
        audio_file = self.local_file_var.get()
        if not audio_file or not os.path.exists(audio_file):
            messagebox.showerror("Erro", "Selecione um arquivo de áudio válido!")
            return

        if not shutil.which("ffmpeg"):
            messagebox.showerror("Erro", "FFmpeg não encontrado! Clique em 'Instalar/Atualizar FFmpeg'.")
            return

        self.is_cutting = True
        self.local_status_var.set("🔍 Detectando silêncios...")
        self.local_progress.start()

        threading.Thread(target=self._detect_silences_thread, daemon=True).start()

    def _detect_silences_thread(self):
        """Thread para detectar silêncios."""
        try:
            audio_file = self.local_file_var.get()
            threshold = self.local_threshold_var.get()
            duration = self.local_duration_var.get()

            self.local_log(f"🔍 Detectando silêncios: threshold={threshold}, duration={duration}s")

            cuts, total_dur = self._detect_silences(audio_file, threshold, duration)

            if cuts:
                self.local_log(f"✅ {len(cuts)} silêncios detectados")
                for i, (s, e) in enumerate(cuts[:5], 1):
                    self.local_log(f"   Corte {i}: {format_time(s)} - {format_time(e)} ({e - s:.1f}s)")
                if len(cuts) > 5:
                    self.local_log(f"   ... e mais {len(cuts) - 5} cortes")

                self.local_info_var.set(f"📁 {os.path.basename(audio_file)} | 🎵 {len(cuts)} cortes detectados")
                self.local_status_var.set(f"✅ {len(cuts)} cortes detectados! Clique em 'Cortar Áudio'")

                messagebox.showinfo(
                    "Detecção Concluída",
                    f"{len(cuts)} cortes detectados!\n\n"
                    f"Duração total: {format_time(total_dur)}\n"
                    f"Primeiro corte: {format_time(cuts[0][0])} - {format_time(cuts[0][1])}\n\n"
                    "Clique em 'Cortar Áudio' para processar."
                )
            else:
                self.local_log("ℹ️ Nenhum silêncio detectado")
                self.local_status_var.set("ℹ️ Nenhum silêncio detectado")
                messagebox.showinfo("Detecção", "Nenhum silêncio significativo detectado no áudio.")

        except Exception as e:
            self.local_log(f"❌ Erro na detecção: {e}")
            self.local_status_var.set("❌ Erro na detecção")
            messagebox.showerror("Erro", f"Falha na detecção:\n{str(e)}")
        finally:
            self.is_cutting = False
            self.local_progress.stop()

    def cut_audio_local(self):
        """Corta o áudio local baseado nos silêncios detectados."""
        audio_file = self.local_file_var.get()
        if not audio_file or not os.path.exists(audio_file):
            messagebox.showerror("Erro", "Selecione um arquivo de áudio válido!")
            return

        if not shutil.which("ffmpeg"):
            messagebox.showerror("Erro", "FFmpeg não encontrado! Clique em 'Instalar/Atualizar FFmpeg'.")
            return

        self.is_cutting = True
        self.local_status_var.set("✂️ Cortando áudio...")

        threading.Thread(target=self._cut_audio_local_thread, daemon=True).start()

    def _cut_audio_local_thread(self):
        """Thread para cortar áudio local."""
        try:
            audio_file = self.local_file_var.get()
            threshold = self.local_threshold_var.get()
            duration = self.local_duration_var.get()
            output_dir = self.local_output_var.get()
            organize = self.local_organize_var.get()
            export_zip = self.local_export_zip_var.get()

            self.local_log(f"✂️ Iniciando corte do áudio: {os.path.basename(audio_file)}")

            cuts, total_dur = self._detect_silences(audio_file, threshold, duration)

            if not cuts:
                self.local_log("ℹ️ Nenhum corte a ser feito")
                self.local_status_var.set("ℹ️ Nenhum corte a ser feito")
                messagebox.showinfo("Info", "Nenhum corte detectado no áudio.")
                return

            allowed, reason = self._trial_can(media_seconds=total_dur, tracks=len(cuts))
            if not allowed:
                self.local_log(f"⛔ {reason}")
                self._ui_local(lambda r=reason: self.local_status_var.set(f"⛔ {r}"))
                self._ui_local(lambda r=reason: messagebox.showwarning("Limite da avaliação", r))
                return

            self.local_log(f"🎯 {len(cuts)} cortes para processar")

            base_name = os.path.splitext(os.path.basename(audio_file))[0]

            if organize:
                output_dir = os.path.join(output_dir, sanitize_filename(base_name))

            ensure_directory(output_dir)

            cut_files = []
            total = len(cuts)

            for i, (s, e) in enumerate(cuts, 1):
                if not self.is_cutting:
                    self.local_log("⏹️ Corte interrompido")
                    break
                percent = (i / total) * 100
                self._ui_local(lambda: self.local_progress.configure(value=percent))
                self._ui_local(lambda i=i, total=total, s=s, e=e: self.local_status_var.set(
                    f"✂️ Cortando {i}/{total}: {format_time(s)} - {format_time(e)}"
                ))

                out_file = os.path.join(output_dir, f"{sanitize_filename(base_name)}_corte_{i:03d}.mp3")

                cmd = [
                    str(self.runtime.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(s), "-to", str(e), "-i", audio_file,
                    "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out_file
                ]

                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )

                if result.returncode == 0:
                    cut_files.append(out_file)
                    self.local_log(f"🎵 Corte {i}: {format_time(s)} - {format_time(e)}")
                else:
                    self.local_log(f"❌ Erro no corte {i}: {result.stderr[:100]}")

            self.local_log(f"✅ {len(cut_files)} cortes gerados com sucesso!")
            self.local_log(f"📁 Pasta: {output_dir}")
            if cut_files:
                self._trial_consume(media_seconds=total_dur, tracks=len(cut_files))

            if export_zip and cut_files:
                zip_path = os.path.join(
                    os.path.dirname(output_dir),
                    f"{sanitize_filename(base_name)}_cortes.zip"
                )
                try:
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fpath in cut_files:
                            zf.write(fpath, os.path.basename(fpath))
                    self.local_log(f"📦 ZIP criado: {zip_path}")
                except Exception as e:
                    self.local_log(f"❌ Erro ao criar ZIP: {e}")

            self.local_status_var.set(f"✅ {len(cut_files)} cortes concluídos!")

            messagebox.showinfo(
                "Corte Concluído",
                f"✅ {len(cut_files)} cortes gerados!\n\n"
                f"📁 Local: {output_dir}\n"
                f"{'📦 ZIP exportado' if export_zip else ''}"
            )

        except Exception as e:
            self.local_log(f"❌ Erro no corte: {e}")
            self.local_status_var.set("❌ Erro no corte")
            messagebox.showerror("Erro", f"Falha no corte:\n{str(e)}")
        finally:
            self.is_cutting = False
            self.local_progress.stop()
            self._ui_local(lambda: self.local_progress.configure(value=0))

    def _detect_silences(self, audio_file: str, threshold: str, duration: str) -> tuple:
        """Detecta silêncios no áudio."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise Exception("FFmpeg/ffprobe não encontrado")

        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Arquivo não encontrado: {audio_file}")

        cmd = [
            str(self.runtime.ffmpeg), "-hide_banner", "-i", audio_file,
            "-af", f"silencedetect=noise={threshold}:d={duration}",
            "-f", "null", "-"
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        events = []
        stderr_text = result.stderr or ""
        if result.returncode not in (0, 1) and not stderr_text:
            raise Exception(f"FFmpeg terminou sem retornar detalhes (código {result.returncode})")
        for line in stderr_text.splitlines():
            start_match = re.search(r"silence_start:\s*([\d.]+)", line)
            end_match = re.search(r"silence_end:\s*([\d.]+)", line)
            if start_match:
                events.append(("start", float(start_match.group(1))))
            if end_match:
                events.append(("end", float(end_match.group(1))))

        probe = self.runtime.ffprobe
        if not probe:
            raise Exception("ffprobe não encontrado")
        duration_result = subprocess.run(
            [str(probe), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_file],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        total_dur = float(duration_result.stdout.strip() or "0")
        if total_dur <= 0:
            raise Exception("Não foi possível obter duração do áudio")

        cuts = []
        content_start = 0.0
        in_silence = False
        for kind, position in events:
            if kind == "start" and not in_silence:
                if position - content_start > 2.0:
                    cuts.append((content_start, min(position, total_dur)))
                in_silence = True
            elif kind == "end" and in_silence:
                content_start = min(position, total_dur)
                in_silence = False
        if not in_silence and total_dur - content_start > 2.0:
            cuts.append((content_start, total_dur))

        return cuts, total_dur

    def stop_cut_local(self):
        """Para o corte local."""
        self.is_cutting = False
        self.local_status_var.set("⏹️ Interrompido")
        self.local_log("⏹️ Corte interrompido pelo usuário")

    def local_log(self, message):
        """Adiciona mensagem ao log local."""
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp_str}] [CORTE] {message}\n"
        self._write_session_line(log_line)
        self._ui_local(lambda: self._append_local_log(log_line))
        if hasattr(self, "history_text"):
            self._ui(lambda: self._append_history(log_line))

    def _append_local_log(self, line):
        """Adiciona linha ao log local."""
        self.local_log_text.insert(tk.END, line)
        self.local_log_text.see(tk.END)

    def _ui_local(self, func):
        """Executa função na thread principal para UI local."""
        self.root.after(0, func)

    # ============ MÉTODOS EXISTENTES ============

    def setup_cookies(self):
        """Janela para configurar cookies."""
        win = tk.Toplevel(self.root)
        win.title("🍪 Configurar Cookies")
        win.geometry("520x420")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding="20")
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="🍪 Configuração de Cookies", font=("Arial", 14, "bold")).pack(pady=(0, 20))
        ttk.Label(frame, text="Selecione o navegador que você usa para logar no YouTube:", wraplength=400).pack(
            pady=(0, 15))

        browser_frame = ttk.Frame(frame)
        browser_frame.pack(pady=10)

        self.temp_browser_var = tk.StringVar(value="chrome")

        for text, value in [("🌐 Chrome", "chrome"), ("🌐 Edge", "edge"), ("🦊 Firefox", "firefox")]:
            ttk.Radiobutton(browser_frame, text=text, variable=self.temp_browser_var, value=value).pack(side="left",
                                                                                                        padx=10)

        status_label = ttk.Label(frame, text="Navegador selecionado: Chrome", font=("Arial", 10))
        status_label.pack(pady=10)

        def capture():
            browser = self.temp_browser_var.get()
            status_label.config(text=f"🔄 Capturando cookies do {browser}...")
            win.update()
            threading.Thread(target=self._capture_cookies_thread, args=(browser, status_label, win),
                             daemon=True).start()

        ttk.Button(frame, text="🔓 Capturar Cookies Automaticamente", command=capture, width=30).pack(pady=10)

        info_frame = ttk.LabelFrame(frame, text="📋 Instruções", padding="10")
        info_frame.pack(fill="x", pady=10)

        instructions = """
        1. Faça login no YouTube no navegador selecionado
        2. Mantenha o navegador aberto
        3. Clique no botão para capturar os cookies
        4. Pronto! O downloader usará sua sessão
        """
        ttk.Label(info_frame, text=instructions, wraplength=400, justify="left").pack()
        ttk.Button(frame, text="Fechar", command=win.destroy, width=15).pack(pady=10)

    def _capture_cookies_thread(self, browser, status_label, window):
        """Thread para capturar cookies."""
        try:
            cookie_file = self.cookie_manager.get_cookies_auto(browser)
            if cookie_file:
                self.cookie_file_var.set(cookie_file)
                self.auth_method_var.set("file")
                self.save_config()
                self._ui(lambda: self.auth_status_var.set(f"✅ Autenticado via {browser}"))
                self._ui(lambda: status_label.config(text=f"✅ Cookies capturados com sucesso!"))
                self.log(f"✅ Cookies capturados do {browser}")
                self.test_connection()
                window.after(1500, window.destroy)
            else:
                self._ui(lambda: status_label.config(text="❌ Falha ao capturar cookies"))
                self.log(f"❌ Falha ao capturar cookies do {browser}")
        except Exception as e:
            self._ui(lambda: status_label.config(text=f"❌ Erro: {str(e)[:50]}..."))
            self.log(f"❌ Erro ao capturar cookies: {e}")

    def get_ydl_opts(self):
        """Obtém opções para o yt-dlp."""
        download_dir = self.dir_entry.get()
        ensure_directory(download_dir)

        base_opts = {
            "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
            "retries": 10,
            "fragment_retries": 10,
            "http_headers": {"User-Agent": self.ua_var.get()},
            "force_ipv4": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
        }

        auth = self.auth_method_var.get()
        cookie_file = self.cookie_file_var.get()

        if auth == "file" and cookie_file and os.path.exists(cookie_file):
            base_opts["cookiefile"] = cookie_file
        elif auth == "browser":
            try:
                import browser_cookie3
                browser = self.browser_var.get()
                self.log(f"🍪 Tentando extrair cookies do {browser}...")
            except:
                pass

        if self.download_type.get() == "video":
            quality_map = {
                "Melhor": "bestvideo+bestaudio/best",
                "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
                "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
                "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
            }
            base_opts["format"] = quality_map.get(self.quality_var.get(),
                                                  "bestvideo[height<=720]+bestaudio/best[height<=720]")
            base_opts["merge_output_format"] = "mp4"
        else:
            base_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })

        return base_opts

    def test_connection(self):
        """Testa a conexão com o YouTube."""
        threading.Thread(target=self._test_connection_thread, daemon=True).start()

    def _test_connection_thread(self):
        try:
            self._ui(lambda: self.status_var.set("🔍 Testando conexão..."))
            self._ui(self.progress.start)
            if not self.runtime.ensure():
                raise RuntimeError("componentes necessários indisponíveis")
            info = self.download_engine.probe("https://www.youtube.com/watch?v=jNQXAC9IVRw")
            self.log(f"✅ Conexão OK: {info.get('title', 'N/A')}")
            self._ui(lambda: self.status_var.set("✅ Conexão OK"))
            self._ui(lambda: self.auth_status_var.set("✅ YouTube acessível"))
        except Exception as e:
            self.log(f"❌ Falha na conexão: {str(e)[:100]}")
            self._ui(lambda: self.status_var.set("❌ Falha na conexão"))
            self._ui(lambda: self.auth_status_var.set("❌ Falha na autenticação"))
        finally:
            self._ui(self.progress.stop)

    def analyze_urls(self):
        """Analisa as URLs antes do download."""
        urls = self.get_urls_from_text()
        if not urls:
            messagebox.showwarning("Aviso", "⚠️ Nenhuma URL para analisar!")
            return
        threading.Thread(target=self._analyze_urls_thread, args=(urls,), daemon=True).start()

    def _analyze_urls_thread(self, urls):
        try:
            if not self.runtime.ensure():
                raise RuntimeError("componentes necessários indisponíveis")
            for i, url in enumerate(urls, 1):
                self._ui(lambda i=i, n=len(urls): self.status_var.set(f"📋 Analisando {i}/{n}..."))
                try:
                    info = self.download_engine.probe(url)
                    if info.get("_type") == "playlist":
                        entries = info.get("entries") or []
                        self.log(f"📋 Playlist {i}: {info.get('title', 'N/A')[:50]} | {len(entries)} vídeos")
                    else:
                        self.log(f"🎬 Vídeo {i}: {info.get('title', 'N/A')[:50]}")
                except Exception as e:
                    self.log(f"❌ Erro na URL {i}: {str(e)[-200:]}")
            self._ui(lambda: self.status_var.set("✅ Análise concluída"))
        except Exception as e:
            self.log(f"❌ Erro na análise: {e}")

    def batch_download(self):
        """Inicia download em lote."""
        urls = self.get_urls_from_text()
        if not urls:
            messagebox.showwarning("Aviso", "⚠️ Insira URLs para download!")
            return

        valid_urls = [u for u in urls if is_valid_youtube_url(u)]
        if not valid_urls:
            messagebox.showerror("Erro", "❌ Nenhuma URL válida encontrada!")
            return

        self.is_downloading = True
        self._ui(lambda: self.stop_btn.config(state="normal"))
        self.log(f"🚀 Iniciando download de {len(valid_urls)} itens...")
        threading.Thread(target=self._batch_download_thread, args=(valid_urls,), daemon=True).start()

    def get_urls_from_text(self):
        text = self.url_text.get(1.0, tk.END)
        return [u.strip() for u in text.split("\n") if u.strip()]

    def _batch_download_thread(self, urls):
        total = len(urls)
        download_dir = pathlib.Path(self.dir_entry_val.get())
        audio = self.download_type.get() == "audio"
        quality = self.quality_var.get()
        auto_cut = self.auto_cut_var.get()
        preferred_browser = self.browser_var.get()

        if not self.runtime.ensure():
            self.log("❌ Não foi possível preparar os componentes necessários")
            self.is_downloading = False
            self._ui(lambda: self.stop_btn.config(state="disabled"))
            return

        for i, url in enumerate(urls, 1):
            if not self.is_downloading:
                break

            self.download_count += 1
            success = False
            chapters = []
            media_duration = 0
            if (auto_cut and audio) or self._is_trial():
                try:
                    info = self.download_engine.probe(url)
                    media_duration = int(float(info.get("duration") or 0))
                    chapters = chapters_from_info(info)
                    if chapters:
                        self.log(f"📑 {len(chapters)} faixas encontradas nos capítulos/descrição")
                    else:
                        self.log("ℹ️ Sem lista de faixas válida; será usada detecção de silêncio")
                except Exception as metadata_error:
                    if self.download_engine.looks_like_auth_error(metadata_error):
                        for browser in ordered_browsers(preferred_browser, self.download_engine.installed_browsers()):
                            try:
                                info = self.download_engine.probe(url, browser)
                                chapters = chapters_from_info(info)
                                if chapters:
                                    self.log(f"📑 {len(chapters)} faixas encontradas usando a sessão do {browser.title()}")
                                    break
                            except Exception:
                                continue
                    if not chapters:
                        self.log(f"⚠️ Não foi possível ler a descrição; será usado silêncio: {str(metadata_error)[-180:]}")
            if self._is_trial():
                if media_duration <= 0:
                    self.log("⛔ Avaliação: não foi possível confirmar a duração deste conteúdo.")
                    self.failed_count += 1
                    self._ui(self.update_stats)
                    continue
                allowed, reason = self._trial_can(downloads=1, media_seconds=media_duration)
                if not allowed:
                    self.log(f"⛔ {reason}")
                    self._ui(lambda r=reason: self.status_var.set(f"⛔ {r}"))
                    break
            self._ui(lambda i=i, n=total: self.status_var.set(f"⬇️ Baixando {i}/{n}..."))
            self._ui(lambda v=((i - 1) / total * 100): self.progress.configure(value=v))
            try:
                result = self.download_engine.download(
                    url, download_dir, audio, quality, None,
                    cancelled=lambda: not self.is_downloading,
                    status=lambda message: self._ui(lambda m=message: self.status_var.set(f"⬇️ {m}")),
                )
                success = True
            except DownloadCancelled:
                break
            except Exception as first_error:
                # Cookies são usados somente quando o YouTube realmente pede autenticação.
                if self.download_engine.looks_like_auth_error(first_error):
                    result = self._retry_with_browsers(
                        url, download_dir, audio, quality, preferred_browser
                    )
                    success = result is not None
                else:
                    self.log(f"❌ Erro no download {i}: {str(first_error)[-500:]}")
                    result = None

            if success and result is not None:
                self._trial_consume(downloads=1, media_seconds=media_duration)
                for final_file in result.files:
                    self.log(f"✅ Arquivo final: {final_file.name}")
                    if auto_cut and audio and final_file.suffix.lower() == ".mp3":
                        self._ui(lambda: self.status_var.set("✂️ Gerando cortes..."))
                        if chapters:
                            self.cut_audio_by_chapters(str(final_file), chapters)
                        else:
                            self.auto_cut_audio(str(final_file))
                self.log(f"✅ Download {i}/{total} concluído")

            if success:
                self.success_count += 1
            elif self.is_downloading:
                self.failed_count += 1

            self._ui(self.update_stats)

        self.is_downloading = False
        self._ui(lambda: self.stop_btn.config(state="disabled"))
        self._ui(lambda: self.progress.configure(value=100))

        msg = f"✅ Concluído: {self.success_count} OK, {self.failed_count} falhas"
        self._ui(lambda m=msg: self.status_var.set(m))
        self.log(f"📦 Download em lote finalizado! {msg}")

        if self.shutdown_var.get() and self.success_count > 0:
            self.log("💻 Desligando o computador em 60 segundos...")
            if sys.platform == "win32":
                os.system("shutdown /s /t 60")
            else:
                os.system("shutdown -h +1")

        self.save_config()

    def _retry_with_browsers(self, url, download_dir, audio, quality, preferred_browser):
        browsers = ordered_browsers(preferred_browser, self.download_engine.installed_browsers())
        if not browsers:
            self.log("⚠️ O YouTube pediu autenticação, mas nenhum navegador compatível foi encontrado")
            return None
        for browser in browsers:
            if not self.is_downloading:
                return None
            self.log(f"🍪 Tentando sessão existente do {browser.title()}...")
            try:
                result = self.download_engine.download(
                    url, download_dir, audio, quality, browser,
                    cancelled=lambda: not self.is_downloading,
                    status=lambda message: self._ui(lambda m=message: self.status_var.set(f"⬇️ {m}")),
                )
                self._ui(lambda b=browser: self.auth_status_var.set(f"✅ Sessão do {b.title()}"))
                return result
            except DownloadCancelled:
                return None
            except Exception as error:
                self.log(f"⚠️ Sessão do {browser.title()} indisponível: {str(error)[-180:]}")
        self.log("❌ Nenhuma sessão de navegador pôde autenticar no YouTube")
        return None

    def cut_audio_by_chapters(self, audio_file: str, chapters: List[Chapter]) -> bool:
        """Gera faixas nomeadas usando capítulos nativos ou horários da descrição."""
        try:
            if not self.runtime.ffmpeg or not os.path.exists(audio_file):
                return False
            allowed, reason = self._trial_can(tracks=len(chapters))
            if not allowed:
                self.log(f"⛔ {reason}")
                return False
            base = os.path.splitext(os.path.basename(audio_file))[0]
            out_dir = self.cuts_dir_entry_val.get()
            if self.organize_by_music_var.get():
                out_dir = os.path.join(out_dir, sanitize_filename(base))
            if not ensure_directory(out_dir):
                raise Exception(f"não foi possível criar a pasta {out_dir}")

            self.is_cutting = True
            cut_files = []
            track_list = []
            for index, chapter in enumerate(chapters, 1):
                if not self.is_cutting:
                    break
                title = sanitize_filename(chapter.title) or f"Faixa {index:02d}"
                out_file = os.path.join(out_dir, f"{index:02d} - {title}.mp3")
                self.log(
                    f"🎵 Faixa {index}/{len(chapters)}: {chapter.title} "
                    f"({format_time(chapter.start)}–{format_time(chapter.end)})"
                )
                command = [
                    str(self.runtime.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(chapter.start), "-to", str(chapter.end), "-i", audio_file,
                    "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
                    "-metadata", f"title={chapter.title}",
                    "-metadata", f"track={index}/{len(chapters)}", out_file,
                ]
                result = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if result.returncode == 0 and os.path.exists(out_file):
                    cut_files.append(out_file)
                    track_list.append(f"{format_time(chapter.start)} - {chapter.title}")
                else:
                    self.log(f"❌ Falha na faixa {index}: {(result.stderr or 'erro desconhecido')[-250:]}")

            if track_list:
                list_path = os.path.join(out_dir, "lista_de_faixas.txt")
                with open(list_path, "w", encoding="utf-8") as stream:
                    stream.write("\n".join(track_list) + "\n")
            if self.export_zip_var.get() and cut_files:
                zip_path = os.path.join(os.path.dirname(out_dir), f"{sanitize_filename(base)}_faixas.zip")
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as package:
                    for path in cut_files:
                        package.write(path, os.path.basename(path))
            self.log(f"✅ {len(cut_files)} faixas nomeadas geradas em: {out_dir}")
            self._trial_consume(tracks=len(cut_files))
            return len(cut_files) == len(chapters)
        except Exception as error:
            self.log(f"❌ Erro ao cortar pelos capítulos: {error}")
            return False
        finally:
            self.is_cutting = False

    def auto_cut_audio(self, audio_file):
        """Corta o áudio automaticamente detectando silêncio."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.log("⚠️ FFmpeg/ffprobe não encontrado")
            return False

        try:
            if not os.path.exists(audio_file):
                self.log(f"❌ Arquivo não encontrado: {audio_file}")
                return False

            base = os.path.splitext(os.path.basename(audio_file))[0]
            self.log(f"✂️ Cortando áudio: {base}")

            threshold = self.silence_threshold_var.get()
            duration = self.silence_duration_var.get()

            cuts, total_dur = self._detect_silences(audio_file, threshold, duration)

            if not cuts:
                self.log("ℹ️ Nenhum corte gerado (sem silêncios significativos)")
                return True

            allowed, reason = self._trial_can(tracks=len(cuts))
            if not allowed:
                self.log(f"⛔ {reason}")
                return False

            out_dir = self.cuts_dir_entry.get()
            if self.organize_by_music_var.get():
                out_dir = os.path.join(out_dir, sanitize_filename(base))

            ensure_directory(out_dir)
            self.log(f"📁 Salvando cortes em: {out_dir}")

            cut_files = []
            total = len(cuts)

            self.is_cutting = True
            for i, (s, e) in enumerate(cuts, 1):
                if not self.is_cutting:
                    self.log("⏹️ Corte automático interrompido")
                    break
                self.log(f"🎵 Corte {i}/{total}: {format_time(s)} - {format_time(e)}")

                out_file = os.path.join(out_dir, f"{sanitize_filename(base)}_corte_{i:03d}.mp3")

                cmd = [
                    str(self.runtime.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(s), "-to", str(e), "-i", audio_file,
                    "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out_file
                ]

                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )

                if result.returncode == 0:
                    cut_files.append(out_file)
                else:
                    self.log(f"❌ Erro no corte {i}: {result.stderr[:100]}")

            self.log(f"✅ {len(cut_files)} cortes gerados com sucesso!")
            self._trial_consume(tracks=len(cut_files))

            if self.export_zip_var.get() and cut_files:
                zip_path = os.path.join(
                    os.path.dirname(out_dir),
                    f"{sanitize_filename(base)}_cortes.zip"
                )
                try:
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fpath in cut_files:
                            zf.write(fpath, os.path.basename(fpath))
                    self.log(f"📦 ZIP criado: {zip_path}")
                except Exception as e:
                    self.log(f"❌ Erro ao criar ZIP: {e}")

            return True

        except Exception as e:
            self.log(f"❌ Erro no corte automático: {e}")
            return False
        finally:
            self.is_cutting = False

    def update_stats(self):
        """Atualiza as estatísticas na interface."""
        self.total_label.config(text=str(self.download_count))
        self.success_label.config(text=str(self.success_count))
        self.failed_label.config(text=str(self.failed_count))

        if self.download_count > 0:
            rate = (self.success_count / self.download_count) * 100
            self.rate_label.config(text=f"{rate:.1f}%")
        else:
            self.rate_label.config(text="0%")

    def clear_stats(self):
        """Limpa as estatísticas."""
        self.download_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.update_stats()
        self.history_text.delete(1.0, tk.END)
        self.log("🗑️ Estatísticas limpas")

    def export_log(self):
        """Exporta o log para arquivo."""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Arquivo de texto", "*.txt"), ("Todos os arquivos", "*.*")]
        )
        if filepath:
            try:
                shutil.copy2(self.session_log_file, filepath)
                self.log(f"📤 Log exportado para: {filepath}")
            except Exception as e:
                self.log(f"❌ Erro ao exportar log: {e}")

    def open_log_location(self):
        try:
            if sys.platform == "win32":
                os.startfile(str(self.session_log_file.parent))
            else:
                messagebox.showinfo("Local do Log", str(self.session_log_file))
        except Exception as error:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta do log:\n{error}")

    def reset_config(self):
        """Reseta configurações para os padrões."""
        if messagebox.askyesno("Resetar", "Tem certeza que deseja resetar todas as configurações?"):
            if self.config_file.exists():
                self.config_file.unlink()
            self.dir_entry_val.set(DEFAULT_CONFIG["download_dir"])
            self.cuts_dir_entry_val.set(DEFAULT_CONFIG["cuts_dir"])
            self.auth_method_var.set(DEFAULT_CONFIG["auth_method"])
            self.browser_var.set(DEFAULT_CONFIG["browser"])
            self.cookie_file_var.set(DEFAULT_CONFIG["cookie_file"])
            self.ua_var.set(DEFAULT_CONFIG["user_agent"])
            self.quality_var.set(DEFAULT_CONFIG["quality"])
            self.silence_threshold_var.set(DEFAULT_CONFIG["silence_threshold"])
            self.silence_duration_var.set(DEFAULT_CONFIG["silence_duration"])
            self.auto_retry_var.set(DEFAULT_CONFIG["auto_retry"])
            self.shutdown_var.set(DEFAULT_CONFIG["shutdown"])
            self.auto_cut_var.set(DEFAULT_CONFIG["auto_cut"])
            self.organize_by_music_var.set(DEFAULT_CONFIG["organize_by_music"])
            self.export_zip_var.set(DEFAULT_CONFIG["export_zip"])
            self.download_type.set(DEFAULT_CONFIG["download_type"])
            self.save_config()
            self.log("🔄 Configurações resetadas para os padrões")
            messagebox.showinfo("Sucesso", "Configurações resetadas com sucesso!")

    def browse_directory(self):
        """Seleciona diretório de download."""
        directory = filedialog.askdirectory()
        if directory:
            self.dir_entry_val.set(directory)
            self.save_config()

    def browse_cuts_directory(self):
        """Seleciona diretório de cortes."""
        directory = filedialog.askdirectory()
        if directory:
            self.cuts_dir_entry_val.set(directory)
            self.save_config()

    def browse_cookie_file(self):
        """Seleciona arquivo de cookies."""
        filepath = filedialog.askopenfilename(
            filetypes=[("Arquivo de texto", "*.txt"), ("Todos os arquivos", "*.*")]
        )
        if filepath:
            self.cookie_file_var.set(filepath)
            self.save_config()

    def stop_download(self):
        """Para o download em andamento."""
        self.is_downloading = False
        self.download_engine.cancel()
        self._ui(lambda: self.stop_btn.config(state="disabled"))
        self._ui(lambda: self.status_var.set("⏹️ Interrompendo..."))
        self.log("⏹️ Download interrompido pelo usuário")

    def log(self, message):
        """Adiciona mensagem ao log."""
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp_str}] {message}\n"
        self._write_session_line(log_line)
        self._ui(lambda: self._append_log(log_line))
        self._ui(lambda: self._append_history(log_line))

    def _write_session_line(self, line):
        """Grava imediatamente para preservar o diagnóstico mesmo após falhas."""
        try:
            with self._session_log_lock:
                with open(self.session_log_file, "a", encoding="utf-8") as stream:
                    stream.write(line)
                    stream.flush()
        except Exception:
            pass

    def _handle_tk_exception(self, exception_type, exception, trace):
        details = "".join(traceback.format_exception(exception_type, exception, trace))
        self._write_session_line(f"[ERRO DA INTERFACE]\n{details}\n")
        try:
            messagebox.showerror("Erro inesperado", f"O erro foi registrado em:\n{self.session_log_file}\n\n{exception}")
        except Exception:
            pass

    def _handle_thread_exception(self, args):
        details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        self._write_session_line(f"[ERRO DE PROCESSAMENTO | {args.thread.name}]\n{details}\n")

    def _append_log(self, line):
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)

    def _append_history(self, line):
        self.history_text.insert(tk.END, line)
        self.history_text.see(tk.END)

    def on_close(self):
        """Callback ao fechar a janela."""
        if self.is_downloading or self.is_cutting:
            if not messagebox.askyesno("Sair", "⚠️ Processo em andamento. Sair mesmo assim?"):
                return
            self.is_downloading = False
            self.is_cutting = False

        self.log("👋 Encerramento solicitado pelo usuário")
        self.save_config()
        self.cookie_manager.cleanup()
        self._write_session_line(f"===== FIM DA SESSÃO | {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        self.root.destroy()

    def _ui(self, func):
        """Executa função na thread principal (UI thread)."""
        self.root.after(0, func)


# ============ MAIN ============
def request_login(root) -> Optional[AccessResult]:
    """Exibe autenticação modal antes de carregar os componentes do aplicativo."""
    result_holder = {"result": None}
    finished = tk.BooleanVar(root, value=False)
    root.title(f"Acesso — {APP_NAME}")
    root.geometry("560x430")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: finished.set(True))

    frame = ttk.Frame(root, padding=25)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=APP_NAME, font=("Arial", 16, "bold")).pack(pady=(0, 8))
    ttk.Label(frame, text="Entre para continuar", foreground="gray").pack(pady=(0, 18))

    form = ttk.Frame(frame)
    form.pack(fill="x")
    ttk.Label(form, text="E-mail:").grid(row=0, column=0, sticky="w", pady=7)
    saved_email, saved_password = LicenseClient().load_login()
    username = tk.StringVar(value=saved_email)
    user_entry = ttk.Entry(form, textvariable=username, width=28)
    user_entry.grid(row=0, column=1, padx=10, pady=7)
    ttk.Label(form, text="Senha:").grid(row=1, column=0, sticky="w", pady=7)
    password = tk.StringVar(value=saved_password)
    password_entry = ttk.Entry(form, textvariable=password, show="●", width=28)
    password_entry.grid(row=1, column=1, padx=10, pady=7)
    remember_login = tk.BooleanVar(value=bool(saved_email or saved_password))

    def update_saved_login_preference():
        if not remember_login.get():
            LicenseClient().clear_saved_login()

    ttk.Checkbutton(
        form,
        text="Manter login neste computador",
        variable=remember_login,
        command=update_saved_login_preference,
    ).grid(row=2, column=1, sticky="w", padx=10, pady=(0, 4))
    status = ttk.Label(frame, text="", foreground="red", wraplength=360)
    status.pack(pady=8)

    machine = AccessControl.machine_id()
    machine_frame = ttk.Frame(frame)
    machine_frame.pack(fill="x", pady=(2, 4))
    ttk.Label(machine_frame, text=f"ID deste computador: {machine}", foreground="gray").pack(side="left")

    def copy_machine_id():
        root.clipboard_clear()
        root.clipboard_append(machine)
        status.config(text="ID copiado.", foreground="green")

    ttk.Button(machine_frame, text="Copiar ID", command=copy_machine_id, width=10).pack(side="right")

    def login(_event=None):
        entered_email = username.get().strip().lower()
        entered_password = password.get()
        access = AccessControl().authenticate(entered_email, entered_password)
        if access.allowed:
            if "@" in entered_email:
                client = LicenseClient()
                if remember_login.get():
                    client.save_login(entered_email, entered_password)
                else:
                    client.clear_saved_login()
            password.set("")
            result_holder["result"] = access
            finished.set(True)
        else:
            status.config(text=access.message, foreground="red")
            password_entry.focus_set()

    ttk.Button(frame, text="Entrar", command=login, width=18).pack(pady=4)

    def register():
        window = tk.Toplevel(root)
        window.title("Criar conta")
        window.geometry("430x300")
        content = ttk.Frame(window, padding=22); content.pack(fill="both", expand=True)
        name = tk.StringVar(); email = tk.StringVar(value=username.get()); secret = tk.StringVar(); confirm = tk.StringVar()
        fields = [("Nome:", name, ""), ("E-mail:", email, ""), ("Senha (mínimo 8 caracteres):", secret, "●"), ("Confirmar senha:", confirm, "●")]
        for row, (label, variable, mask) in enumerate(fields):
            ttk.Label(content, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(content, textvariable=variable, show=mask, width=30).grid(row=row, column=1, padx=8, pady=7)
        feedback = ttk.Label(content, text="", foreground="red", wraplength=370); feedback.grid(row=4, column=0, columnspan=2, pady=8)
        def submit():
            if secret.get() != confirm.get():
                feedback.config(text="As senhas não coincidem."); return
            if len(secret.get()) < 8:
                feedback.config(text="A senha precisa ter pelo menos 8 caracteres."); return
            try:
                LicenseClient().register(name.get(), email.get(), secret.get(), machine)
                username.set(email.get()); window.destroy()
                status.config(text="Conta criada. Você já pode entrar.", foreground="green")
            except LicenseAPIError as error:
                feedback.config(text=str(error))
        ttk.Button(content, text="Criar conta", command=submit).grid(row=5, column=0, columnspan=2, pady=8)

    ttk.Button(frame, text="Criar conta", command=register, width=18).pack(pady=3)

    def forgot_password():
        window = tk.Toplevel(root)
        window.title("Recuperar senha")
        window.geometry("450x360")
        window.resizable(False, False)
        content = ttk.Frame(window, padding=22); content.pack(fill="both", expand=True)
        ttk.Label(content, text="Recuperar senha", font=("Arial", 13, "bold")).pack(pady=(0, 8))
        ttk.Label(content, text="Informe seu e-mail para receber um código de seis dígitos.", wraplength=380, justify="center").pack(pady=(0, 14))
        form = ttk.Frame(content); form.pack(fill="x")
        email = tk.StringVar(value=username.get()); code = tk.StringVar(); secret = tk.StringVar(); confirm = tk.StringVar()
        fields = [("E-mail:", email, ""), ("Código:", code, ""), ("Nova senha:", secret, "●"), ("Confirmar senha:", confirm, "●")]
        for row, (label, variable, mask) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(form, textvariable=variable, show=mask, width=30).grid(row=row, column=1, padx=8, pady=5)
        feedback = ttk.Label(content, text="", foreground="red", wraplength=380); feedback.pack(pady=8)

        def send_code():
            try:
                LicenseClient().request_password_reset(email.get())
                feedback.config(text="Se o e-mail estiver cadastrado, o código foi enviado.", foreground="green")
            except LicenseAPIError as error:
                feedback.config(text=str(error), foreground="red")

        def change_password():
            if secret.get() != confirm.get():
                feedback.config(text="As senhas não coincidem.", foreground="red"); return
            if len(secret.get()) < 8:
                feedback.config(text="A senha precisa ter pelo menos 8 caracteres.", foreground="red"); return
            try:
                LicenseClient().reset_password(email.get(), code.get(), secret.get())
                username.set(email.get()); password.set(""); window.destroy()
                status.config(text="Senha redefinida. Entre com a nova senha.", foreground="green")
            except LicenseAPIError as error:
                feedback.config(text=str(error), foreground="red")

        buttons = ttk.Frame(content); buttons.pack(pady=4)
        ttk.Button(buttons, text="Enviar código", command=send_code).pack(side="left", padx=4)
        ttk.Button(buttons, text="Redefinir senha", command=change_password).pack(side="left", padx=4)

    ttk.Button(frame, text="Esqueci minha senha", command=forgot_password, width=18).pack(pady=3)

    def request_unlock():
        email = username.get().strip().lower()
        entered_password = password.get()
        if not email or not entered_password:
            status.config(text="Informe seu e-mail e senha para gerar um Pix vinculado à sua conta.", foreground="red")
            password_entry.focus_set()
            return

        client = LicenseClient()
        window = tk.Toplevel(root)
        window.title("Comprar licença PRO")
        window.geometry("650x680")
        window.resizable(False, False)
        content = ttk.Frame(window, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Continue usando o YouTube Downloader PRO", font=("Arial", 15, "bold")).pack(pady=(0, 10))
        message = ("Confirme sua conta para gerar um Pix exclusivo. A licença será ativada automaticamente "
                   "assim que o Mercado Pago confirmar o pagamento.")
        ttk.Label(content, text=message, wraplength=590, justify="center").pack(pady=5)
        progress = ttk.Label(content, text="Validando sua conta e gerando o Pix…", foreground="gray")
        progress.pack(pady=24)

        def show_payment(payment):
            if not window.winfo_exists():
                return
            payload = str(payment.get("qr_code") or "")
            if not payload:
                progress.config(text="O Mercado Pago não retornou um código Pix. Tente novamente.", foreground="red")
                return
            progress.destroy()
            import qrcode
            from PIL import ImageTk
            image = qrcode.make(payload).resize((260, 260))
            photo = ImageTk.PhotoImage(image)
            qr_label = ttk.Label(content, image=photo)
            qr_label.image = photo
            qr_label.pack(pady=10)
            ttk.Label(content, text=f"Valor: R$ {float(payment['amount']):.2f}\nStatus: aguardando pagamento", justify="center").pack()

            def copy_pix():
                window.clipboard_clear(); window.clipboard_append(payload)
                messagebox.showinfo("Pix", "Código Pix Copia e Cola copiado.", parent=window)

            payment_status = ttk.Label(content, text="A confirmação é automática; esta janela pode permanecer aberta.", foreground="gray")
            payment_status.pack(pady=8)

            def check_payment():
                if not window.winfo_exists():
                    return
                try:
                    current = client.payment(payment["id"])
                    if current.get("status") == "approved":
                        payment_status.config(text="Pagamento aprovado — licença PRO ativada!", foreground="green")
                        status.config(text="Licença ativada. Clique em Entrar novamente.", foreground="green")
                        return
                except LicenseAPIError:
                    pass
                window.after(5000, check_payment)

            buttons = ttk.Frame(content); buttons.pack(pady=12)
            ttk.Button(buttons, text="Copiar Pix", command=copy_pix).pack(side="left", padx=5)
            ttk.Button(buttons, text="Fechar", command=window.destroy).pack(side="left", padx=5)
            window.after(3000, check_payment)

        def fail_payment(error):
            if window.winfo_exists():
                progress.config(text=str(error), foreground="red")

        def create_payment_worker():
            try:
                # Não confiamos no token salvo: a cobrança deve pertencer ao e-mail
                # e à senha informados nesta tela, no momento desta solicitação.
                client.login(email, entered_password, machine)
                payment = client.create_pix(machine)
                root.after(0, lambda: show_payment(payment))
            except LicenseAPIError as error:
                root.after(0, lambda error=error: fail_payment(error))

        password.set("")
        threading.Thread(target=create_payment_worker, daemon=True).start()

    ttk.Button(frame, text="Entrar e gerar Pix", command=request_unlock, width=24).pack(pady=4)
    root.bind("<Return>", login)
    root.deiconify()
    root.update_idletasks()
    x = max(0, (root.winfo_screenwidth() - root.winfo_width()) // 2)
    y = max(0, (root.winfo_screenheight() - root.winfo_height()) // 2)
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    user_entry.focus_set()
    root.wait_variable(finished)
    root.unbind("<Return>")
    for child in root.winfo_children():
        child.destroy()
    return result_holder["result"]


if __name__ == "__main__":
    # Confirma ao atualizador que o Python e os módulos essenciais carregaram.
    # Se o executável falhar antes daqui, o atualizador restaura a versão anterior.
    for argument in sys.argv[1:]:
        if argument.startswith("--update-ready="):
            try:
                pathlib.Path(argument.split("=", 1)[1]).write_text(VERSION, encoding="utf-8")
            except OSError:
                pass
    print("=" * 50)
    print(f"{APP_NAME} v{VERSION}")
    print("=" * 50)

    print(f"Python {sys.version}")

    print("Motor de download externo com atualização automática")

    try:
        import browser_cookie3

        print("browser-cookie3 instalado")
    except ImportError:
        print("browser-cookie3 não encontrado")

    print("=" * 50)
    print("Iniciando aplicação...")

    root = tk.Tk()
    access = request_login(root)
    if access is None:
        root.destroy()
        raise SystemExit(0)
    app = YouTubeDownloaderEnhanced(root, access)
    root.protocol("WM_DELETE_WINDOW", app.on_close)

    root.mainloop()
