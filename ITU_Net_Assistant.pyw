import subprocess
import time
import socket
import os
import json
import threading
import logging
import sys
import ctypes
from enum import Enum
from logging.handlers import RotatingFileHandler
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import pystray
from pystray import MenuItem as item

# ==============================================================================
# 1. WINDOWS IDENTITY
# ==============================================================================
WM_SETICON = 0x80
ICON_SMALL  = 0
ICON_BIG    = 1

try:
    myappid = 'itu.ossaggelen.netassistant.final'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

ICON_PATH     = os.path.join(BASE_DIR, "icon.png")
ICON_ICO_PATH = os.path.join(BASE_DIR, "icon.ico")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
LOG_PATH      = os.path.join(BASE_DIR, "ITU_Net_Assistant.log")

# ==============================================================================
# 2. STATUS ENUM
# String karşılaştırması yerine Enum: typo hatası yok, IDE desteği var
# ==============================================================================
class Status(Enum):
    INITIALIZING = "Initializing..."
    ACTIVE       = "Active"
    PASSIVE      = "Passive"
    RESETTING    = "Resetting..."
    PAUSED       = "Paused"

# ==============================================================================
# 3. SINGLE INSTANCE (MUTEX)
# ==============================================================================
_instance_mutex = None

def check_single_instance():
    global _instance_mutex
    kernel32        = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(
        None, False, "Global\\ITUNetAssistant_ossaggelen_SingleInstance_Mutex"
    )
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            0, "ITU Net Assistant zaten arka planda calisiyor!", "Bilgi", 0x40
        )
        return False
    return True

# ==============================================================================
# 4. ADMIN PRIVILEGE
# ==============================================================================
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

if not is_admin():
    # sys.executable'i dogrudan kullan; replace() ile .pythonw donusumu kirilgan
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 0
    )
    sys.exit()

# ==============================================================================
# 5. CONFIG
# ==============================================================================
class Config:
    def __init__(self):
        self.defaults = {
            "adapter_name"  : "Ethernet",
            "check_interval": 5,
            "startup_delay" : 5,
            "log_max_mb"    : 5,
            "auto_start"    : True,
        }
        self.data = self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return {**self.defaults, **json.load(f)}
            except (json.JSONDecodeError, OSError) as e:
                logging.warning(f"Settings load failed, using defaults: {e}")
                return dict(self.defaults)
        return dict(self.defaults)

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)
        except OSError as e:
            logging.error(f"Settings save failed: {e}")
            raise  # UI katmanina ilet; sessizce yutma

# ==============================================================================
# 6. NETWORK WORKER
# ==============================================================================
class NetworkWorker:
    # Hotspot icin ayri throttle: her check_interval'da powershell spawn etmemek icin
    HOTSPOT_CHECK_INTERVAL = 30

    def __init__(self, config):
        self.config = config

        # threading.Event: bool'dan farkli olarak gercek anlamda thread-safe
        self._running = threading.Event()
        self._running.set()
        self._active  = threading.Event()
        self._active.set()

        # Non-blocking acquire ile TOCTOU race condition'i onluyoruz
        self._reset_lock = threading.Lock()

        self._status      = Status.INITIALIZING
        self._status_lock = threading.Lock()

        self._hotspot_last_check = 0.0
        self.setup_logging()

    # --- Thread-safe property'ler ---
    @property
    def status(self):
        with self._status_lock:
            return self._status

    @status.setter
    def status(self, value: Status):
        with self._status_lock:
            self._status = value

    @property
    def is_active(self):
        return self._active.is_set()

    @is_active.setter
    def is_active(self, value: bool):
        self._active.set() if value else self._active.clear()

    @property
    def running(self):
        return self._running.is_set()

    @running.setter
    def running(self, value: bool):
        self._running.set() if value else self._running.clear()

    # --- Logging ---
    def setup_logging(self):
        max_bytes = self.config.data["log_max_mb"] * 1024 * 1024
        handler   = RotatingFileHandler(
            LOG_PATH, maxBytes=max_bytes, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
            )
        )
        root = logging.getLogger()
        # Eski handler'lari kapat: settings degisince birikmeyi onler
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    # --- Baglanti Kontrolu ---
    def _socket_check(self, ip, results, lock):
        # Per-socket timeout: global setdefaulttimeout() tum thread'leri etkiler
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((ip, 80))
            s.close()
            with lock:
                results.append(True)
        except OSError:
            with lock:
                results.append(False)

    def _run_checks(self):
        """Iki IP'ye paralel socket baglantisi dener. Ham bool doner."""
        results = []
        lock    = threading.Lock()
        threads = [
            threading.Thread(
                target=self._socket_check, args=(ip, results, lock), daemon=True
            )
            for ip in ["8.8.8.8", "1.1.1.1"]
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=4)
        return any(results)

    def is_connected(self):
        """Baglantiyi kontrol eder ve status'u gunceller."""
        connected = self._run_checks()
        self.status = Status.ACTIVE if connected else Status.PASSIVE
        return connected

    def _raw_check(self):
        """
        Status'a DOKUNMADAN baglantiyi kontrol eder.
        reset_adapter_logic icindeki DHCP polling'de kullanilir:
        reset surecinde status "Resetting..." sabit kalmali;
        is_connected() bunu "Passive"/"Active" yapip UI'da flicker'a yol acardi.
        """
        return self._run_checks()

    # --- Hotspot Yonetimi ---
    def manage_hotspot(self):
        now = time.time()
        if now - self._hotspot_last_check < self.HOTSPOT_CHECK_INTERVAL:
            return
        self._hotspot_last_check = now

        # StartTetheringAsync().AsTask().Wait() ile async sonucu bekle
        ps_script = """
        $tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,
               Windows.Networking.NetworkOperators,
               ContentType = WindowsRuntime]::CreateFromConnectionProfile(
                 [Windows.Networking.Connectivity.NetworkInformation,
                  Windows.Networking.Connectivity,
                  ContentType = WindowsRuntime]::GetInternetConnectionProfile())
        if ($tm -and $tm.TetheringOperationalState -eq 'Off') {
            $tm.StartTetheringAsync().AsTask().Wait(5000) | Out-Null
            Write-Output 'STARTED'
        }
        """
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000
            )
            if "STARTED" in result.stdout:
                logging.info("Hotspot was OFF -> automatically STARTED.")
        except (subprocess.TimeoutExpired, OSError) as e:
            logging.warning(f"Hotspot management failed: {e}")

    # --- Adaptor Reset ---
    def reset_adapter_logic(self, is_manual=False):
        # Non-blocking acquire: zaten reset varsa ikinci bir reset baslatma
        if not self._reset_lock.acquire(blocking=False):
            return
        try:
            # Manuel degilse 1.5s bekle; gecici kopuklukta gereksiz reset atmamak icin
            if not is_manual:
                time.sleep(1.5)
                if self.is_connected():
                    return

            self.status = Status.RESETTING
            adapter = self.config.data["adapter_name"]
            logging.warning(f"Connection lost. Resetting adapter: '{adapter}'")

            # shell=False + liste argumanlari: injection riski yok, path guvenligi var
            subprocess.run(
                ["netsh", "interface", "set", "interface", adapter, "disable"],
                capture_output=True, creationflags=0x08000000
            )
            time.sleep(2)
            subprocess.run(
                ["netsh", "interface", "set", "interface", adapter, "enable"],
                capture_output=True, creationflags=0x08000000
            )

            # --- Dinamik DHCP Polling ---
            # Golet senaryosu: ITU router oturumu sifirladiktan sonra
            # IP alma suresi degisken; sabit 8s yetersiz kaliyordu.
            # _raw_check() kullaniyoruz: reset surecinde "Resetting..." sabit kalsin
            logging.info("Reset complete. Waiting for DHCP (max 25s)...")
            deadline = time.time() + 25
            while time.time() < deadline:
                time.sleep(2)
                if self._raw_check():
                    logging.info("DHCP acquired. Connection restored.")
                    break
            else:
                # while dongusu break olmadan bittiyse: timeout
                logging.warning("DHCP timeout (25s). Main loop will retry.")

            self._hotspot_last_check = 0.0  # Reset sonrasi hotspot hemen kontrol edilsin
            self.manage_hotspot()
        finally:
            self._reset_lock.release()

    # --- Ana Izleme Dongusu ---
    def run(self):
        """
        Golet'teki asil sorun 'Silent Drop':
        Yerel IP (10.x.x.x) hic degismeden ITU router'i interneti sessizce keser.
        Windows link state "Up" kalmaya devam eder; NotifyAddrChange gibi kernel
        event'leri bu durumu goremez. Tek guvenilir cozum: periyodik olarak
        disariya (8.8.8.8) dokunup gercek internet erisimini dogrulamak.
        """
        logging.info("=== MONITORING STARTED ===")
        time.sleep(self.config.data["startup_delay"])

        while self._running.is_set():
            if self._active.is_set():
                if not self._reset_lock.locked():
                    if not self.is_connected():
                        logging.warning("No connection -> starting reset...")
                        threading.Thread(
                            target=self.reset_adapter_logic, daemon=True
                        ).start()
                    else:
                        self.manage_hotspot()
            else:
                self.status = Status.PAUSED

            # time.sleep() yerine Event.wait():
            # exit_app() _running'i clear edince sleep bitmesini beklemez,
            # program aninda kapanir.
            self._running.wait(timeout=self.config.data["check_interval"])

# ==============================================================================
# 7. UI APP
# ==============================================================================
class ITUApp:
    def __init__(self):
        self.config     = Config()
        self.worker     = NetworkWorker(self.config)
        self.window     = None
        self.icon_photo = None
        self.tray_img   = None

        self.load_raw_assets()

        if self.config.data["auto_start"]:
            threading.Thread(
                target=self.manage_task_scheduler, args=(True,), daemon=True
            ).start()

        threading.Thread(target=self.worker.run, daemon=True).start()

        self.tray_icon = pystray.Icon(
            "ITU_Net", self.tray_img, "ITU Net Assistant",
            menu=pystray.Menu(
                item("Open Dashboard", self._tray_open, default=True),
                item("Force Reset", lambda *_: threading.Thread(
                    target=self.worker.reset_adapter_logic, args=(True,), daemon=True
                ).start()),
                pystray.Menu.SEPARATOR,
                item("Exit Program", self.exit_app),
            )
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    # pystray callback'leri kendi thread'inden cagirilir; tkinter sadece main
    # thread'de calisabilir. window.after(0,...) ile main thread'e yonlendiriyoruz.
    def _tray_open(self, *_):
        if self.window:
            self.window.after(0, self._bring_to_front)

    def _bring_to_front(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def load_raw_assets(self):
        if os.path.exists(ICON_PATH):
            try:
                # context manager: PIL dosya handle'ini sizdirmaz
                with Image.open(ICON_PATH) as pil_img:
                    self.tray_img = pil_img.resize((64, 64), Image.Resampling.LANCZOS)
                return
            except (OSError, Exception):
                pass
        self._fallback_tray_img()

    def _fallback_tray_img(self):
        img = Image.new("RGB", (64, 64), (0, 120, 215))
        ImageDraw.Draw(img).text((10, 20), "ITU", fill="white")
        self.tray_img = img

    def manage_task_scheduler(self, enabled):
        task_name = "ITUNetAssistant"
        exe_p     = sys.executable if sys.executable.lower().endswith(".exe") else sys.argv[0]
        full_path = os.path.abspath(exe_p)
        try:
            if enabled:
                # shell=False + liste: path'de bosluk olsa bile guvenli
                subprocess.run(
                    [
                        "schtasks", "/create",
                        "/tn", task_name,
                        "/tr", f'"{full_path}" --background',
                        "/sc", "onlogon",
                        "/rl", "highest",
                        "/f",
                    ],
                    capture_output=True, creationflags=0x08000000
                )
                logging.info(f"Task Scheduler entry created: {full_path}")
            else:
                subprocess.run(
                    ["schtasks", "/delete", "/tn", task_name, "/f"],
                    capture_output=True, creationflags=0x08000000
                )
                logging.info("Task Scheduler entry removed.")
        except OSError as e:
            logging.error(f"Task Scheduler error: {e}")

    def set_icon_via_win32(self):
        if not self.window:
            return
        try:
            hwnd  = ctypes.windll.user32.GetParent(self.window.winfo_id())
            hicon = self.icon_photo.tk.call("image", "get", self.icon_photo.name, "-handle")
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG,   hicon)
            self.window.wm_iconphoto(True, self.icon_photo)
        except Exception:
            pass

    def show_dashboard(self, silent=False):
        if self.window:
            if not silent:
                self._bring_to_front()
            return

        ctk.set_appearance_mode("dark")
        self.window = ctk.CTk()
        self.window.title("ITU Net Assistant")
        self.window.geometry("380x460")
        self.window.protocol("WM_DELETE_WINDOW", self.hide_dashboard)

        if os.path.exists(ICON_ICO_PATH):
            try:
                self.window.iconbitmap(ICON_ICO_PATH)
            except Exception:
                pass

        if self.tray_img:
            self.icon_photo = ImageTk.PhotoImage(self.tray_img)
            self.window.after(100, self.set_icon_via_win32)

        # --- UI ---
        ctk.CTkLabel(
            self.window, text="DASHBOARD", font=("Arial", 22, "bold")
        ).pack(pady=20)

        self.status_lbl = ctk.CTkLabel(
            self.window,
            text=f"Status: {self.worker.status.value}",
            font=("Arial", 16, "bold"),
        )
        self.status_lbl.pack(pady=10)

        self.btn_act = ctk.CTkButton(
            self.window, text="ACTIVATE", fg_color="green", command=self.handle_act
        )
        self.btn_act.pack(pady=5)

        self.btn_deact = ctk.CTkButton(
            self.window, text="DEACTIVATE", fg_color="#911", command=self.handle_deact
        )
        self.btn_deact.pack(pady=5)

        ctk.CTkLabel(
            self.window, text="--- Manual Actions ---", font=("Arial", 12)
        ).pack(pady=15)

        ctk.CTkButton(
            self.window, text="Force Adapter Reset", command=self.handle_reset
        ).pack(pady=5)
        ctk.CTkButton(
            self.window, text="Open Logs", fg_color="gray",
            command=lambda: os.startfile(LOG_PATH)
        ).pack(pady=5)
        ctk.CTkButton(
            self.window, text="Settings", fg_color="#444", command=self.open_settings
        ).pack(pady=5)
        ctk.CTkButton(
            self.window, text="Exit Program",
            fg_color="#611", hover_color="#811", command=self.exit_app
        ).pack(pady=(20, 5))

        if silent:
            self.window.withdraw()

        self.update_ui_loop()
        self.window.mainloop()

    def update_ui_loop(self):
        if not self.window:
            return
        status    = self.worker.status
        color_map = {
            Status.ACTIVE      : "green",
            Status.RESETTING   : "orange",
            Status.PASSIVE     : "gray",
            Status.PAUSED      : "gray",
            Status.INITIALIZING: "white",
        }
        color = color_map.get(status, "white")
        self.status_lbl.configure(text=f"Status: {status.value}", text_color=color)
        self.window.after(1000, self.update_ui_loop)

    def handle_act(self):
        self.btn_act.configure(state="disabled", text="Activating...")
        self.worker.is_active = True
        self.window.after(2000, lambda: self.btn_act.configure(state="normal", text="ACTIVATE"))

    def handle_deact(self):
        self.btn_deact.configure(state="disabled", text="Deactivating...")
        self.worker.is_active = False
        self.window.after(2000, lambda: self.btn_deact.configure(state="normal", text="DEACTIVATE"))

    def handle_reset(self):
        threading.Thread(
            target=self.worker.reset_adapter_logic, args=(True,), daemon=True
        ).start()

    def open_settings(self):
        win = ctk.CTkToplevel(self.window)
        win.title("Settings")
        win.geometry("320x420")
        win.attributes("-topmost", True)
        if self.icon_photo:
            win.wm_iconphoto(True, self.icon_photo)

        fields = {}
        schema = [
            ("Adapter Name:",        "adapter_name"),
            ("Check Interval (s):",  "check_interval"),
            ("Startup Delay (s):",   "startup_delay"),
            ("Log Max (MB):",        "log_max_mb"),
        ]
        for label_text, key in schema:
            ctk.CTkLabel(win, text=label_text).pack(pady=(5, 0))
            ent = ctk.CTkEntry(win, width=180)
            ent.insert(0, str(self.config.data[key]))
            ent.pack()
            fields[key] = ent

        auto_var = ctk.BooleanVar(value=self.config.data["auto_start"])
        ctk.CTkCheckBox(win, text="Start with Windows", variable=auto_var).pack(pady=15)

        error_lbl = ctk.CTkLabel(win, text="", text_color="red", wraplength=280)
        error_lbl.pack(pady=(0, 5))

        def save():
            try:
                new_data = {}
                for key in ["adapter_name", "check_interval", "startup_delay", "log_max_mb"]:
                    raw = fields[key].get().strip()
                    if key == "adapter_name":
                        if not raw:
                            raise ValueError("Adapter Name bos birakilamaz.")
                        new_data[key] = raw
                    else:
                        val = int(raw)
                        if val <= 0:
                            raise ValueError(f"'{key}' pozitif bir tam sayi olmali.")
                        new_data[key] = val
                new_data["auto_start"] = auto_var.get()
                self.config.data.update(new_data)
                self.config.save()
                self.worker.setup_logging()
                threading.Thread(
                    target=self.manage_task_scheduler,
                    args=(new_data["auto_start"],),
                    daemon=True
                ).start()
                win.destroy()
            except ValueError as e:
                error_lbl.configure(text=str(e))
            except OSError as e:
                error_lbl.configure(text=f"Kaydetme hatasi: {e}")

        ctk.CTkButton(win, text="Save & Apply", command=save, fg_color="green").pack(pady=10)

    def hide_dashboard(self):
        if self.window:
            self.window.withdraw()

    def exit_app(self, *_):
        self.worker.running = False   # _running.clear() -> wait() aninda uyanir
        self.tray_icon.stop()
        if self.window:
            self.window.quit()
            self.window.destroy()
        sys.exit()

# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    if not check_single_instance():
        sys.exit()

    app           = ITUApp()
    is_background = "--background" in sys.argv
    app.show_dashboard(silent=is_background)
