"""Windows integration: start with Windows, single instance, app identity, icon."""

import ctypes
import socket
import sys
import threading
from pathlib import Path

APP_NAME = "TrikiAudio"
APP_ID = "witw2.TrikiAudio"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTANCE_PORT = 47613  # localhost only; used to find an already running copy

ENTRY_SCRIPT = Path(__file__).with_name("triki_audio.py").resolve()


# ---------------------------------------------------------------- startup
def _gui_python():
    """pythonw.exe runs without a console window."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def startup_command():
    return f'"{_gui_python()}" "{ENTRY_SCRIPT}" --tray'


def is_startup_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_startup(enabled):
    """Add/remove the per-user 'run at sign-in' entry (no admin rights needed)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- identity
def set_app_id():
    """Own taskbar button + icon instead of being grouped under python.exe."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


# ---------------------------------------------------------------- single instance
class SingleInstance:
    """First copy listens on a localhost port; later copies ask it to show itself and exit."""

    def __init__(self, on_activate):
        self.on_activate = on_activate
        self.sock = None

    def acquire(self):
        """True if we are the first instance."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", INSTANCE_PORT))
        except OSError:
            s.close()
            try:
                with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as c:
                    c.sendall(b"show")
            except OSError:
                pass
            return False
        s.listen(2)
        self.sock = s
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                if conn.recv(16).startswith(b"show"):
                    self.on_activate()

    def release(self):
        if self.sock:
            self.sock.close()
