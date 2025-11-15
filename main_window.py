from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QObject, QUrl, QSize, QEvent, QTimer
from PySide6.QtWidgets import QSizePolicy
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QLineEdit,
    QPushButton,
    QLabel,
    QProgressBar,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QFormLayout,
    QFrame,
    QTextEdit,
    QComboBox,
)

try:
    # Prefer absolute imports so PyInstaller bundles modules reliably
    from settings import load_settings, save_settings, AppSettings
    import downloader
except Exception:
    # Fallback for package-style imports
    from .settings import load_settings, save_settings, AppSettings
    from . import downloader


class DownloadWorker(QObject):
    progressed = Signal(int)  # emit percent (0-100) only for lighter UI updates
    finished = Signal(str)  # emits final file path
    failed = Signal(str)

    def __init__(self, url: str, download_dir: Path, quality: str):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.quality = quality
        # no cancel/pause feature

    def _hook(self, d: dict):
        # Throttle and minimize progress emissions for better performance
        try:
            status = d.get("status")
            if status == "downloading":
                p = d.get("_percent_str", "0.0%")
                percent = int(float(p.strip().replace("%", "")))
                # Emit only on integer percent change to reduce signal traffic
                if not hasattr(self, "_last_percent") or percent != getattr(self, "_last_percent"):
                    self._last_percent = percent
                    self.progressed.emit(percent)
            elif status in ("finished", "postprocessor"):  # capture final path
                self.progressed.emit(100)
                fp = None
                info = d.get("info_dict") or {}
                # Try several possible keys used by yt-dlp across stages
                for k in ("filepath", "requested_downloads", "_filename", "filename", "target"):
                    v = d.get(k)
                    if v:
                        fp = v
                        break
                    v2 = info.get(k) if isinstance(info, dict) else None
                    if v2:
                        fp = v2
                        break
                # requested_downloads may be a list of dicts
                if isinstance(fp, list) and fp and isinstance(fp[0], dict):
                    fp = fp[0].get("filepath") or fp[0].get("filename")
                if isinstance(fp, (list, tuple)):
                    fp = fp[0] if fp else None
                if fp:
                    self._result_file = str(fp)
        except Exception:
            # In case parsing fails, skip emission
            pass

    def run(self):
        try:
            downloader.download(self.url, self.download_dir, self.quality, self._hook)
            # Emit only if hook determined a final path
            self.finished.emit(getattr(self, "_result_file", ""))
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video İndirici")
        self.resize(960, 600)
        # Minimum pencere boyutu (çok küçülmeyi engelle)
        self.setMinimumSize(900, 560)

        self.settings: AppSettings = load_settings()
        self._active_thread: Optional[QThread] = None
        self._stt_threads = {}

        self._init_menu()
        self._init_ui()

    def _init_menu(self):
        # Hide app menu bar entirely per request
        self.menuBar().setVisible(False)

    def _init_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Main tab
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("URL yapıştırın (YouTube, Instagram, TikTok)")

        self.paste_btn = QPushButton()
        self.paste_btn.clicked.connect(self._paste_from_clipboard)
        self.paste_btn.setToolTip("Yapıştır")
        self._as_icon_button(self.paste_btn)

        # Separator between paste and download action buttons
        self.actions_separator = QFrame()
        self.actions_separator.setFrameShape(QFrame.VLine)
        self.actions_separator.setFrameShadow(QFrame.Sunken)

        # Direct download action buttons (icon-only)
        self.download_best_btn = QPushButton()
        self.download_best_btn.setToolTip("En iyi kalite")
        self.download_best_btn.clicked.connect(lambda: self._start_download("best"))
        self._as_icon_button(self.download_best_btn)

        self.download_mp4_btn = QPushButton()
        self.download_mp4_btn.setToolTip("MP4")
        self.download_mp4_btn.clicked.connect(lambda: self._start_download("mp4"))
        self._as_icon_button(self.download_mp4_btn)

        self.download_mp3_btn = QPushButton()
        self.download_mp3_btn.setToolTip("MP3 (sadece ses)")
        self.download_mp3_btn.clicked.connect(lambda: self._start_download("mp3"))
        self._as_icon_button(self.download_mp3_btn)

        self.open_folder_btn = QPushButton()
        self.open_folder_btn.clicked.connect(self._open_downloads)
        self.open_folder_btn.setToolTip("İndirilenler")
        self._as_icon_button(self.open_folder_btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setVisible(False)

        # Downloads list (replaces log area)
        # Search + filter bar (right-aligned, compact)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)
        search_row.addStretch(1)  # push controls to the right
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("")
        self.search_edit.setClearButtonEnabled(True)
        # Arama kutusunda ikon (metin yerine)
        try:
            act = self.search_edit.addAction(self._icon("search"), QLineEdit.LeadingPosition)
        except Exception:
            pass
        self.filter_combo = QComboBox()
        self.search_edit.setMaximumWidth(320)
        self.filter_combo.setMaximumWidth(44)
        # Filtre seçenekleri ikonla (metinsiz) + tooltip ve role ile veri
        self.filter_combo.clear()
        _filters = [
            ("ALL", "filter", "Tümü"),
            ("Video", "video", "Video"),
            ("Müzik", "music", "Müzik"),
            ("Metin", "file_text", "Metin"),
        ]
        from PySide6.QtCore import Qt as _Qt
        for kind, icon_name, tip in _filters:
            self.filter_combo.addItem(self._icon(icon_name), "")
            i = self.filter_combo.count() - 1
            self.filter_combo.setItemData(i, tip, _Qt.ToolTipRole)
            self.filter_combo.setItemData(i, kind, _Qt.UserRole)
        self.search_edit.textChanged.connect(self._apply_list_filter)
        self.filter_combo.currentIndexChanged.connect(lambda _: self._apply_list_filter())
        search_row.addWidget(self.search_edit)
        search_row.addWidget(self.filter_combo)

        self.downloads_list = QListWidget()
        self.downloads_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        # Improve layout/spacing so rows don't overlap
        try:
            from PySide6.QtWidgets import QListView
            self.downloads_list.setResizeMode(QListView.Adjust)
        except Exception:
            pass
        self.downloads_list.setUniformItemSizes(False)
        self.downloads_list.setSpacing(5)
        self.downloads_list.setAlternatingRowColors(True)

        main_page = QWidget()
        mv = QVBoxLayout(main_page)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("URL"))
        row1.addWidget(self.url_edit, 1)
        row1.addWidget(self.paste_btn)
        row1.addWidget(self.actions_separator)
        row1.addWidget(self.download_best_btn)
        row1.addWidget(self.download_mp4_btn)
        row1.addWidget(self.download_mp3_btn)
        row1.addWidget(self.open_folder_btn)

        mv.addLayout(row1)
        mv.addWidget(self.progress)
        # URL satırı ile arama satırı arasına ayırıcı
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        mv.addWidget(sep)
        mv.addLayout(search_row)
        mv.addWidget(self.downloads_list, 2)

        # Transcript panel (inline, hidden by default)
        self.transcript_panel = QWidget()
        tp_lay = QVBoxLayout(self.transcript_panel)
        tp_lay.setContentsMargins(0, 0, 0, 0)
        tp_hdr = QHBoxLayout()
        tp_title = QLabel("Transkript")
        tp_close = QPushButton()
        tp_close.setIcon(self._icon("close"))
        tp_close.setToolTip("Kapat")
        tp_close.clicked.connect(lambda: self.transcript_panel.setVisible(False))
        tp_hdr.addWidget(tp_title)
        tp_hdr.addStretch(1)
        tp_hdr.addWidget(tp_close)
        tp_lay.addLayout(tp_hdr)
        self.transcript_view = QTextEdit()
        self.transcript_view.setReadOnly(True)
        tp_lay.addWidget(self.transcript_view, 3)
        self.transcript_panel.setVisible(False)
        mv.addWidget(self.transcript_panel, 1)
        

        # Settings tab
        settings_page = QWidget()
        form = QFormLayout(settings_page)
        self.settings_dir_edit = QLineEdit(str(self.settings.download_dir))
        self.settings_dir_edit.setReadOnly(True)
        self.settings_dir_edit.setCursorPosition(0)
        self.settings_dir_edit.setToolTip(str(self.settings.download_dir))
        self.settings_dir_btn = QPushButton()
        self.settings_dir_btn.clicked.connect(self._change_dir_from_settings)
        self.settings_dir_btn.setToolTip("Klasör Değiştir")
        self._as_icon_button(self.settings_dir_btn)

        dir_row = QHBoxLayout()
        dir_row.addWidget(self.settings_dir_edit, 1)
        dir_row.addWidget(self.settings_dir_btn)

        form.addRow("Varsayılan İndirme Klasörü", dir_row)

        self.tabs.addTab(main_page, "")
        self.tabs.addTab(settings_page, "")
        # Tooltips to indicate tab purpose when text is hidden
        self.tabs.setTabToolTip(0, "Ana Sayfa")
        self.tabs.setTabToolTip(1, "Ayarlar")

        self._apply_icons()
        self._apply_sizing()

        # Populate list with existing downloads on startup
        try:
            self._load_existing_downloads()
        except Exception:
            pass

    # ------------------------
    # Whisper STT (background worker)
    # ------------------------
    class _STTWorker(QObject):
        finished = Signal(str)  # path to txt
        failed = Signal(str)

        def __init__(self, video_path: Path, lang: Optional[str] = None, model_size: str = "small"):
            super().__init__()
            self.video_path = video_path
            self.lang = lang
            self.model_size = model_size

        def run(self):
            try:
                import shutil, subprocess, tempfile
                from faster_whisper import WhisperModel

                if shutil.which('ffmpeg') is None:
                    raise RuntimeError("FFmpeg bulunamadı (PATH'te olmalı)")

                # Extract mono 16k wav to temp
                with tempfile.TemporaryDirectory() as td:
                    wav_path = Path(td) / "audio.wav"
                    cmd = [
                        'ffmpeg', '-y', '-i', str(self.video_path),
                        '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', str(wav_path)
                    ]
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
                    segments, info = model.transcribe(str(wav_path), language=self.lang, vad_filter=True)

                    texts = []
                    for seg in segments:
                        texts.append(seg.text.strip())
                    text = "\n".join([t for t in texts if t])

                out_txt = self.video_path.with_suffix("").with_suffix("")  # ensure only last suffix removed once
                # Correct stem: Path('a.mp4').with_suffix('') -> 'a'
                out_txt = self.video_path.with_suffix("")
                out_txt = out_txt.with_name(out_txt.name + ".transcript.txt")
                try:
                    Path(out_txt).write_text(text, encoding='utf-8')
                except Exception:
                    Path(out_txt).write_text(text)
                self.finished.emit(str(out_txt))
            except Exception as e:
                self.failed.emit(str(e))

    def _start_whisper_transcribe(self, video_path: Path):
        # Start background STT worker
        try:
            # quick import check to give early feedback
            import importlib
            if importlib.util.find_spec('faster_whisper') is None:
                self._status("Whisper (faster-whisper) yüklü değil. requirements.txt ile kurun.")
                return
        except Exception:
            pass

        worker = MainWindow._STTWorker(video_path)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_whisper_finished)
        worker.failed.connect(self._on_whisper_failed)
        self._stt_threads[worker] = thread
        thread.start()
        self._status("Otomatik transkript başlatıldı (Whisper)...")

    def _on_whisper_finished(self, txt_path: str):
        # Ensure cleanup from the GUI thread
        try:
            worker = self.sender()
            thread = self._stt_threads.pop(worker, None)
            if thread is not None:
                thread.quit(); thread.wait()
        except Exception:
            pass
        # Load and show
        try:
            txt = Path(txt_path).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            txt = ""
        self.transcript_view.setPlainText(txt)
        self.transcript_panel.setVisible(True)
        self._status(f"Whisper transkript hazır: {Path(txt_path).name}")
        self._add_asset_item(Path(txt_path))
        try:
            video_path = getattr(worker, 'video_path', None)
            self._set_row_busy(self._find_row_widget_by_path(video_path) if video_path else None, False)
        except Exception:
            pass

    def _on_whisper_failed(self, message: str):
        try:
            worker = self.sender()
            thread = self._stt_threads.pop(worker, None)
            if thread is not None:
                thread.quit(); thread.wait()
        except Exception:
            pass
        self._status(f"Whisper hata: {message}")
        try:
            video_path = getattr(worker, 'video_path', None)
            self._set_row_busy(self._find_row_widget_by_path(video_path) if video_path else None, False)
        except Exception:
            pass

    def _change_dir_from_settings(self):
        self._pick_dir()

    def _pick_dir(self):
        start = str(self.settings.download_dir)
        new_dir = QFileDialog.getExistingDirectory(self, "Klasör Seç", start)
        if new_dir:
            self.settings.download_dir = new_dir
            save_settings(self.settings)
            self.settings_dir_edit.setText(new_dir)
            self.settings_dir_edit.setToolTip(new_dir)

    def _open_downloads(self):
        path = Path(self.settings.download_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _paste_from_clipboard(self):
        from PySide6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        text = cb.text().strip()
        if text:
            self.url_edit.setText(text)

    def _icon(self, name: str) -> QIcon:
        # Looks up icons from multiple candidate locations and extensions:
        #  - <frozen_root>/assets/icons/<name>.{png,ico} (PyInstaller)
        #  - <repo_root>/assets/icons/<name>.{png,ico}
        #  - <package_dir>/assets/icons/<name>.{png,ico}
        # If not found, returns an empty icon so UI stays functional.
        import sys as _sys
        pkg_dir = Path(__file__).resolve().parent
        repo_root = pkg_dir.parent
        frozen_root = Path(getattr(_sys, "_MEIPASS", repo_root))
        exts = ("png", "ico")
        candidates = []
        for ext in exts:
            candidates.append(frozen_root / "assets" / "icons" / f"{name}.{ext}")
            candidates.append(repo_root / "assets" / "icons" / f"{name}.{ext}")
            candidates.append(pkg_dir / "assets" / "icons" / f"{name}.{ext}")
        for p in candidates:
            if p.exists():
                return QIcon(str(p))
        return QIcon()

    def _apply_icons(self):
        # Buttons
        self.paste_btn.setIcon(self._icon("paste"))
        self.download_best_btn.setIcon(self._icon("download_best"))
        self.download_mp4_btn.setIcon(self._icon("download_mp4"))
        self.download_mp3_btn.setIcon(self._icon("download_mp3"))
        self.open_folder_btn.setIcon(self._icon("downloads"))
        self.settings_dir_btn.setIcon(self._icon("folder"))

        # Tabs
        self.tabs.setTabIcon(0, self._icon("home"))
        self.tabs.setTabIcon(1, self._icon("settings"))

        # App/window icon (try 'app' then 'logo')
        app_icon = self._icon("app")
        if app_icon.isNull():
            app_icon = self._icon("logo")
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.instance().setWindowIcon(app_icon)
            except Exception:
                pass

        # Explicit icon sizes
        icon_size = QSize(32, 32)
        self.paste_btn.setIconSize(icon_size)
        self.download_best_btn.setIconSize(icon_size)
        self.download_mp4_btn.setIconSize(icon_size)
        self.download_mp3_btn.setIconSize(icon_size)
        self.open_folder_btn.setIconSize(icon_size)
        self.settings_dir_btn.setIconSize(icon_size)
        self.tabs.setIconSize(QSize(24, 24))

    def _as_icon_button(self, btn: QPushButton, *, danger: bool = False, success: bool = False):
        try:
            btn.setFlat(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            try:
                btn.setAutoDefault(False)
                btn.setDefault(False)
            except Exception:
                pass
            # Stronger hover/pressed visuals; tone by variant
            if danger:
                hover_bg = 'rgba(220,0,0,0.12)'; press_bg = 'rgba(220,0,0,0.22)'; border = 'rgba(220,0,0,0.25)'
            elif success:
                hover_bg = 'rgba(0,140,0,0.12)'; press_bg = 'rgba(0,140,0,0.22)'; border = 'rgba(0,140,0,0.25)'
            else:
                hover_bg = 'rgba(0,0,0,0.10)'; press_bg = 'rgba(0,0,0,0.18)'; border = 'rgba(0,0,0,0.18)'
            btn.setStyleSheet(
                "QPushButton{border:none;background:transparent;padding:6px;border-radius:8px;}"
                f"QPushButton:hover{{background:{hover_bg};border:1px solid {border};}}"
                f"QPushButton:pressed{{background:{press_bg};border:1px solid {border};}}"
                )
        except Exception:
            pass

    def _apply_sizing(self):
        # Slightly larger base font for readability
        f = self.font()
        try:
            ps = f.pointSize()
        except Exception:
            ps = 10
        if ps and ps > 0:
            f.setPointSize(ps + 2)
        else:
            f.setPointSize(12)
        self.setFont(f)

        # Bigger controls
        self.url_edit.setMinimumHeight(36)
        self.progress.setFixedHeight(20)

        big_btns = [
            self.paste_btn,
            self.download_best_btn,
            self.download_mp4_btn,
            self.download_mp3_btn,
            self.open_folder_btn,
            self.settings_dir_btn,
        ]
        for b in big_btns:
            b.setMinimumSize(40, 40)

        # Match separator height with buttons for better alignment
        self.actions_separator.setFixedHeight(40)

        # Layout spacing and margins for each tab page
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            lay = w.layout()
            if lay is not None:
                lay.setContentsMargins(12, 12, 12, 12)
                lay.setSpacing(10)

    def _set_download_buttons_enabled(self, enabled: bool):
        self.download_best_btn.setEnabled(enabled)
        self.download_mp4_btn.setEnabled(enabled)
        self.download_mp3_btn.setEnabled(enabled)

    def _start_download(self, quality: str = "best"):
        url = self.url_edit.text().strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            self._status("Geçerli bir URL girin (http/https)")
            return

        self._set_download_buttons_enabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        target_dir = Path(self.settings.download_dir)

        worker = DownloadWorker(url, target_dir, quality)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_progress)
        # Connect directly to MainWindow slots (queued across threads)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)

        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _on_progress(self, percent: int):
        self.progress.setValue(max(0, min(100, int(percent))))
        if not self.progress.isVisible():
            self.progress.setVisible(True)
        # row progress for active download removed

    def _on_finished(self, final_path: str):
        self._status("İndirme tamamlandı.")
        self._set_download_buttons_enabled(True)
        self.progress.setVisible(False)
        # Clean up thread safely from the GUI thread
        t = getattr(self, "_active_thread", None)
        w = getattr(self, "_active_worker", None)
        if t is not None:
            t.quit()
            t.wait()
        self._active_thread = None
        # Add to downloads list
        try:
            if final_path:
                p = Path(final_path)
                if p.exists() and p.is_file():
                    url = getattr(w, "url", "") if w is not None else ""
                    self._add_download_item(url, str(p))
        except Exception:
            pass

    def _on_failed(self, message: str):
        self._status(f"Hata: {message}")
        self._set_download_buttons_enabled(True)
        self.progress.setVisible(False)
        t = getattr(self, "_active_thread", None)
        if t is not None:
            t.quit()
            t.wait()
        self._active_thread = None

    def _append_log(self, text: str):
        # Route messages to the status bar (no dialogs)
        self._status(text)

    def _append_info(self, text: str):
        self._status(text)

    def _status(self, text: str, timeout_ms: int = 5000):
        try:
            self.statusBar().showMessage(text, timeout_ms)
        except Exception:
            pass

    # ------------------------
    # Downloads list handling
    # ------------------------
    def _is_video_file(self, p: Path) -> bool:
        video_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v"}
        ext = p.suffix.lower()
        return ext in video_exts

    def _is_temp_file(self, p: Path) -> bool:
        bad_suffixes = (".part", ".temp", ".tmp")
        return any(str(p).lower().endswith(s) for s in bad_suffixes)

    def _load_existing_downloads(self):
        base = Path(self.settings.download_dir)
        if not base.exists():
            return
        # Sort by modified time desc to show newest first
        items = sorted(base.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
        for p in items:
            if not p.is_file() or self._is_temp_file(p):
                continue
            if self._is_video_file(p):
                # URL bilinmiyor; sadece açma/silme ve MP3/Transkript eylemleri (URL gerekirse uyarır)
                self._add_download_item("", str(p))
            elif p.suffix.lower() == ".mp3" or p.name.lower().endswith(".transcript.txt") or p.suffix.lower() in {".srt", ".vtt"}:
                self._add_asset_item(p)

    def _thumb_for(self, media_path: Path) -> Optional[Path]:
        # Look for sidecar thumbnail files next to media
        stem = media_path.with_suffix("").name
        parent = media_path.parent
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            for cand in parent.glob(f"{stem}*{ext}"):
                if cand.is_file():
                    return cand
        # If not found and it's a video, try extracting a frame via ffmpeg
        if self._is_video_file(media_path):
            try:
                import shutil, subprocess
                if shutil.which('ffmpeg') is None:
                    return None
                # Create a deterministic thumbnail path
                out = parent / f"{stem}.thumb.jpg"
                # Use a small offset to avoid black frames; suppress output
                cmd = [
                    'ffmpeg', '-y', '-ss', '3', '-i', str(media_path),
                    '-frames:v', '1', '-q:v', '2', str(out)
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if out.exists():
                    return out
            except Exception:
                return None
        return None

    def _type_icon_for(self, p: Path):
        try:
            ext = p.suffix.lower()
        except Exception:
            ext = ""
        # Determine kind
        if self._is_video_file(p):
            return self._icon("video"), "Video"
        if ext == ".mp3":
            return self._icon("music"), "Müzik"
        if p.name.lower().endswith(".transcript.txt") or ext in {".srt", ".vtt", ".txt"}:
            return self._icon("file_text"), "Metin"
        return QIcon(), ""
    def _add_download_item(self, url: str, file_path: str):
        if not file_path:
            return
        p = Path(file_path)
        item = QListWidgetItem()
        item.setData(Qt.UserRole, {
            "url": url,
            "path": str(p),
            "kind": "Video",
        })
        widget = self._create_download_item_widget(p)
        # Fix row height so hover state changes don't clip content
        item.setSizeHint(QSize(widget.sizeHint().width(), 110))
        self.downloads_list.addItem(item)
        self.downloads_list.setItemWidget(item, widget)

    def _create_download_item_widget(self, path: Path) -> QWidget:
        w = QWidget()
        # Make row taller
        w.setMinimumHeight(100)
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(12)
        # Thumbnail if available
        thumb = QLabel()
        # Larger thumbnail for better visibility
        thumb.setFixedSize(160, 90)
        thumb.setScaledContents(True)
        tp = self._thumb_for(path)
        if tp:
            try:
                pm = QPixmap(str(tp))
                if not pm.isNull():
                    thumb.setPixmap(pm)
            except Exception:
                pass
        h.addWidget(thumb)

        name_lbl = QLabel(path.name)
        name_lbl.setMinimumWidth(120)
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        name_lbl.setToolTip(str(path))
        h.addWidget(name_lbl, 1)

        # Row progress bar (determinate fill)
        row_spinner = QProgressBar()
        row_spinner.setRange(0, 100)
        row_spinner.setValue(0)
        row_spinner.setTextVisible(False)
        row_spinner.setFixedHeight(12)
        row_spinner.setMaximumWidth(120)
        row_spinner.setVisible(False)
        h.addWidget(row_spinner)
        # Timer to animate progress while task runs (up to 90%)
        prog_timer = QTimer(w)
        prog_timer.setInterval(120)
        def _tick():
            v = row_spinner.value()
            if v < 90:
                row_spinner.setValue(min(90, v + 2))
        prog_timer.timeout.connect(_tick)

        btn_tr = QPushButton()
        btn_tr.setIcon(self._icon("transcript"))
        btn_tr.setIconSize(QSize(28, 28))
        btn_tr.setToolTip("Transkript (Whisper)")
        self._as_icon_button(btn_tr)
        btn_tr.clicked.connect(lambda: self._action_transcript(path))
        h.addWidget(btn_tr)

        btn_mp3 = QPushButton()
        btn_mp3.setIcon(self._icon("audio"))
        btn_mp3.setIconSize(QSize(28, 28))
        btn_mp3.setToolTip("Sesi MP3 olarak çıkar")
        self._as_icon_button(btn_mp3)
        btn_mp3.clicked.connect(lambda: self._action_audio_mp3(path))
        h.addWidget(btn_mp3)

        btn_del = QPushButton()
        btn_del.setIcon(self._icon("delete"))
        btn_del.setIconSize(QSize(28, 28))
        btn_del.setToolTip("Dosyayı sil")
        self._as_icon_button(btn_del, danger=True)
        h.addWidget(btn_del)

        # Inline confirm controls (hidden initially)
        btn_yes = QPushButton()
        btn_yes.setIcon(self._icon("check"))
        btn_yes.setIconSize(QSize(28, 28))
        btn_yes.setToolTip("Evet")
        self._as_icon_button(btn_yes, success=True)
        btn_no = QPushButton()
        btn_no.setIcon(self._icon("close"))
        btn_no.setIconSize(QSize(28, 28))
        btn_no.setToolTip("Hayır")
        self._as_icon_button(btn_no, danger=True)
        h.addWidget(btn_yes)
        h.addWidget(btn_no)

        # Store controls on row widget for hover handling
        w._spinner = row_spinner
        w._progress_timer = prog_timer
        w._btn_tr = btn_tr
        w._btn_mp3 = btn_mp3
        w._btn_del = btn_del
        w._btn_yes = btn_yes
        w._btn_no = btn_no
        w._confirm_mode = False

        def show_confirm():
            w._confirm_mode = True
            self._set_row_actions_visible(w, True)

        def cancel_confirm():
            w._confirm_mode = False
            self._set_row_actions_visible(w, True)

        btn_del.clicked.connect(show_confirm)
        btn_no.clicked.connect(cancel_confirm)
        btn_yes.clicked.connect(lambda: self._action_delete_group(path))

        # Hover behavior: hide actions by default, show on row hover
        self._attach_hover_behavior(w)
        self._set_row_actions_visible(w, False)

        return w

    def _on_item_double_clicked(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        p = Path(str(data.get("path", "")))
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    # ------------------------
    # Item actions
    # ------------------------
    def _action_transcript(self, media_path: Path):
        # Always use Whisper STT. Works for both video and audio files.
        row = self._find_row_widget_by_path(media_path)
        self._set_row_busy(row, True)
        self._start_whisper_transcribe(media_path)

    def _action_audio_mp3(self, video_path: Path):
        # Convert to MP3 using ffmpeg
        mp3_path = video_path.with_suffix('.mp3')
        try:
            import shutil, subprocess
            if not shutil.which('ffmpeg'):
                self._status("FFmpeg bulunamadı. Lütfen FFmpeg kurun ve PATH'e ekleyin.")
                return
            row = self._find_row_widget_by_path(video_path)
            self._set_row_busy(row, True)
            cmd = [
                'ffmpeg', '-y', '-i', str(video_path),
                '-vn', '-acodec', 'libmp3lame', '-q:a', '2', str(mp3_path)
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._status(f"MP3 oluşturuldu: {mp3_path.name}")
            # Add MP3 as its own list item for double-click opening
            self._add_asset_item(mp3_path)
        except subprocess.CalledProcessError:
            self._status("MP3 dönüştürme başarısız oldu.")
        except Exception as e:
            self._status(f"Hata: {e}")
        finally:
            try:
                self._set_row_busy(self._find_row_widget_by_path(video_path), False)
            except Exception:
                pass

    def _action_delete_group(self, video_path: Path):
        # Delete video and related files (inline, no dialogs)
        related = []
        stem = video_path.stem
        parent = video_path.parent
        # Collect typical sidecars: audio, transcripts, subtitles, thumbnails
        side_exts = ('.mp3', '.transcript.txt', '.srt', '.vtt', '.jpg', '.jpeg', '.png', '.webp', '.thumb.jpg')
        for ext in side_exts:
            related += list(parent.glob(f"{stem}*{ext}"))
        files_to_delete = [video_path] + [p for p in related if p.exists()]
        errs = []
        for p in files_to_delete:
            try:
                p.unlink(missing_ok=True)
            except Exception as e:
                errs.append(f"{p.name}: {e}")
        if errs:
            self._status("Bazı dosyalar silinemedi: " + "; ".join(errs))
        # Remove any matching list items
        i = 0
        while i < self.downloads_list.count():
            it = self.downloads_list.item(i)
            data = it.data(Qt.UserRole) or {}
            p = Path(str(data.get("path", "")))
            if not p.exists() and (p.stem == stem or p == video_path):
                self.downloads_list.takeItem(i)
            else:
                i += 1
        self._status("Silme işlemi tamamlandı.")

    def _action_delete_single(self, path: Path):
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            self._status(f"Silinemedi: {e}")
            return
        # Remove list entry
        for i in range(self.downloads_list.count()):
            it = self.downloads_list.item(i)
            data = it.data(Qt.UserRole) or {}
            if Path(str(data.get("path", ""))) == path:
                self.downloads_list.takeItem(i)
                break
        self._status("Dosya silindi.")

    def _add_asset_item(self, path: Path):
        item = QListWidgetItem()
        # Determine kind for filtering
        kind = "Metin"
        ext = path.suffix.lower()
        name_low = path.name.lower()
        if ext == ".mp3":
            kind = "Müzik"
        elif name_low.endswith(".transcript.txt") or ext in {".srt", ".vtt", ".txt"}:
            kind = "Metin"
        item.setData(Qt.UserRole, {"path": str(path), "kind": kind})
        w = QWidget()
        # Taller asset rows as well
        w.setMinimumHeight(72)
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(12)
        # Type icon for asset
        kind_icon_lbl = QLabel()
        kind_icon_lbl.setFixedSize(24, 24)
        kind_icon_lbl.setScaledContents(True)
        kind_icon, kind_tip = self._type_icon_for(path)
        if kind_icon and not kind_icon.isNull():
            kind_icon_lbl.setPixmap(kind_icon.pixmap(24, 24))
        if kind_tip:
            kind_icon_lbl.setToolTip(kind_tip)
        h.addWidget(kind_icon_lbl)

        lbl = QLabel(path.name)
        lbl.setToolTip(str(path))
        h.addWidget(lbl, 1)

        # Row progress bar (determinate fill)
        row_spinner = QProgressBar()
        row_spinner.setRange(0, 100)
        row_spinner.setValue(0)
        row_spinner.setTextVisible(False)
        row_spinner.setFixedHeight(12)
        row_spinner.setMaximumWidth(120)
        row_spinner.setVisible(False)
        h.addWidget(row_spinner)
        prog_timer = QTimer(w)
        prog_timer.setInterval(120)
        def _tick():
            v = row_spinner.value()
            if v < 90:
                row_spinner.setValue(min(90, v + 2))
        prog_timer.timeout.connect(_tick)

        # Transcript button for audio assets as well
        btn_tr = None
        if kind == "Müzik":
            btn_tr = QPushButton()
            btn_tr.setIcon(self._icon("transcript"))
            btn_tr.setIconSize(QSize(28, 28))
            btn_tr.setToolTip("Transkript (Whisper)")
            self._as_icon_button(btn_tr)
            btn_tr.clicked.connect(lambda: self._action_transcript(path))
            h.addWidget(btn_tr)
        btn_del = QPushButton()
        btn_del.setIcon(self._icon("delete"))
        btn_del.setIconSize(QSize(28, 28))
        btn_del.setToolTip("Sil")
        self._as_icon_button(btn_del, danger=True)
        h.addWidget(btn_del)

        # Inline confirm for asset
        btn_yes = QPushButton()
        btn_yes.setIcon(self._icon("check"))
        btn_yes.setIconSize(QSize(28, 28))
        btn_yes.setToolTip("Evet")
        self._as_icon_button(btn_yes, success=True)
        btn_no = QPushButton()
        btn_no.setIcon(self._icon("close"))
        btn_no.setIconSize(QSize(28, 28))
        btn_no.setToolTip("Hayır")
        self._as_icon_button(btn_no, danger=True)
        h.addWidget(btn_yes)
        h.addWidget(btn_no)

        def show_confirm():
            w._confirm_mode = True
            self._set_row_actions_visible(w, True)

        def cancel_confirm():
            w._confirm_mode = False
            self._set_row_actions_visible(w, True)

        btn_del.clicked.connect(show_confirm)
        btn_no.clicked.connect(cancel_confirm)
        btn_yes.clicked.connect(lambda: self._action_delete_single(path))

        # Store buttons for hover management
        w._spinner = row_spinner
        w._progress_timer = prog_timer
        w._btn_tr = btn_tr
        w._btn_mp3 = None
        w._btn_del = btn_del
        w._btn_yes = btn_yes
        w._btn_no = btn_no
        w._confirm_mode = False

        # Hover behavior: hide actions by default, show on row hover
        self._attach_hover_behavior(w)
        self._set_row_actions_visible(w, False)

        # Fix asset row height to avoid clipping when buttons appear
        item.setSizeHint(QSize(w.sizeHint().width(), 90))
        self.downloads_list.addItem(item)
        self.downloads_list.setItemWidget(item, w)

    def _find_row_widget_by_path(self, path: Path):
        for i in range(self.downloads_list.count()):
            it = self.downloads_list.item(i)
            data = it.data(Qt.UserRole) or {}
            if Path(str(data.get("path", ""))) == path:
                return self.downloads_list.itemWidget(it)
        return None

    def _set_row_busy(self, row_widget: QWidget | None, busy: bool):
        if row_widget is None:
            return
        sp = getattr(row_widget, "_spinner", None)
        tm = getattr(row_widget, "_progress_timer", None)
        if sp is not None:
            if busy:
                sp.setVisible(True)
                try:
                    sp.setValue(5)
                except Exception:
                    pass
                if tm is not None:
                    tm.start()
            else:
                # Finish bar and hide shortly after
                try:
                    if tm is not None:
                        tm.stop()
                    sp.setValue(100)
                    QTimer.singleShot(600, lambda: (sp.setVisible(False), sp.setValue(0)))
                except Exception:
                    sp.setVisible(False)
        # Disable action buttons while busy
        for name in ("_btn_tr", "_btn_mp3", "_btn_del", "_btn_yes", "_btn_no"):
            b = getattr(row_widget, name, None)
            if b is not None:
                b.setEnabled(not busy)

    def _apply_list_filter(self):
        text = (self.search_edit.text() or "").strip().lower()
        from PySide6.QtCore import Qt as _Qt
        filt = self.filter_combo.currentData(_Qt.UserRole)
        for i in range(self.downloads_list.count()):
            it = self.downloads_list.item(i)
            data = it.data(Qt.UserRole) or {}
            path = str(data.get("path", ""))
            name = Path(path).name.lower()
            kind = data.get("kind", "")
            match_text = (text in name) if text else True
            match_kind = True if filt in (None, "ALL") else (kind == filt)
            it.setHidden(not (match_text and match_kind))

    # ------------------------
    # Downloading row helpers
    # ------------------------
    def _create_downloading_row(self, url: str, quality: str):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, {"url": url, "path": "", "kind": "Video", "state": "Downloading"})
        w = QWidget()
        w.setMinimumHeight(90)
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(12)

        name_lbl = QLabel(url)
        name_lbl.setToolTip(url)
        name_lbl.setMinimumWidth(120)
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h.addWidget(name_lbl, 1)

        pb = QProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        pb.setTextVisible(False)
        pb.setFixedHeight(12)
        pb.setMaximumWidth(160)
        pb.setVisible(True)
        h.addWidget(pb)

        btn_pause = QPushButton()
        btn_pause.setIcon(self._icon("pause"))
        btn_pause.setIconSize(QSize(24, 24))
        self._as_icon_button(btn_pause)
        btn_pause.setToolTip("Durdur")
        h.addWidget(btn_pause)

        btn_stop = QPushButton()
        btn_stop.setIcon(self._icon("stop"))
        btn_stop.setIconSize(QSize(24, 24))
        self._as_icon_button(btn_stop)
        btn_stop.setToolTip("İptal")
        h.addWidget(btn_stop)

        # bind behavior
        def on_pause():
            # toggle pause/resume
            if getattr(w, "_paused", False):
                # resume
                btn_pause.setIcon(self._icon("pause"))
                btn_pause.setToolTip("Durdur")
                w._paused = False
                # start new worker resuming the same URL
                self._resume_from_row(w)
            else:
                # request cancel, will mark paused
                w._pause_requested = True
                if getattr(self, "_active_worker", None) is not None:
                    self._active_worker.request_cancel()

        def on_stop():
            w._stop_requested = True
            if getattr(self, "_active_worker", None) is not None:
                self._active_worker.request_cancel()

        btn_pause.clicked.connect(on_pause)
        btn_stop.clicked.connect(on_stop)

        # attach row attrs
        w._progress_only_bar = pb
        w._btn_pause = btn_pause
        w._btn_stop = btn_stop
        w._url = url
        w._quality = quality
        w._paused = False
        w._pause_requested = False
        w._stop_requested = False

        item.setSizeHint(QSize(w.sizeHint().width(), 100))
        self.downloads_list.insertItem(0, item)
        self.downloads_list.setItemWidget(item, w)
        return item, w

    def _resume_from_row(self, row_widget: QWidget):
        # Start a new worker resuming the same URL
        url = getattr(row_widget, "_url", None)
        quality = getattr(row_widget, "_quality", "best")
        if not url:
            return
        self._set_download_buttons_enabled(False)
        self.progress.setVisible(True)
        worker = DownloadWorker(url, Path(self.settings.download_dir), quality)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.canceled.connect(self._on_download_canceled)
        worker.failed.connect(self._on_failed)
        self._active_thread = thread
        self._active_worker = worker
        self._current_row_widget = row_widget
        self._current_row_item = self.downloads_list.itemAt(0, 0) if False else getattr(self, "_current_row_item", None)
        thread.start()

    def _replace_downloading_with_final(self, url: str, final_path: Path):
        # Replace temp row with a real downloaded item at the same position
        temp_item = getattr(self, "_current_row_item", None)
        if temp_item is not None:
            row = self.downloads_list.row(temp_item)
            if row >= 0:
                self.downloads_list.takeItem(row)
                item = QListWidgetItem()
                item.setData(Qt.UserRole, {"url": url, "path": str(final_path), "kind": "Video"})
                w = self._create_download_item_widget(final_path)
                item.setSizeHint(w.sizeHint())
                self.downloads_list.insertItem(row, item)
                self.downloads_list.setItemWidget(item, w)
                return
        # fallback add to end
        self._add_download_item(url, str(final_path))

    def _on_download_canceled(self):
        # Called when worker canceled (pause or stop)
        try:
            t = getattr(self, "_active_thread", None)
            if t is not None:
                t.quit(); t.wait()
        except Exception:
            pass
        self._active_thread = None
        self._active_worker = None
        self._set_download_buttons_enabled(True)
        self.progress.setVisible(False)
        rw = getattr(self, "_current_row_widget", None)
        if rw is None:
            return
        # determine intent
        if getattr(rw, "_stop_requested", False):
            # remove row
            try:
                it = getattr(self, "_current_row_item", None)
                if it is not None:
                    idx = self.downloads_list.row(it)
                    if idx >= 0:
                        self.downloads_list.takeItem(idx)
            except Exception:
                pass
            self._status("İndirme iptal edildi")
        else:
            # mark paused, swap button to 'play'
            rw._paused = True
            rw._pause_requested = False
            try:
                rw._btn_pause.setIcon(self._icon("play"))
                rw._btn_pause.setToolTip("Devam")
            except Exception:
                pass
            self._status("İndirme durduruldu (devam edilebilir)")

    def _attach_hover_behavior(self, row_widget: QWidget) -> None:
        class _HoverFilter(QObject):
            def __init__(self, owner, target):
                super().__init__(target)
                self._owner = owner
                self._target = target
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Enter:
                    self._owner._set_row_actions_visible(self._target, True)
                elif ev.type() == QEvent.Leave:
                    self._owner._set_row_actions_visible(self._target, False)
                return False
        f = _HoverFilter(self, row_widget)
        row_widget.installEventFilter(f)
        # Keep reference to avoid garbage collection
        setattr(row_widget, "_hover_filter", f)

    def _set_row_actions_visible(self, row_widget: QWidget, visible: bool) -> None:
        btn_tr = getattr(row_widget, "_btn_tr", None)
        btn_mp3 = getattr(row_widget, "_btn_mp3", None)
        btn_del = getattr(row_widget, "_btn_del", None)
        btn_yes = getattr(row_widget, "_btn_yes", None)
        btn_no = getattr(row_widget, "_btn_no", None)
        confirm = bool(getattr(row_widget, "_confirm_mode", False))
        # Hide all by default
        for b in (btn_tr, btn_mp3, btn_del, btn_yes, btn_no):
            if b is not None:
                b.setVisible(False)
        if not visible:
            return
        # Show according to state
        if confirm:
            if btn_yes is not None:
                btn_yes.setVisible(True)
            if btn_no is not None:
                btn_no.setVisible(True)
        else:
            if btn_tr is not None:
                btn_tr.setVisible(True)
            if btn_mp3 is not None:
                btn_mp3.setVisible(True)
            if btn_del is not None:
                btn_del.setVisible(True)
