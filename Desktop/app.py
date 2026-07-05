"""
NPMSMA Desktop — Social Autopilot
==================================

A PySide6 desktop application whose entire UI is a Three.js-style animated
starfield/nebula interface rendered inside a QWebEngineView. Python handles
everything a browser can't: native file dialogs, OAuth capture, background
uploads, and local token storage. The result looks and feels like a native
app inside a living universe — not a bolted-on webview.

BACKEND
-------
This app talks to the existing FastAPI backend you already deployed:

    POST https://sonuramashishnpm-npmsma.hf.space/data-entry

That endpoint (npmsma.py, already built) receives the video file plus any
platform auth codes you attach, runs the Ollama title/description/hashtag
pipeline, uploads to Supabase storage, and fires the per-platform background
publish tasks (facebook / instagram / linkedin / thread / tiktok / youtube).

This desktop app's only job is: get accounts connected, get a video picked,
POST it to that endpoint, and show the user what's happening.

INSTALL PAGE
------------
Ship the packaged installer (PyInstaller/Briefcase) from:
    https://npmsma.onrender.com

BEFORE RUNNING
--------------
    pip install PySide6 PySide6-Addons requests

Fill in the placeholder OAuth client IDs / secrets / redirect URIs in
OAUTH_CONFIG below with your real app credentials from each platform's
developer console. The redirect_uri you register with each platform MUST
match the one you set here — this app watches the embedded OAuth webview
for that exact redirect and pulls the `code` query param off it.

This is a single file by design so it's easy to read top-to-bottom and easy
to hand to a packager. Feel free to split it once it's stable.
"""

import sys
import os
import json
import mimetypes
import traceback

import requests

from PySide6.QtCore import QObject, Signal, Slot, QThread, QUrl, Qt, QSettings, QUrlQuery
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QDialog, QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebChannel import QWebChannel


# ============================================================================
# CONFIG — fill these in with your real values
# ============================================================================

BACKEND_URL = "https://sonuramashishnpm-npmsma.hf.space"
DATA_ENTRY_ENDPOINT = f"{BACKEND_URL}/data-entry"
INSTALL_SITE_URL = "https://npmsma.onrender.com"

APP_ORG = "NPMAI"
APP_NAME = "NPMSMA"

# Redirect URI registered with every platform's developer console.
# Must be identical (scheme, host, path) to what you configure on each app.
REDIRECT_URI = f"{INSTALL_SITE_URL}/oauth/callback"

OAUTH_CONFIG = {
    "youtube": {
        "label": "YouTube",
        "auth_url": (
            "https://accounts.google.com/o/oauth2/v2/auth"
            "?client_id=YOUR_GOOGLE_CLIENT_ID"
            f"&redirect_uri={REDIRECT_URI}"
            "&response_type=code"
            "&access_type=offline"
            "&prompt=consent"
            "&scope=https://www.googleapis.com/auth/youtube.upload"
        ),
        "redirect_uri": REDIRECT_URI,
    },
    "facebook": {
        "label": "Facebook",
        "auth_url": (
            "https://www.facebook.com/v19.0/dialog/oauth"
            "?client_id=YOUR_FACEBOOK_APP_ID"
            f"&redirect_uri={REDIRECT_URI}"
            "&scope=pages_show_list,pages_read_engagement,pages_manage_posts,publish_video"
            "&response_type=code"
        ),
        "redirect_uri": REDIRECT_URI,
    },
    "instagram": {
        "label": "Instagram",
        "auth_url": (
            "https://www.facebook.com/v19.0/dialog/oauth"
            "?client_id=YOUR_FACEBOOK_APP_ID"
            f"&redirect_uri={REDIRECT_URI}"
            "&scope=instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"
            "&response_type=code"
        ),
        "redirect_uri": REDIRECT_URI,
    },
    "linkedin": {
        "label": "LinkedIn",
        "auth_url": (
            "https://www.linkedin.com/oauth/v2/authorization"
            "?response_type=code"
            "&client_id=YOUR_LINKEDIN_CLIENT_ID"
            f"&redirect_uri={REDIRECT_URI}"
            "&scope=w_member_social,openid,profile"
        ),
        "redirect_uri": REDIRECT_URI,
    },
    "threads": {
        "label": "Threads",
        "auth_url": (
            "https://threads.net/oauth/authorize"
            "?client_id=YOUR_THREADS_APP_ID"
            f"&redirect_uri={REDIRECT_URI}"
            "&scope=threads_basic,threads_content_publish"
            "&response_type=code"
        ),
        "redirect_uri": REDIRECT_URI,
    },
    "tiktok": {
        "label": "TikTok",
        "auth_url": (
            "https://www.tiktok.com/v2/auth/authorize"
            "?client_key=YOUR_TIKTOK_CLIENT_KEY"
            "&scope=user.info.basic,video.upload,video.publish"
            f"&redirect_uri={REDIRECT_URI}"
            "&response_type=code"
        ),
        "redirect_uri": REDIRECT_URI,
    },
}

# Maps our internal platform key -> the form field name data-entry expects
PLATFORM_FIELD = {
    "youtube": "auth_code_yt",
    "facebook": "auth_code_fb",
    "instagram": "auth_code_ig",
    "tiktok": "auth_code_tk",
    "linkedin": "auth_code_ld",
    "threads": "auth_code_td",
}


# ============================================================================
# OAUTH CAPTURE DIALOG
# ============================================================================

class OAuthDialog(QDialog):
    """A small embedded browser that watches for the redirect_uri and pulls
    the `code` param off the URL the moment the platform redirects back."""

    codeReceived = Signal(str)

    def __init__(self, platform_key, auth_url, redirect_uri, parent=None):
        super().__init__(parent)
        self.redirect_uri = redirect_uri
        self.setWindowTitle(f"Connect {OAUTH_CONFIG[platform_key]['label']}")
        self.resize(480, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        layout.addWidget(self.view)
        self.view.urlChanged.connect(self._check_url)
        self.view.load(QUrl(auth_url))

    def _check_url(self, url: QUrl):
        url_str = url.toString()
        if url_str.startswith(self.redirect_uri):
            code = QUrlQuery(url).queryItemValue("code")
            if code:
                self.codeReceived.emit(code)
            self.close()


# ============================================================================
# BACKGROUND UPLOAD WORKER (runs off the GUI thread)
# ============================================================================

class UploadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, video_path, form_fields):
        super().__init__()
        self.video_path = video_path
        self.form_fields = form_fields

    @Slot()
    def run(self):
        try:
            self.progress.emit(5, "Reading video file…")
            filename = os.path.basename(self.video_path)
            mime_type = mimetypes.guess_type(self.video_path)[0] or "video/mp4"

            self.progress.emit(20, "Sending to NPMSMA backend…")
            with open(self.video_path, "rb") as f:
                files = {"video_path": (filename, f, mime_type)}
                response = requests.post(
                    DATA_ENTRY_ENDPOINT,
                    files=files,
                    data=self.form_fields,
                    timeout=1800,
                )

            self.progress.emit(85, "Waiting for AI pipeline + publish tasks…")
            try:
                result = response.json()
            except ValueError:
                result = {"status_code": response.status_code, "raw": response.text[:2000]}

            self.progress.emit(100, "Done")
            self.finished.emit(json.dumps(result))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"{exc}\n{traceback.format_exc(limit=2)}")


# ============================================================================
# BRIDGE — exposed to JavaScript via QWebChannel as `window.bridge`
# ============================================================================

class Bridge(QObject):
    log = Signal(str)
    videoPicked = Signal(str, str)          # full_path, filename
    oauthConnected = Signal(str)             # platform key
    uploadProgress = Signal(int, str)
    uploadDone = Signal(str)                 # json string
    uploadError = Signal(str)
    connectedPlatformsChanged = Signal(str)  # json array of connected keys

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.selected_video_path = None
        self.settings = QSettings(APP_ORG, APP_NAME)
        self._upload_thread = None
        self._upload_worker = None

    # ---- persistence helpers ----

    def _load_tokens(self):
        try:
            return json.loads(self.settings.value("tokens", "{}"))
        except (TypeError, ValueError):
            return {}

    def _save_tokens(self, tokens):
        self.settings.setValue("tokens", json.dumps(tokens))

    # ---- slots callable from JS ----

    @Slot()
    def ready(self):
        """Called once the JS side has finished wiring up signal handlers."""
        self.connectedPlatformsChanged.emit(json.dumps(list(self._load_tokens().keys())))

    @Slot()
    def pick_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self.main_window,
            "Choose a video to publish",
            "",
            "Video Files (*.mp4 *.mov *.mkv *.avi *.webm)",
        )
        if path:
            self.selected_video_path = path
            self.videoPicked.emit(path, os.path.basename(path))

    @Slot(str)
    def connect_platform(self, platform_key):
        cfg = OAUTH_CONFIG.get(platform_key)
        if not cfg:
            self.log.emit(f"Unknown platform: {platform_key}")
            return
        dialog = OAuthDialog(platform_key, cfg["auth_url"], cfg["redirect_uri"], self.main_window)
        dialog.codeReceived.connect(lambda code, p=platform_key: self._on_code_received(p, code))
        dialog.exec()

    def _on_code_received(self, platform_key, code):
        tokens = self._load_tokens()
        tokens[platform_key] = code
        self._save_tokens(tokens)
        self.oauthConnected.emit(platform_key)
        self.connectedPlatformsChanged.emit(json.dumps(list(tokens.keys())))

    @Slot(str)
    def disconnect_platform(self, platform_key):
        tokens = self._load_tokens()
        tokens.pop(platform_key, None)
        self._save_tokens(tokens)
        self.connectedPlatformsChanged.emit(json.dumps(list(tokens.keys())))

    @Slot(str)
    def start_upload(self, selected_platforms_json):
        if not self.selected_video_path:
            self.uploadError.emit("Pick a video first.")
            return

        selected = set(json.loads(selected_platforms_json))
        tokens = self._load_tokens()
        form_fields = {}
        for platform_key in selected:
            code = tokens.get(platform_key)
            field = PLATFORM_FIELD.get(platform_key)
            if code and field:
                form_fields[field] = code

        if not form_fields:
            self.uploadError.emit("Connect at least one platform before publishing.")
            return

        self._upload_thread = QThread(self.main_window)
        self._upload_worker = UploadWorker(self.selected_video_path, form_fields)
        self._upload_worker.moveToThread(self._upload_thread)

        self._upload_thread.started.connect(self._upload_worker.run)
        self._upload_worker.progress.connect(self.uploadProgress)
        self._upload_worker.finished.connect(self._on_upload_finished)
        self._upload_worker.error.connect(self._on_upload_error)

        self._upload_thread.start()

    def _on_upload_finished(self, result_json):
        self.uploadDone.emit(result_json)
        self._upload_thread.quit()

    def _on_upload_error(self, message):
        self.uploadError.emit(message)
        self._upload_thread.quit()

    @Slot(int)
    def set_particle_intensity(self, value):
        self.settings.setValue("particle_intensity", value)

    @Slot(result=int)
    def get_particle_intensity(self):
        return int(self.settings.value("particle_intensity", 70))

    @Slot()
    def open_install_site(self):
        QDesktopServices.openUrl(QUrl(INSTALL_SITE_URL))

    # ---- frameless window controls ----

    @Slot()
    def start_window_move(self):
        handle = self.main_window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    @Slot()
    def minimize_window(self):
        self.main_window.showMinimized()

    @Slot()
    def toggle_maximize_window(self):
        if self.main_window.isMaximized():
            self.main_window.showNormal()
        else:
            self.main_window.showMaximized()

    @Slot()
    def close_window(self):
        self.main_window.close()


# ============================================================================
# CUSTOM WEB PAGE — external links open in the real browser, not inside us
# ============================================================================

class ShellWebEnginePage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.host() and "npmsma.local" not in url.host():
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


# ============================================================================
# HTML / CSS / JS SHELL
# ============================================================================

HTML_SHELL = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>NPMSMA</title>
<style>
  :root {
    --bg-deep:      #05060f;
    --bg-panel:     rgba(16, 18, 38, 0.62);
    --bg-panel-2:   rgba(16, 18, 38, 0.85);
    --border-soft:  rgba(140, 150, 220, 0.18);
    --accent-violet:#7c5cff;
    --accent-cyan:  #33e6cc;
    --accent-pink:  #ff4d8d;
    --text-bright:  #eef0ff;
    --text-muted:   #8890b5;
    --radius:       18px;
  }

  * { box-sizing: border-box; }

  html, body {
    margin: 0; padding: 0; height: 100%; width: 100%;
    background: var(--bg-deep);
    color: var(--text-bright);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    overflow: hidden;
    user-select: none;
  }

  #starfield {
    position: fixed; inset: 0; z-index: 0; display: block;
  }

  #app {
    position: relative; z-index: 1; height: 100vh; width: 100vw;
    display: flex; flex-direction: column;
  }

  /* ---------- custom titlebar ---------- */
  #titlebar {
    height: 40px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 12px 0 18px;
    -webkit-app-region: drag;
    background: linear-gradient(180deg, rgba(10,11,24,0.6), transparent);
  }
  #titlebar .brand {
    font-family: 'Space Grotesk', 'Inter', sans-serif;
    letter-spacing: 0.14em; font-size: 12px; font-weight: 600;
    color: var(--text-muted);
  }
  #titlebar .brand b { color: var(--accent-cyan); }
  #win-controls { display: flex; gap: 8px; -webkit-app-region: no-drag; }
  #win-controls button {
    width: 13px; height: 13px; border-radius: 50%; border: none; cursor: pointer;
    opacity: 0.85; transition: opacity 0.15s, transform 0.15s;
  }
  #win-controls button:hover { opacity: 1; transform: scale(1.15); }
  .win-min { background: #ffbd44; }
  .win-max { background: #00ca4e; }
  .win-close { background: #ff605c; }

  /* ---------- shell layout ---------- */
  #shell { flex: 1; display: flex; min-height: 0; }

  #rail {
    width: 76px; flex-shrink: 0;
    display: flex; flex-direction: column; align-items: center;
    padding: 18px 0; gap: 6px;
    background: rgba(8, 9, 20, 0.35);
    border-right: 1px solid var(--border-soft);
  }
  .rail-btn {
    width: 48px; height: 48px; border-radius: 14px; border: none; cursor: pointer;
    background: transparent; color: var(--text-muted);
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; transition: background 0.2s, color 0.2s, transform 0.2s;
    position: relative;
  }
  .rail-btn:hover { background: rgba(124,92,255,0.12); color: var(--text-bright); transform: translateY(-1px); }
  .rail-btn.active { color: var(--accent-cyan); background: rgba(51,230,204,0.10); }
  .rail-btn.active::before {
    content: ""; position: absolute; left: -18px; top: 50%; transform: translateY(-50%);
    width: 3px; height: 22px; border-radius: 4px;
    background: linear-gradient(180deg, var(--accent-cyan), var(--accent-violet));
  }
  .rail-spacer { flex: 1; }

  #content { flex: 1; min-width: 0; overflow-y: auto; padding: 28px 34px 40px; }

  .page { display: none; animation: pageIn 0.45s ease; }
  .page.active { display: block; }
  @keyframes pageIn {
    from { opacity: 0; transform: translateY(10px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  h1.page-title {
    font-family: 'Space Grotesk', sans-serif; font-weight: 600;
    font-size: 26px; margin: 4px 0 4px;
  }
  p.page-sub { color: var(--text-muted); margin: 0 0 26px; font-size: 14px; }

  .glass {
    background: var(--bg-panel);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius);
    backdrop-filter: blur(18px);
  }

  /* ---------- dashboard orbit ---------- */
  #orbit-wrap {
    position: relative; height: 380px; display: flex; align-items: center; justify-content: center;
    margin-bottom: 8px;
  }
  #hub {
    width: 132px; height: 132px; border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, rgba(124,92,255,0.55), rgba(10,10,26,0.9) 70%);
    border: 1px solid rgba(124,92,255,0.5);
    box-shadow: 0 0 60px rgba(124,92,255,0.35), inset 0 0 30px rgba(124,92,255,0.25);
    display: flex; align-items: center; justify-content: center; flex-direction: column;
    z-index: 2;
  }
  #hub .hub-label { font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 15px; letter-spacing: 0.08em; }
  #hub .hub-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .orbit-ring {
    position: absolute; border: 1px dashed rgba(140,150,220,0.18); border-radius: 50%;
  }
  .orbit-node {
    position: absolute; width: 54px; height: 54px; border-radius: 16px;
    background: var(--bg-panel-2); border: 1px solid var(--border-soft);
    display: flex; align-items: center; justify-content: center; font-size: 22px;
    color: var(--text-muted); cursor: default;
    transition: box-shadow 0.3s, color 0.3s, border-color 0.3s;
  }
  .orbit-node.connected {
    color: var(--accent-cyan); border-color: rgba(51,230,204,0.5);
    box-shadow: 0 0 22px rgba(51,230,204,0.35);
  }
  .orbit-node.dim { animation: pulseDim 2.4s ease-in-out infinite; }
  @keyframes pulseDim { 0%,100% { opacity: 0.55; } 50% { opacity: 0.9; } }

  #stat-row { display: flex; gap: 16px; margin-top: 18px; }
  .stat-card { flex: 1; padding: 16px 18px; }
  .stat-card .num { font-family: 'Space Grotesk', sans-serif; font-size: 24px; font-weight: 600; }
  .stat-card .lbl { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

  /* ---------- connect page ---------- */
  #platform-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
  .platform-card { padding: 20px; display: flex; flex-direction: column; gap: 10px; }
  .platform-card .icon-row { display: flex; align-items: center; justify-content: space-between; }
  .platform-card .icon { font-size: 26px; }
  .platform-card .name { font-weight: 600; font-size: 15px; }
  .status-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--text-muted); }
  .status-dot.on { background: var(--accent-cyan); box-shadow: 0 0 8px var(--accent-cyan); }
  .connect-btn {
    margin-top: 4px; padding: 9px 14px; border-radius: 10px; border: none; cursor: pointer;
    font-size: 13px; font-weight: 600; letter-spacing: 0.02em;
    background: linear-gradient(120deg, var(--accent-violet), var(--accent-cyan));
    color: #05060f; transition: transform 0.15s, opacity 0.15s;
  }
  .connect-btn:hover { transform: translateY(-1px); opacity: 0.92; }
  .connect-btn.connected { background: transparent; border: 1px solid rgba(51,230,204,0.4); color: var(--accent-cyan); }

  /* ---------- upload page ---------- */
  #dropzone {
    padding: 44px 24px; text-align: center; cursor: pointer;
    border: 1.5px dashed rgba(140,150,220,0.3);
    transition: border-color 0.2s, background 0.2s;
  }
  #dropzone:hover, #dropzone.drag-over { border-color: var(--accent-cyan); background: rgba(51,230,204,0.05); }
  #dropzone .dz-icon { font-size: 34px; margin-bottom: 8px; }
  #dropzone .dz-title { font-weight: 600; margin-bottom: 4px; }
  #dropzone .dz-sub { color: var(--text-muted); font-size: 13px; }
  #video-chip {
    display: none; margin-top: 14px; padding: 12px 16px; align-items: center; gap: 10px;
  }
  #video-chip.show { display: flex; }

  #platform-checks { display: flex; flex-wrap: wrap; gap: 10px; margin: 20px 0; }
  .check-pill {
    padding: 9px 14px; border-radius: 20px; font-size: 13px; cursor: pointer;
    border: 1px solid var(--border-soft); color: var(--text-muted);
    transition: all 0.15s;
  }
  .check-pill.selected { color: var(--accent-cyan); border-color: rgba(51,230,204,0.5); background: rgba(51,230,204,0.08); }
  .check-pill.locked { opacity: 0.4; cursor: not-allowed; }

  #publish-btn {
    padding: 13px 26px; border-radius: 12px; border: none; cursor: pointer;
    font-weight: 600; font-size: 14px;
    background: linear-gradient(120deg, var(--accent-violet), var(--accent-pink));
    color: white;
  }
  #publish-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  #progress-wrap { margin-top: 22px; display: none; }
  #progress-wrap.show { display: block; }
  #progress-track { height: 8px; border-radius: 6px; background: rgba(140,150,220,0.15); overflow: hidden; }
  #progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--accent-violet), var(--accent-cyan)); transition: width 0.4s ease; }
  #progress-msg { margin-top: 8px; font-size: 13px; color: var(--text-muted); }

  #result-box { margin-top: 18px; padding: 16px; font-family: monospace; font-size: 12px; white-space: pre-wrap; color: var(--text-muted); display: none; max-height: 220px; overflow-y: auto; }
  #result-box.show { display: block; }

  /* ---------- docs page ---------- */
  .tab-row { display: flex; gap: 8px; margin-bottom: 18px; }
  .tab-btn {
    padding: 8px 16px; border-radius: 10px; border: 1px solid var(--border-soft);
    background: transparent; color: var(--text-muted); cursor: pointer; font-size: 13px;
  }
  .tab-btn.active { color: var(--bg-deep); background: var(--accent-cyan); border-color: var(--accent-cyan); font-weight: 600; }
  .doc-tab { display: none; }
  .doc-tab.active { display: block; }
  pre {
    padding: 18px; border-radius: 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.6;
    background: rgba(6,7,16,0.75); border: 1px solid var(--border-soft); position: relative;
  }
  .copy-btn {
    position: absolute; top: 10px; right: 10px; font-size: 11px; padding: 5px 10px;
    border-radius: 8px; border: 1px solid var(--border-soft); background: rgba(255,255,255,0.04);
    color: var(--text-muted); cursor: pointer;
  }
  .guide-step { display: flex; gap: 14px; padding: 16px; margin-bottom: 12px; }
  .guide-step .step-num {
    width: 30px; height: 30px; flex-shrink: 0; border-radius: 50%;
    background: rgba(124,92,255,0.18); color: var(--accent-violet);
    display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px;
  }
  .guide-step .step-title { font-weight: 600; margin-bottom: 4px; }
  .guide-step .step-body { color: var(--text-muted); font-size: 13.5px; line-height: 1.5; }

  /* ---------- settings ---------- */
  .settings-row { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px; margin-bottom: 12px; }
  .settings-row .label { font-weight: 600; font-size: 14px; }
  .settings-row .sub { color: var(--text-muted); font-size: 12.5px; margin-top: 2px; }
  input[type="range"] { width: 180px; accent-color: var(--accent-cyan); }
  .link-btn { color: var(--accent-cyan); cursor: pointer; font-size: 13px; text-decoration: underline; }

  /* ---------- splash ---------- */
  #splash {
    position: fixed; inset: 0; z-index: 50;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: var(--bg-deep); transition: opacity 0.7s ease;
  }
  #splash .ring {
    width: 96px; height: 96px; border-radius: 50%;
    border: 2px solid transparent; border-top-color: var(--accent-cyan); border-right-color: var(--accent-violet);
    animation: spin 1.1s linear infinite; margin-bottom: 22px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  #splash .brand-name { font-family: 'Space Grotesk', sans-serif; font-size: 22px; letter-spacing: 0.16em; font-weight: 700; }
  #splash .brand-tag { color: var(--text-muted); font-size: 12.5px; margin-top: 6px; letter-spacing: 0.05em; }
  #splash.hide { opacity: 0; pointer-events: none; }

  ::-webkit-scrollbar { width: 8px; }
  ::-webkit-scrollbar-thumb { background: rgba(140,150,220,0.25); border-radius: 8px; }
</style>
</head>
<body>

<canvas id="starfield"></canvas>

<div id="splash">
  <div class="ring"></div>
  <div class="brand-name">NPM<span style="color:var(--accent-cyan)">SMA</span></div>
  <div class="brand-tag">BOOTING SOCIAL AUTOPILOT…</div>
</div>

<div id="app">

  <div id="titlebar">
    <div class="brand">NPM<b>SMA</b> · SOCIAL AUTOPILOT</div>
    <div id="win-controls">
      <button class="win-min" onclick="bridge.minimize_window()" title="Minimize"></button>
      <button class="win-max" onclick="bridge.toggle_maximize_window()" title="Maximize"></button>
      <button class="win-close" onclick="bridge.close_window()" title="Close"></button>
    </div>
  </div>

  <div id="shell">
    <div id="rail">
      <button class="rail-btn active" data-page="dashboard" title="Dashboard">◈</button>
      <button class="rail-btn" data-page="connect" title="Connect Accounts">⚭</button>
      <button class="rail-btn" data-page="upload" title="Upload">⬆</button>
      <button class="rail-btn" data-page="docs" title="Docs">▤</button>
      <div class="rail-spacer"></div>
      <button class="rail-btn" data-page="settings" title="Settings">⚙</button>
    </div>

    <div id="content">

      <!-- DASHBOARD -->
      <div class="page active" id="page-dashboard">
        <h1 class="page-title">Mission Control</h1>
        <p class="page-sub">Your accounts, orbiting one hub. Glowing = connected.</p>

        <div id="orbit-wrap">
          <div class="orbit-ring" style="width:260px;height:260px;"></div>
          <div class="orbit-ring" style="width:340px;height:340px;"></div>
          <div id="hub">
            <div class="hub-label">NPMSMA</div>
            <div class="hub-sub">autopilot core</div>
          </div>
          <div id="orbit-nodes"></div>
        </div>

        <div id="stat-row">
          <div class="glass stat-card"><div class="num" id="stat-connected">0</div><div class="lbl">Accounts connected</div></div>
          <div class="glass stat-card"><div class="num" id="stat-video">—</div><div class="lbl">Video selected</div></div>
          <div class="glass stat-card"><div class="num">FastAPI</div><div class="lbl">Backend engine</div></div>
        </div>
      </div>

      <!-- CONNECT -->
      <div class="page" id="page-connect">
        <h1 class="page-title">Connect Accounts</h1>
        <p class="page-sub">Authorize each platform once — tokens stay on this device.</p>
        <div id="platform-grid"></div>
      </div>

      <!-- UPLOAD -->
      <div class="page" id="page-upload">
        <h1 class="page-title">Upload Center</h1>
        <p class="page-sub">Pick a video, choose where it goes, launch it.</p>

        <div class="glass" id="dropzone" onclick="bridge.pick_video()">
          <div class="dz-icon">☄</div>
          <div class="dz-title">Click to choose a video</div>
          <div class="dz-sub">MP4, MOV, MKV, AVI, WEBM</div>
        </div>
        <div class="glass" id="video-chip">
          <span>🎬</span>
          <span id="video-name">—</span>
        </div>

        <div id="platform-checks"></div>

        <button id="publish-btn" disabled onclick="handlePublish()">Publish to selected platforms</button>

        <div id="progress-wrap">
          <div id="progress-track"><div id="progress-fill"></div></div>
          <div id="progress-msg">Waiting…</div>
        </div>
        <div class="glass" id="result-box"></div>
      </div>

      <!-- DOCS -->
      <div class="page" id="page-docs">
        <h1 class="page-title">Docs</h1>
        <p class="page-sub">How to call the API, and how to use the app.</p>

        <div class="tab-row">
          <button class="tab-btn active" data-doctab="api">Developer / API</button>
          <button class="tab-btn" data-doctab="guide">User Guide</button>
        </div>

        <div class="doc-tab active" id="doc-api">
          <div class="tab-row">
            <button class="tab-btn active" data-lang="curl">cURL</button>
            <button class="tab-btn" data-lang="python">Python</button>
            <button class="tab-btn" data-lang="javascript">JavaScript</button>
            <button class="tab-btn" data-lang="java">Java</button>
          </div>
          <div id="lang-curl" class="lang-block"></div>
          <div id="lang-python" class="lang-block" style="display:none;"></div>
          <div id="lang-javascript" class="lang-block" style="display:none;"></div>
          <div id="lang-java" class="lang-block" style="display:none;"></div>
        </div>

        <div class="doc-tab" id="doc-guide">
          <div class="glass guide-step">
            <div class="step-num">1</div>
            <div><div class="step-title">Connect your accounts</div>
              <div class="step-body">Go to Connect Accounts and authorize each platform you want to post to. This opens each platform's real login screen — NPMSMA never sees your password.</div></div>
          </div>
          <div class="glass guide-step">
            <div class="step-num">2</div>
            <div><div class="step-title">Upload a video</div>
              <div class="step-body">Go to Upload Center, pick a video, tick the platforms to publish to, and hit Publish. The AI writes your title, description, and hashtags automatically.</div></div>
          </div>
          <div class="glass guide-step">
            <div class="step-num">3</div>
            <div><div class="step-title">Track your post</div>
              <div class="step-body">Watch the progress bar — it uploads, runs the AI pipeline, then fires the publish job for every platform you selected.</div></div>
          </div>
        </div>
      </div>

      <!-- SETTINGS -->
      <div class="page" id="page-settings">
        <h1 class="page-title">Settings</h1>
        <p class="page-sub">Tune the experience.</p>

        <div class="glass settings-row">
          <div><div class="label">Particle density</div><div class="sub">Lower this on slower machines.</div></div>
          <input type="range" min="10" max="100" id="particle-slider" />
        </div>
        <div class="glass settings-row">
          <div><div class="label">Check for updates</div><div class="sub">Opens the NPMSMA install page in your browser.</div></div>
          <span class="link-btn" onclick="bridge.open_install_site()">npmsma.onrender.com ↗</span>
        </div>
      </div>

    </div>
  </div>
</div>

<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
/* ============================================================
   STARFIELD — ambient particle background, runs on a canvas
   ============================================================ */
const canvas = document.getElementById('starfield');
const ctx = canvas.getContext('2d');
let particles = [];
let particleCount = 140;

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener('resize', resize);
resize();

function makeParticles() {
  particles = [];
  for (let i = 0; i < particleCount; i++) {
    particles.push({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      r: Math.random() * 1.4 + 0.3,
      vx: (Math.random() - 0.5) * 0.06,
      vy: (Math.random() - 0.5) * 0.06,
      tw: Math.random() * Math.PI * 2,
    });
  }
}
makeParticles();

let shootingStar = null;
function maybeSpawnShootingStar() {
  if (!shootingStar && Math.random() < 0.003) {
    shootingStar = {
      x: Math.random() * canvas.width * 0.6,
      y: Math.random() * canvas.height * 0.3,
      vx: 6 + Math.random() * 4,
      vy: 3 + Math.random() * 2,
      life: 60,
    };
  }
}

function drawFrame() {
  ctx.fillStyle = '#05060f';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // faint nebula glow blobs
  const g1 = ctx.createRadialGradient(canvas.width*0.2, canvas.height*0.25, 0, canvas.width*0.2, canvas.height*0.25, 420);
  g1.addColorStop(0, 'rgba(124,92,255,0.10)');
  g1.addColorStop(1, 'rgba(124,92,255,0)');
  ctx.fillStyle = g1;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const g2 = ctx.createRadialGradient(canvas.width*0.85, canvas.height*0.75, 0, canvas.width*0.85, canvas.height*0.75, 480);
  g2.addColorStop(0, 'rgba(51,230,204,0.08)');
  g2.addColorStop(1, 'rgba(51,230,204,0)');
  ctx.fillStyle = g2;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (const p of particles) {
    p.x += p.vx; p.y += p.vy; p.tw += 0.02;
    if (p.x < 0) p.x = canvas.width; if (p.x > canvas.width) p.x = 0;
    if (p.y < 0) p.y = canvas.height; if (p.y > canvas.height) p.y = 0;
    const alpha = 0.35 + Math.sin(p.tw) * 0.35;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(238,240,255,${alpha})`;
    ctx.fill();
  }

  maybeSpawnShootingStar();
  if (shootingStar) {
    const s = shootingStar;
    ctx.strokeStyle = 'rgba(180,200,255,0.8)';
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(s.x - s.vx * 6, s.y - s.vy * 6);
    ctx.stroke();
    s.x += s.vx; s.y += s.vy; s.life--;
    if (s.life <= 0 || s.x > canvas.width || s.y > canvas.height) shootingStar = null;
  }

  requestAnimationFrame(drawFrame);
}
drawFrame();

/* ============================================================
   NAVIGATION
   ============================================================ */
document.querySelectorAll('.rail-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.rail-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const target = btn.dataset.page;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + target).classList.add('active');
  });
});

document.querySelectorAll('[data-doctab]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-doctab]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.doc-tab').forEach(t => t.classList.remove('active'));
    document.getElementById('doc-' + btn.dataset.doctab).classList.add('active');
  });
});

document.querySelectorAll('[data-lang]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-lang]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.lang-block').forEach(b => b.style.display = 'none');
    document.getElementById('lang-' + btn.dataset.lang).style.display = 'block';
  });
});

/* draggable titlebar via native window move */
document.getElementById('titlebar').addEventListener('mousedown', (e) => {
  if (e.target.closest('#win-controls')) return;
  bridge.start_window_move();
});

/* ============================================================
   PLATFORM DATA + RENDERING
   ============================================================ */
const PLATFORMS = [
  { key: 'youtube',   label: 'YouTube',   icon: '▶' },
  { key: 'facebook',  label: 'Facebook',  icon: '📘' },
  { key: 'instagram', label: 'Instagram', icon: '📸' },
  { key: 'tiktok',    label: 'TikTok',    icon: '🎵' },
  { key: 'linkedin',  label: 'LinkedIn',  icon: '💼' },
  { key: 'threads',   label: 'Threads',   icon: '🧵' },
];
let connectedSet = new Set();
let selectedPublishSet = new Set();
let currentVideoName = null;

function renderOrbit() {
  const wrap = document.getElementById('orbit-nodes');
  wrap.innerHTML = '';
  const radius = 165;
  PLATFORMS.forEach((p, i) => {
    const angle = (i / PLATFORMS.length) * Math.PI * 2 - Math.PI / 2;
    const x = Math.cos(angle) * radius;
    const y = Math.sin(angle) * radius;
    const node = document.createElement('div');
    node.className = 'orbit-node ' + (connectedSet.has(p.key) ? 'connected' : 'dim');
    node.style.left = `calc(50% + ${x}px - 27px)`;
    node.style.top = `calc(50% + ${y}px - 27px)`;
    node.title = p.label + (connectedSet.has(p.key) ? ' · connected' : ' · not connected');
    node.textContent = p.icon;
    wrap.appendChild(node);
  });
}

function renderPlatformGrid() {
  const grid = document.getElementById('platform-grid');
  grid.innerHTML = '';
  PLATFORMS.forEach(p => {
    const connected = connectedSet.has(p.key);
    const card = document.createElement('div');
    card.className = 'glass platform-card';
    card.innerHTML = `
      <div class="icon-row">
        <span class="icon">${p.icon}</span>
        <span class="status-dot ${connected ? 'on' : ''}"></span>
      </div>
      <div class="name">${p.label}</div>
      <button class="connect-btn ${connected ? 'connected' : ''}">${connected ? '✓ Connected' : 'Connect'}</button>
    `;
    card.querySelector('.connect-btn').addEventListener('click', () => {
      if (connected) { bridge.disconnect_platform(p.key); }
      else { bridge.connect_platform(p.key); }
    });
    grid.appendChild(card);
  });
}

function renderPlatformChecks() {
  const row = document.getElementById('platform-checks');
  row.innerHTML = '';
  PLATFORMS.forEach(p => {
    const connected = connectedSet.has(p.key);
    const pill = document.createElement('div');
    pill.className = 'check-pill' + (selectedPublishSet.has(p.key) ? ' selected' : '') + (connected ? '' : ' locked');
    pill.textContent = p.icon + ' ' + p.label;
    pill.title = connected ? '' : 'Connect this account first';
    pill.addEventListener('click', () => {
      if (!connected) return;
      if (selectedPublishSet.has(p.key)) selectedPublishSet.delete(p.key);
      else selectedPublishSet.add(p.key);
      renderPlatformChecks();
      updatePublishButton();
    });
    row.appendChild(pill);
  });
}

function updatePublishButton() {
  const btn = document.getElementById('publish-btn');
  btn.disabled = !(currentVideoName && selectedPublishSet.size > 0);
}

function updateDashboardStats() {
  document.getElementById('stat-connected').textContent = connectedSet.size;
  document.getElementById('stat-video').textContent = currentVideoName ? '✓' : '—';
}

/* ============================================================
   UPLOAD FLOW
   ============================================================ */
function handlePublish() {
  document.getElementById('progress-wrap').classList.add('show');
  document.getElementById('result-box').classList.remove('show');
  document.getElementById('publish-btn').disabled = true;
  bridge.start_upload(JSON.stringify(Array.from(selectedPublishSet)));
}

function onVideoPicked(path, filename) {
  currentVideoName = filename;
  document.getElementById('video-name').textContent = filename;
  document.getElementById('video-chip').classList.add('show');
  updatePublishButton();
  updateDashboardStats();
}

function onUploadProgress(pct, msg) {
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-msg').textContent = msg;
}

function onUploadDone(resultJson) {
  document.getElementById('progress-msg').textContent = 'Published. Backend response below.';
  const box = document.getElementById('result-box');
  try {
    box.textContent = JSON.stringify(JSON.parse(resultJson), null, 2);
  } catch (e) {
    box.textContent = resultJson;
  }
  box.classList.add('show');
  document.getElementById('publish-btn').disabled = false;
}

function onUploadError(message) {
  document.getElementById('progress-msg').textContent = 'Error: ' + message;
  document.getElementById('publish-btn').disabled = false;
}

function onOauthConnected(platformKey) {
  connectedSet.add(platformKey);
  renderOrbit(); renderPlatformGrid(); renderPlatformChecks(); updateDashboardStats();
}

function onConnectedPlatformsChanged(jsonArr) {
  connectedSet = new Set(JSON.parse(jsonArr));
  renderOrbit(); renderPlatformGrid(); renderPlatformChecks(); updateDashboardStats();
}

/* ============================================================
   DOCS CONTENT
   ============================================================ */
function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function docBlock(id, code) {
  const el = document.getElementById(id);
  el.innerHTML = `<pre><button class="copy-btn" onclick="copyCode(this)">Copy</button><code>${escapeHtml(code)}</code></pre>`;
}
function copyCode(btn) {
  const code = btn.parentElement.querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(() => {
    const old = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => btn.textContent = old, 1200);
  });
}

docBlock('lang-curl',
`curl -X POST "__ENDPOINT__" \\
  -F "video_path=@/path/to/video.mp4" \\
  -F "auth_code_yt=YOUR_YOUTUBE_CODE" \\
  -F "auth_code_ig=YOUR_INSTAGRAM_CODE"`);

docBlock('lang-python',
`import requests

url = "__ENDPOINT__"
files = {"video_path": open("video.mp4", "rb")}
data = {
    "auth_code_yt": "YOUR_YOUTUBE_CODE",
    "auth_code_ig": "YOUR_INSTAGRAM_CODE",
}

response = requests.post(url, files=files, data=data, timeout=1800)
print(response.json())`);

docBlock('lang-javascript',
`const form = new FormData();
form.append("video_path", videoFileInput.files[0]);
form.append("auth_code_yt", "YOUR_YOUTUBE_CODE");
form.append("auth_code_ig", "YOUR_INSTAGRAM_CODE");

const res = await fetch("__ENDPOINT__", {
  method: "POST",
  body: form,
});
const result = await res.json();
console.log(result);`);

docBlock('lang-java',
`OkHttpClient client = new OkHttpClient();

RequestBody body = new MultipartBody.Builder()
    .setType(MultipartBody.FORM)
    .addFormDataPart("video_path", "video.mp4",
        RequestBody.create(new File("video.mp4"), MediaType.parse("video/mp4")))
    .addFormDataPart("auth_code_yt", "YOUR_YOUTUBE_CODE")
    .addFormDataPart("auth_code_ig", "YOUR_INSTAGRAM_CODE")
    .build();

Request request = new Request.Builder()
    .url("__ENDPOINT__")
    .post(body)
    .build();

try (Response response = client.newCall(request).execute()) {
    System.out.println(response.body().string());
}`);

document.querySelectorAll('.lang-block pre code').forEach(el => {
  el.textContent = el.textContent.replace(/__ENDPOINT__/g, '__DATA_ENTRY_ENDPOINT__');
});

/* ============================================================
   SETTINGS
   ============================================================ */
document.getElementById('particle-slider').addEventListener('input', (e) => {
  const val = parseInt(e.target.value, 10);
  particleCount = Math.round(20 + (val / 100) * 260);
  makeParticles();
  bridge.set_particle_intensity(val);
});

/* ============================================================
   BOOT SEQUENCE + QWEBCHANNEL WIRING
   ============================================================ */
new QWebChannel(qt.webChannelTransport, function(channel) {
  window.bridge = channel.objects.bridge;

  bridge.videoPicked.connect(onVideoPicked);
  bridge.uploadProgress.connect(onUploadProgress);
  bridge.uploadDone.connect(onUploadDone);
  bridge.uploadError.connect(onUploadError);
  bridge.oauthConnected.connect(onOauthConnected);
  bridge.connectedPlatformsChanged.connect(onConnectedPlatformsChanged);

  renderOrbit();
  renderPlatformGrid();
  renderPlatformChecks();

  bridge.get_particle_intensity(function(val) {
    document.getElementById('particle-slider').value = val;
    particleCount = Math.round(20 + (val / 100) * 260);
    makeParticles();
  });

  bridge.ready();

  setTimeout(() => document.getElementById('splash').classList.add('hide'), 1400);
});
</script>
</body>
</html>
"""

HTML_SHELL = HTML_SHELL.replace("__DATA_ENTRY_ENDPOINT__", DATA_ENTRY_ENDPOINT)


# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NPMSMA — Social Autopilot")
        self.resize(1320, 840)
        self.setMinimumSize(980, 640)

        # Frameless for the immersive look; app draws its own titlebar in HTML.
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)

        self.view = QWebEngineView(self)
        self.page = ShellWebEnginePage(self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)

        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)

        self.page.setHtml(HTML_SHELL, QUrl("https://npmsma.local/"))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
