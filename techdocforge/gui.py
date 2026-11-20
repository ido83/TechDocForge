"""PySide6 (Qt) user interface."""

from __future__ import annotations

import logging
import re
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QImage, QKeySequence, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .config import DEFAULT_GATEWAY_URL, DEFAULT_GUIDELINES, DEFAULT_OLLAMA_URL, Settings, is_loopback_host, log_dir, validate_base_url
from .docio import SUPPORTED_INPUT, DocumentError, markdown_to_docx, read_document
from .fsutil import atomic_write_bytes, atomic_write_text
from .llm import LLMConfig, LLMError, build_ssl, list_models, stream_chat
from .mermaid import materialize_mermaid_images, split_mermaid_diagrams
from .prompting import build_messages, clean_output
from .secrets_store import delete_api_key, get_api_key, keyring_available, set_api_key

log = logging.getLogger(__name__)
DOC_FILTER = "Documents (" + " ".join(f"*{e}" for e in SUPPORTED_INPUT) + ")"
_TABLE_DELIMITER = re.compile(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*")
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_MARKDOWN_WRAPPER = re.compile(r"(?is)\A\s*(`{3,}|~{3,})(?:markdown|md)[ \t]*\n(.*)\n\1[ \t]*\s*\Z")
_PREVIEW_INLINE = re.compile(
    r"(?P<code>(?<!`)(?P<ticks>`+)(?!`).+?(?<!`)(?P=ticks)(?!`))"
    r"|(?P<break_tag><br\s*/?>)"
    r"|</?(?:p|div|section|article)(?:\s+[^<>]*)?>",
    re.I,
)
_HTML_LIST_OR_TABLE = re.compile(r"(?i)^\s*<(?:table|ul|ol)(?:\s|>)")


def _prepare_preview_markdown(markdown: str) -> tuple[str, bool]:
    """Normalize model formatting while preserving literal text and code."""
    lines = markdown.split("\n")
    spaced = []
    fence = ""
    table_delimiter = -1
    in_table = False
    has_html_lists_or_tables = False
    for index, line in enumerate(lines):
        match = _CODE_FENCE.match(line)
        if match:
            marker, suffix = match.groups()
            if not fence:
                fence = marker
            elif marker.startswith(fence) and not suffix.strip():
                fence = ""
        elif not fence:
            has_html_lists_or_tables |= bool(_HTML_LIST_OR_TABLE.match(line))
            # Keep backtick spans intact. Convert common HTML wrappers and line breaks
            # before disabling HTML interpretation for Markdown text.
            line = _PREVIEW_INLINE.sub(
                lambda match: match["code"] or ("\u2028" if match["break_tag"] else ""), line
            )
            if in_table and index > table_delimiter and "|" not in line:
                in_table = False
                if line.strip():
                    spaced.append("")
            if (
                not in_table and index + 1 < len(lines) and "|" in line
                and _TABLE_DELIMITER.fullmatch(lines[index + 1])
            ):
                in_table = True
                table_delimiter = index + 1
                if spaced and spaced[-1].strip():
                    spaced.append("")
        spaced.append(line)
    return "\n".join(spaced), has_html_lists_or_tables


class SafePreview(QTextBrowser):
    """Markdown preview that loads only generated local diagram images and never follows links."""

    def __init__(self) -> None:
        super().__init__()
        self.allowed_image_dir: Path | None = None
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self._apply_document_colors()

    def _apply_document_colors(self) -> None:
        palette = self.palette()
        foreground = palette.text().color().name()
        background = palette.base().color().name()
        self.setStyleSheet(
            f"QTextBrowser {{ color: {foreground}; background-color: {background}; }}"
        )
        self.document().setDefaultStyleSheet(
            "body, p, li, td, th, h1, h2, h3, h4, h5, h6, blockquote, pre "
            f"{{ color: {foreground}; }} "
            f"table {{ border-collapse: collapse; width: 100%; color: {foreground}; }} "
            f"td, th {{ border: 1px solid {foreground}; padding: 6px; "
            f"background-color: {background}; color: {foreground}; }} "
            f"ul, ol {{ margin-left: 18px; }} li {{ margin-top: 3px; margin-bottom: 3px; }}"
        )

    def set_preview_markdown(self, markdown: str) -> None:
        self._apply_document_colors()
        markdown, has_html_lists_or_tables = _prepare_preview_markdown(markdown)
        features = QTextDocument.MarkdownFeature
        dialect = features.MarkdownDialectGitHub
        if not has_html_lists_or_tables:
            dialect |= features.MarkdownNoHTML
        self.document().setMarkdown(markdown, dialect)
        # Model-generated inline colors can override the document stylesheet.
        palette = self.palette()
        colors = QTextCharFormat()
        colors.setForeground(palette.text())
        colors.setBackground(palette.base())
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(colors)
        self.verticalScrollBar().setValue(0)

    def loadResource(self, _type, url):  # noqa: N802 - Qt override
        if self.allowed_image_dir is not None:
            path = Path(url.toLocalFile())
            try:
                if path.is_file() and path.resolve().parent == self.allowed_image_dir.resolve():
                    image = QImage(str(path))
                    if not image.isNull():
                        return image
            except OSError:
                pass
        return None


class _Signals(QObject):
    chunk = Signal(str)
    result = Signal(object)
    failed = Signal(str)
    completed = Signal()


class BackgroundTask:
    """Runs work on a daemon thread; results are marshalled to the UI thread via queued Qt signals."""

    def __init__(self) -> None:
        self.signals = _Signals()
        self.cancel_event = threading.Event()

    def start(self, target) -> None:
        threading.Thread(target=self._guard(target), daemon=True).start()

    def _guard(self, target):
        def run():
            try:
                target()
            except (LLMError, DocumentError) as e:
                self.signals.failed.emit(str(e))
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error in background task")
                self.signals.failed.emit(f"Unexpected error. Details are in the log folder: {log_dir()}")
        return run


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.s = s
        f = QFormLayout(self)
        self.verify = QCheckBox("Verify TLS certificates")
        self.verify.setChecked(s.verify_tls)
        self.os_store = QCheckBox("Use OS certificate store (recommended for corporate CAs)")
        self.os_store.setChecked(s.use_os_trust_store)
        self.ca = QLineEdit(s.ca_bundle)
        self.ca.setPlaceholderText("Optional custom CA bundle (.pem)")
        ca_btn = QPushButton("Browse…")
        ca_btn.clicked.connect(self._pick_ca)
        ca_row = QHBoxLayout()
        ca_row.addWidget(self.ca)
        ca_row.addWidget(ca_btn)
        self.proxy = QCheckBox("Use system proxy settings (HTTPS_PROXY etc.)")
        self.proxy.setChecked(s.use_system_proxy)
        self.insecure = QCheckBox("Allow plain HTTP to non-localhost endpoints (NOT recommended)")
        self.insecure.setChecked(s.allow_insecure_remote_http)
        self.timeout = QSpinBox(); self.timeout.setRange(10, 7200); self.timeout.setValue(s.timeout_s); self.timeout.setSuffix(" s")
        self.max_in = QSpinBox(); self.max_in.setRange(1000, 5_000_000); self.max_in.setSingleStep(10000); self.max_in.setValue(s.max_input_chars)
        self.num_ctx = QSpinBox(); self.num_ctx.setRange(2048, 1_048_576); self.num_ctx.setSingleStep(2048); self.num_ctx.setValue(s.ollama_num_ctx)
        self.header_text = QLineEdit(s.header_text)
        self.header_text.setPlaceholderText("Optional text shown in every Word header")
        self.footer_text = QLineEdit(s.footer_text)
        self.footer_text.setPlaceholderText("Optional text shown in every Word footer")
        self.logo = QLineEdit(s.logo_path)
        self.logo.setPlaceholderText("No logo (optional — browse to add)")
        logo_browse = QPushButton("Browse…")
        logo_browse.clicked.connect(self._pick_logo)
        logo_default = QPushButton("Clear logo")
        logo_default.clicked.connect(lambda: self.logo.clear())
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo, 1)
        logo_row.addWidget(logo_browse)
        logo_row.addWidget(logo_default)
        f.addRow(self.verify)
        f.addRow(self.os_store)
        f.addRow("CA bundle:", ca_row)
        f.addRow(self.proxy)
        f.addRow(self.insecure)
        f.addRow("Request timeout:", self.timeout)
        f.addRow("Max input characters:", self.max_in)
        f.addRow("Ollama context (num_ctx):", self.num_ctx)
        f.addRow("Document header:", self.header_text)
        f.addRow("Document footer:", self.footer_text)
        f.addRow("Header logo:", logo_row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _pick_logo(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Document logo", "",
                                          "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*)")
        if p:
            self.logo.setText(p)

    def _pick_ca(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "CA bundle", "", "PEM (*.pem *.crt);;All files (*)")
        if p:
            self.ca.setText(p)

    def _accept(self) -> None:
        if not self.verify.isChecked() and self.s.verify_tls:
            if QMessageBox.warning(self, "Security warning",
                                   "Disabling TLS verification exposes your documents and API key to "
                                   "interception. Continue?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        if self.ca.text() and not Path(self.ca.text()).is_file():
            QMessageBox.warning(self, "Settings", "CA bundle file does not exist.")
            return
        if self.logo.text() and not Path(self.logo.text()).is_file():
            QMessageBox.warning(self, "Settings", "Logo file does not exist.")
            return
        s = self.s
        s.verify_tls, s.use_os_trust_store = self.verify.isChecked(), self.os_store.isChecked()
        s.ca_bundle, s.use_system_proxy = self.ca.text().strip(), self.proxy.isChecked()
        s.allow_insecure_remote_http = self.insecure.isChecked()
        s.timeout_s, s.max_input_chars, s.ollama_num_ctx = self.timeout.value(), self.max_in.value(), self.num_ctx.value()
        s.header_text = self.header_text.text().strip()
        s.footer_text = self.footer_text.text().strip()
        s.logo_path = self.logo.text().strip()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.s = Settings.load()
        self._stream: BackgroundTask | None = None
        self._generation_timeout = QTimer(self)
        self._generation_timeout.setSingleShot(True)
        self._generation_timeout.timeout.connect(self._on_generation_timeout)
        self._tasks: set[BackgroundTask] = set()
        self._preview_assets = tempfile.TemporaryDirectory(prefix="techdocforge-preview-")
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "TechDocForge.png"
        if getattr(sys, "_MEIPASS", None):
            icon_path = Path(sys._MEIPASS) / "assets" / "TechDocForge.png"
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1300, 820)
        self._build_menu()
        self._build_ui()
        self._load_into_ui()

    # ------------------------------------------------------------------ UI construction
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, slot, key in (("&Settings…", self._open_settings, "Ctrl+,"),
                                ("Open &log folder", self._open_logs, None),
                                ("&Quit", self.close, QKeySequence.Quit)):
            a = QAction(text, self)
            if key:
                a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        h = self.menuBar().addMenu("&Help")
        version_action = QAction("&Version", self)
        version_action.triggered.connect(lambda: QMessageBox.information(
            self, "Version", f"{APP_NAME} version {__version__}\nMermaid diagram support: enabled"))
        h.addAction(version_action)
        a = QAction("&About", self)
        a.triggered.connect(lambda: QMessageBox.about(
            self, APP_NAME, f"{APP_NAME} {__version__}\nMermaid diagram support: enabled\nConvert simple documents into template-styled "
                            "technical documentation using Ollama or an OpenAI-compatible LLM gateway."))
        h.addAction(a)

    def _build_ui(self) -> None:
        left = QWidget()
        lv = QVBoxLayout(left)

        conn = QGroupBox("LLM connection")
        cf = QFormLayout(conn)
        self.provider = QComboBox()
        self.provider.addItem("Ollama (local)", "ollama")
        self.provider.addItem("OpenAI-compatible gateway", "openai_compat")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.base_url = QLineEdit()
        self.model = QComboBox(); self.model.setEditable(True); self.model.setMinimumWidth(220)
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self._refresh_models)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.setTextVisible(False)
        self.model_progress.setFixedSize(72, 10)
        self.model_progress.setAccessibleName("Generating document")
        self.model_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #64748b; border-radius: 4px; background: #e2e8f0; }"
            "QProgressBar::chunk { border-radius: 3px; background: qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:0.5 #7c3aed, stop:1 #0d9488); }"
        )
        self.model_progress.hide()
        mrow = QHBoxLayout()
        mrow.addWidget(self.model, 1)
        mrow.addWidget(self.model_progress)
        mrow.addWidget(refresh)
        self.api_key = QLineEdit(); self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Not needed for local Ollama")
        save_key = QPushButton("Save"); save_key.setToolTip("Store in the OS keychain"); save_key.clicked.connect(self._save_key)
        forget_key = QPushButton("Forget"); forget_key.clicked.connect(self._forget_key)
        krow = QHBoxLayout(); krow.addWidget(self.api_key, 1); krow.addWidget(save_key); krow.addWidget(forget_key)
        self.temperature = QDoubleSpinBox(); self.temperature.setRange(0.0, 2.0); self.temperature.setSingleStep(0.1)
        cf.addRow("Provider:", self.provider)
        cf.addRow("Endpoint URL:", self.base_url)
        cf.addRow("Model:", mrow)
        cf.addRow("API key:", krow)
        cf.addRow("Temperature:", self.temperature)
        lv.addWidget(conn)

        docs = QGroupBox("Documents")
        df = QFormLayout(docs)
        self.source = QLineEdit(); self.template = QLineEdit()
        df.addRow("Source doc:", self._file_row(self.source, "Select source document"))
        df.addRow("Template:", self._file_row(self.template, "Select template"))
        hint = QLabel("A .docx template supplies Word styles. Header, footer and logo settings are applied to every .docx section.")
        hint.setWordWrap(True); hint.setStyleSheet("color: gray;")
        df.addRow(hint)
        lv.addWidget(docs)

        g = QGroupBox("Style guidelines")
        gv = QVBoxLayout(g)
        self.guidelines = QPlainTextEdit()
        reset_guidelines = QPushButton("Reset to defaults")
        reset_guidelines.clicked.connect(self._reset_guidelines)
        gv.addWidget(self.guidelines)
        gv.addWidget(reset_guidelines)
        lv.addWidget(g, 1)

        brow = QHBoxLayout()
        self.gen_btn = QPushButton("Generate"); self.gen_btn.setDefault(True); self.gen_btn.clicked.connect(self._generate)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setEnabled(False); self.cancel_btn.clicked.connect(self._cancel)
        brow.addWidget(self.gen_btn); brow.addWidget(self.cancel_btn)
        lv.addLayout(brow)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.tabs = QTabWidget()
        self.output = QPlainTextEdit()
        self.output.setPlaceholderText("Generated Markdown appears here. You can edit it before saving.")
        self.preview = SafePreview()
        self.tabs.addTab(self.output, "Markdown")
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        self.show_mermaid_source = QCheckBox("Show Mermaid source code")
        self.show_mermaid_source.setToolTip("Show Mermaid code in the preview instead of rendered diagrams.")
        self.show_mermaid_source.setEnabled(False)
        self.show_mermaid_source.toggled.connect(self._refresh_preview)
        preview_layout.addWidget(self.show_mermaid_source)
        preview_layout.addWidget(self.preview, 1)
        self.tabs.addTab(preview_page, "Preview")
        self._set_preview_available(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.output.textChanged.connect(self._update_preview_availability)
        rv.addWidget(self.tabs, 1)
        srow = QHBoxLayout()
        self.save_btn = QPushButton("Save as…"); self.save_btn.clicked.connect(self._save_output)
        srow.addStretch(1); srow.addWidget(self.save_btn)
        rv.addLayout(srow)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([460, 840])
        self.setCentralWidget(split)
        version_label = QLabel(f"Version {__version__}")
        version_label.setStyleSheet("padding: 0 6px; color: gray;")
        self.statusBar().addPermanentWidget(version_label)
        if not keyring_available():
            self.statusBar().showMessage("No OS keychain found: API keys will be kept for this session only.")

    def _reset_guidelines(self) -> None:
        self.guidelines.setPlainText(DEFAULT_GUIDELINES)
        self.statusBar().showMessage("Style guidelines reset to defaults.", 5000)

    def _file_row(self, edit: QLineEdit, title: str) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        b = QPushButton("Browse…")

        def pick():
            start = edit.text() or self.s.last_source_dir
            p, _ = QFileDialog.getOpenFileName(self, title, start, f"{DOC_FILTER};;All files (*)")
            if p:
                edit.setText(p)
        b.clicked.connect(pick)
        h.addWidget(edit, 1); h.addWidget(b)
        return w

    # ------------------------------------------------------------------ settings <-> UI
    def _load_into_ui(self) -> None:
        s = self.s
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, self.provider.findData(s.provider)))
        self.provider.blockSignals(False)
        self.base_url.setText(s.base_url)
        if s.model:
            self.model.addItem(s.model)
            self.model.setCurrentText(s.model)
        self.temperature.setValue(s.temperature)
        self.guidelines.setPlainText(s.guidelines)
        self.template.setText(s.last_template)

    def _store_from_ui(self) -> None:
        s = self.s
        s.provider = self.provider.currentData()
        s.base_url = self.base_url.text().strip()
        s.model = self.model.currentText().strip()
        s.temperature = self.temperature.value()
        s.guidelines = self.guidelines.toPlainText()
        s.last_template = self.template.text().strip()
        if self.source.text().strip():
            s.last_source_dir = str(Path(self.source.text().strip()).parent)
        try:
            s.save()
        except OSError as e:
            log.warning("Could not save settings: %s", e)

    def _provider_changed(self) -> None:
        cur = self.base_url.text().strip().rstrip("/")
        if self.provider.currentData() == "ollama" and cur in ("", DEFAULT_GATEWAY_URL):
            self.base_url.setText(DEFAULT_OLLAMA_URL)
        elif self.provider.currentData() == "openai_compat" and cur in ("", DEFAULT_OLLAMA_URL):
            self.base_url.setText(DEFAULT_GATEWAY_URL)

    # ------------------------------------------------------------------ helpers
    def _error(self, msg: str) -> None:
        QMessageBox.critical(self, APP_NAME, msg)

    def _endpoint(self) -> str:
        return validate_base_url(self.base_url.text(), self.s.allow_insecure_remote_http)

    def _build_cfg(self) -> LLMConfig:
        url = self._endpoint()
        key = self.api_key.text().strip() or get_api_key(url)
        host = QUrl(url).host()
        if key and url.startswith("http://") and not is_loopback_host(host):
            raise ValueError("Refusing to send an API key over unencrypted HTTP. Use HTTPS.")
        return LLMConfig(
            provider=self.provider.currentData(), base_url=url, model=self.model.currentText().strip(),
            api_key=key or None, temperature=self.temperature.value(), timeout_s=self.s.timeout_s,
            num_ctx=self.s.ollama_num_ctx,
            verify=build_ssl(self.s.verify_tls, self.s.use_os_trust_store, self.s.ca_bundle),
            trust_env=self.s.use_system_proxy,
        )

    def _run(self, task: BackgroundTask, target) -> None:
        self._tasks.add(task)
        task.signals.completed.connect(lambda: self._tasks.discard(task))
        task.signals.failed.connect(lambda _m: self._tasks.discard(task))
        task.start(target)

    def _set_busy(self, busy: bool) -> None:
        self.gen_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.save_btn.setEnabled(not busy)
        self.output.setReadOnly(busy)
        self.model_progress.setVisible(busy)
        if not busy:
            self._generation_timeout.stop()
            self._update_preview_availability()

    @Slot()
    def _update_preview_availability(self) -> None:
        if not self.output.isReadOnly():
            self._set_preview_available(bool(self.output.toPlainText().strip()))

    def _set_preview_available(self, available: bool) -> None:
        self.tabs.setTabEnabled(1, available)
        if not available:
            self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------ actions
    def _open_settings(self) -> None:
        if SettingsDialog(self.s, self).exec():
            self._store_from_ui()

    def _open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def _save_key(self) -> None:
        key = self.api_key.text().strip()
        if not key:
            self._error("Enter an API key first.")
            return
        try:
            url = self._endpoint()
        except ValueError as e:
            self._error(str(e)); return
        persisted = set_api_key(url, key)
        self.api_key.clear()
        self.api_key.setPlaceholderText("•••••• stored" + (" in OS keychain" if persisted else " for this session"))
        self.statusBar().showMessage("API key saved to OS keychain." if persisted
                                     else "No keychain available: key kept for this session only.", 6000)

    def _forget_key(self) -> None:
        try:
            delete_api_key(self._endpoint())
        except ValueError as e:
            self._error(str(e)); return
        self.api_key.clear()
        self.api_key.setPlaceholderText("Not needed for local Ollama")
        self.statusBar().showMessage("API key removed.", 4000)

    def _refresh_models(self) -> None:
        try:
            cfg = self._build_cfg()
        except (ValueError, OSError) as e:
            self._error(str(e)); return
        task = BackgroundTask()
        task.signals.result.connect(self._models_loaded)
        task.signals.failed.connect(self._error)
        self.statusBar().showMessage("Loading models…")
        self._run(task, lambda: (task.signals.result.emit(list_models(cfg)), task.signals.completed.emit()))

    @Slot(object)
    def _models_loaded(self, names: list[str]) -> None:
        cur = self.model.currentText()
        self.model.clear()
        self.model.addItems(names)
        if cur:
            self.model.setCurrentText(cur)
        self.statusBar().showMessage(f"{len(names)} model(s) available.", 5000)

    def _generate(self) -> None:
        self._store_from_ui()
        try:
            cfg = self._build_cfg()
            if not cfg.model:
                raise ValueError("Select or type a model name.")
            if not self.source.text().strip() or not self.template.text().strip():
                raise ValueError("Choose both a source document and a template.")
            src = read_document(self.source.text().strip())
            tpl = read_document(self.template.text().strip())
        except (ValueError, DocumentError, OSError) as e:
            self._error(str(e)); return
        total = len(src) + len(tpl)
        if total > self.s.max_input_chars:
            self._error(f"Input is {total:,} characters; the limit is {self.s.max_input_chars:,}. "
                        "Shorten the documents or raise the limit in Settings (mind the model's context window).")
            return
        if self.provider.currentData() == "ollama" and total / 3.5 > self.s.ollama_num_ctx * 0.7:
            QMessageBox.warning(self, APP_NAME, "The documents may exceed the Ollama context window "
                                "(num_ctx). Consider increasing it in Settings to avoid truncation.")

        messages = build_messages(src, tpl, self.guidelines.toPlainText())
        task = BackgroundTask()
        self._stream = task
        task.signals.chunk.connect(self._on_chunk)
        task.signals.completed.connect(self._on_done)
        task.signals.failed.connect(self._on_fail)
        limit = self.s.max_output_chars

        def work():
            produced = 0
            for piece in stream_chat(cfg, messages, task.cancel_event):
                produced += len(piece)
                if produced > limit:
                    raise LLMError("Model output exceeded the maximum length; stopped.")
                task.signals.chunk.emit(piece)
            task.signals.completed.emit()

        self.output.clear()
        self.tabs.setCurrentIndex(0)
        self._set_preview_available(False)
        self._set_busy(True)
        self._generation_timeout.start(cfg.timeout_s * 1000)
        self.statusBar().showMessage(f"Generating with {cfg.model}…")
        self._run(task, work)

    def _is_current(self) -> bool:
        return self._stream is not None and self.sender() is self._stream.signals

    @Slot(str)
    def _on_chunk(self, piece: str) -> None:
        if not self._is_current():
            return
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(piece)

    @Slot()
    def _on_done(self) -> None:
        if not self._is_current():
            return
        self._stream = None
        text = clean_output(self.output.toPlainText())
        self.output.setPlainText(text)
        self._set_busy(False)
        if not text.strip():
            self.statusBar().showMessage("The model returned no document text.", 8000)
            return
        self.statusBar().showMessage("Done. Review the document, then Save as… (.md or .docx).", 8000)

    @Slot(str)
    def _on_fail(self, msg: str) -> None:
        if not self._is_current():
            return
        self._stream = None
        self._set_busy(False)
        self.statusBar().clearMessage()
        self._error(msg)

    def _cancel(self) -> None:
        if self._stream:
            self._stream.cancel_event.set()
            self._stream = None
        self._set_busy(False)
        self.statusBar().showMessage("Cancelled.", 4000)

    @Slot()
    def _on_generation_timeout(self) -> None:
        if self._stream is None:
            return
        self._cancel()
        self.statusBar().showMessage("Generation timed out. Review any partial output or adjust the request timeout.")

    def _tab_changed(self, idx: int) -> None:
        if idx == 1:
            self._refresh_preview()

    @Slot()
    def _refresh_preview(self) -> None:
        if self.tabs.currentIndex() != 1:
            return
        markdown = self.output.toPlainText()
        wrapper = _MARKDOWN_WRAPPER.fullmatch(markdown)
        if wrapper:
            markdown = wrapper.group(2)
        _, diagrams = split_mermaid_diagrams(markdown)
        self.show_mermaid_source.setEnabled(bool(diagrams))
        asset_dir = Path(self._preview_assets.name) / "assets"
        preview_markdown = materialize_mermaid_images(
            markdown, asset_dir,
            asset_url_prefix=asset_dir.resolve().as_uri(),
            show_source=self.show_mermaid_source.isChecked(),
        )
        self.preview.allowed_image_dir = asset_dir
        self.preview.set_preview_markdown(preview_markdown)

    def _save_output(self) -> None:
        text = self.output.toPlainText()
        if not text.strip():
            self._error("Nothing to save yet.")
            return
        start = str(Path(self.s.last_source_dir or Path.home()) / "technical_document.docx")
        path, flt = QFileDialog.getSaveFileName(self, "Save document", start,
                                                "Word document (*.docx);;Markdown (*.md)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in (".md", ".docx"):
            p = p.with_suffix(".md" if "Markdown" in flt else ".docx")
        try:
            if p.suffix.lower() == ".docx":
                tpl = self.template.text().strip()
                atomic_write_bytes(p, markdown_to_docx(
                    text,
                    tpl if tpl.lower().endswith(".docx") else None,
                    header_text=self.s.header_text,
                    footer_text=self.s.footer_text,
                    logo_path=self.s.logo_path,
                ), 0o644)
            else:
                export_markdown = materialize_mermaid_images(text, p.parent / "assets")
                atomic_write_text(p, export_markdown, 0o644)
        except (OSError, DocumentError) as e:
            self._error(f"Could not save: {e}"); return
        self.statusBar().showMessage(f"Saved {p}", 8000)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._stream:
            self._stream.cancel_event.set()
        self._store_from_ui()
        super().closeEvent(event)
