import multiprocessing
multiprocessing.freeze_support()  # must be first — stops PyInstaller subprocesses re-running this file

import socket
import threading
import time
import webbrowser

import requests
import webview

from app import app as flask_app

# ── Port allocation ───────────────────────────────────────────────────────────

def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

FLASK_PORT = _find_free_port()
FLASK_URL = f"http://127.0.0.1:{FLASK_PORT}"

# ── Python API exposed to JS ──────────────────────────────────────────────────

class Api:
    def save_csv(self, file_type: str) -> None:
        endpoint = "downloadtrades" if file_type == "trades" else "downloadincomes"
        filename = f"{file_type}.csv"
        try:
            resp = requests.get(f"{FLASK_URL}/{endpoint}", timeout=10)
            resp.raise_for_status()
            content = resp.text
        except Exception as e:
            print(f"[save_csv] fetch error: {e}")
            return

        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename,
            file_types=("CSV Files (*.csv)", "All Files (*.*)"),
        )
        if not result:
            return
        save_path = result if isinstance(result, str) else result[0]
        try:
            with open(save_path, "w", newline="", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[save_csv] write error: {e}")

    def open_external(self, url: str) -> None:
        webbrowser.open(url)

# ── Flask thread ──────────────────────────────────────────────────────────────

def _start_flask() -> None:
    flask_app.run(host="127.0.0.1", port=FLASK_PORT, debug=False)


def _wait_for_flask(timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(FLASK_URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    flask_thread = threading.Thread(target=_start_flask, daemon=True)
    flask_thread.start()
    # _wait_for_flask()

    # Open all external (non-localhost) links in the system browser
    # webview.settings = {"OPEN_EXTERNAL_LINKS_IN_BROWSER": True}

    api = Api()
    window = webview.create_window(
        "Sharefolio",
        FLASK_URL,
        js_api=api,
        width=1440,
        height=900,
        min_size=(900, 600),
    )

    def _on_new_window(*args, **kwargs):
        """Intercept all new-window requests from pywebview.

        In PyInstaller bundles the bundled WebKit fires this event for every
        navigation, not just target='_blank' links.  We handle it ourselves:
        - Same-origin URLs → load in the existing window.
        - External URLs    → open in the system browser.
        Returning False tells pywebview not to create a new window.
        """
        # pywebview 3.x passes the URL as a plain string positional arg.
        # pywebview 4.x passes an event/request object with a .url attribute.
        raw = args[0] if args else kwargs.get("url", "")
        url = raw.url if hasattr(raw, "url") else raw
        try:
            raw.prevent_default()
        except AttributeError:
            pass

        if not url or url in ("", "about:blank"):
            return False

        if url.startswith(FLASK_URL):
            if webview.windows:
                webview.windows[0].load_url(url)
        else:
            webbrowser.open(url)
        return False

    try:
        window.events.new_window_requested += _on_new_window
    except AttributeError:
        pass

    webview.start()
