import subprocess
import time
import socket
import os
import json
import threading
import logging
import sys
import ctypes
import winreg as reg
from logging.handlers import RotatingFileHandler
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import pystray
from pystray import MenuItem as item

# --- 1. WINDOWS IDENTITY & WIN32 API ---
WM_SETICON = 0x80
ICON_SMALL = 0
ICON_BIG = 1

try:
    # Taskbar identity to prevent icon grouping issues [cite: 13-01-2026]
    myappid = 'itu.ossaggelen.netassistant.final'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except:
    pass

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

ICON_PATH = os.path.join(BASE_DIR, "icon.png")
ICON_ICO_PATH = os.path.join(BASE_DIR, "icon.ico")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
LOG_PATH = os.path.join(BASE_DIR, "ITU_Net_Assistant.log")

# --- SINGLE INSTANCE CHECK (MUTEX) [cite: 13-01-2026] ---
instance_mutex = None

def check_single_instance():
    global instance_mutex
    mutex_name = "Global\\ITUNetAssistant_ossaggelen_SingleInstance_Mutex"
    kernel32 = ctypes.windll.kernel32
    instance_mutex = kernel32.CreateMutexW(None, False, mutex_name)
    last_error = kernel32.GetLastError()
    
    if last_error == 183: # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(0, "ITU Net Assistant zaten arka planda çalışıyor!", "Bilgi", 0x40 | 0x0)
        return False
    return True

# --- 2. ADMIN PRIVILEGE (REQUIRED FOR SCHTASKS) ---
def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

if not is_admin():
    executable = sys.executable.replace("python.exe", "pythonw.exe")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, " ".join(sys.argv), None, 0)
    sys.exit()

# --- 3. CONFIG & NETWORK WORKER ---
class Config:
    def __init__(self):
        self.defaults = {
            "adapter_name": "Ethernet", 
            "check_interval": 5, 
            "startup_delay": 5, 
            "log_max_mb": 5, 
            "auto_start": True 
        }
        self.data = self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f: return {**self.defaults, **json.load(f)}
            except: return self.defaults
        return self.defaults

    def save(self):
        with open(SETTINGS_FILE, "w") as f: json.dump(self.data, f, indent=4)

class NetworkWorker:
    def __init__(self, config):
        self.config = config
        self.running = True 
        self.is_active = True 
        self.status = "Initializing..."
        self.reset_lock = threading.Lock()
        self.setup_logging()

    def setup_logging(self):
        max_bytes = self.config.data["log_max_mb"] * 1024 * 1024
        handler = RotatingFileHandler(LOG_PATH, maxBytes=max_bytes, backupCount=1, encoding="utf-8")
        for h in logging.root.handlers[:]: logging.root.removeHandler(h)
        logging.basicConfig(handlers=[handler], level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

    def _socket_check(self, ip, results):
        try:
            socket.setdefaulttimeout(2)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((ip, 80))
            s.close()
            results.append(True)
        except: results.append(False)

    def is_connected(self):
        results = []
        threads = [threading.Thread(target=self._socket_check, args=(ip, results)) for ip in ["8.8.8.8", "1.1.1.1"]]
        for t in threads: t.start()
        for t in threads: t.join()
        connected = any(results)
        self.status = "Active" if connected else "Passive"
        return connected

    def manage_hotspot(self):
        ps_script = """
        $tetheringManager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]::CreateFromConnectionProfile([Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]::GetInternetConnectionProfile())
        if ($tetheringManager -and $tetheringManager.TetheringOperationalState -eq "Off") {
            $tetheringManager.StartTetheringAsync()
            return "STARTED"
        }
        """
        result = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True, creationflags=0x08000000)
        if "STARTED" in result.stdout:
            logging.info("Hotspot was OFF. Automatically STARTED.")

    def reset_adapter_logic(self, is_manual=False):
        if self.reset_lock.locked(): return
        with self.reset_lock:
            if not is_manual:
                time.sleep(1.5)
                if self.is_connected(): return
            self.status = "Resetting..."
            adapter = self.config.data["adapter_name"]
            logging.warning(f"Connection lost. Resetting adapter: {adapter}")
            subprocess.run(f"netsh interface set interface \"{adapter}\" disable", shell=True, creationflags=0x08000000)
            time.sleep(2)
            subprocess.run(f"netsh interface set interface \"{adapter}\" enable", shell=True, creationflags=0x08000000)
            logging.info("Reset complete. Waiting for DHCP...")
            time.sleep(8)
            self.manage_hotspot()

    def run(self):
        logging.info("=== MONITORING STARTED: System active ===")
        time.sleep(self.config.data["startup_delay"])
        while self.running:
            if self.is_active and not self.reset_lock.locked():
                if not self.is_connected():
                    self.reset_adapter_logic()
                else:
                    self.manage_hotspot()
            elif not self.is_active: self.status = "Paused"
            time.sleep(self.config.data["check_interval"])

# --- 4. THE UI APP ---
class ITUApp:
    def __init__(self):
        self.config = Config()
        self.worker = NetworkWorker(self.config)
        self.window = None
        self.icon_photo = None 
        self.tray_img = None

        self.load_raw_assets()
        
        # --- AUTO-START (TASK SCHEDULER WITH BACKGROUND FLAG) [cite: 13-01-2026] ---
        if self.config.data["auto_start"]:
            threading.Thread(target=self.manage_task_scheduler, args=(True,), daemon=True).start()

        threading.Thread(target=self.worker.run, daemon=True).start()
        
        self.icon = pystray.Icon("ITU_Net", self.tray_img, "ITU Net Assistant", 
                        menu=pystray.Menu(
                            item('Open Dashboard', self.show_dashboard, default=True),
                            item('Force Reset', lambda: threading.Thread(target=self.worker.reset_adapter_logic, args=(True,)).start()),
                            pystray.Menu.SEPARATOR,
                            item('Exit Program', self.exit_app)
                        ))
        
        threading.Thread(target=self.icon.run, daemon=True).start()

    def load_raw_assets(self):
        if os.path.exists(ICON_PATH):
            try:
                pil_img = Image.open(ICON_PATH)
                self.tray_img = pil_img.resize((64, 64), Image.Resampling.LANCZOS)
            except: self.fallback_tray_img()
        else: self.fallback_tray_img()

    def fallback_tray_img(self):
        img = Image.new('RGB', (64, 64), (0, 120, 215))
        d = ImageDraw.Draw(img); d.text((10, 20), "ITU", fill="white")
        self.tray_img = img

    def manage_task_scheduler(self, enabled):
        """Creates or Deletes a Task with --background argument [cite: 13-01-2026]."""
        task_name = "ITUNetAssistant"
        exe_p = sys.executable if sys.executable.endswith("exe") else sys.argv[0]
        full_path = os.path.abspath(exe_p)

        try:
            if enabled:
                # --background flag added here [cite: 13-01-2026]
                cmd = f'schtasks /create /tn "{task_name}" /tr "\'{full_path}\' --background" /sc onlogon /rl highest /f'
                subprocess.run(cmd, shell=True, capture_output=True, creationflags=0x08000000)
                logging.info(f"Task Scheduler updated with background flag for {full_path}")
            else:
                cmd = f'schtasks /delete /tn "{task_name}" /f'
                subprocess.run(cmd, shell=True, capture_output=True, creationflags=0x08000000)
        except Exception as e:
            logging.error(f"Task Scheduler error: {e}")

    def set_icon_via_win32(self):
        if not self.window: return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.window.winfo_id())
            hicon = self.icon_photo.tk.call('image', 'get', self.icon_photo.name, '-handle')
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            self.window.wm_iconphoto(True, self.icon_photo)
        except: pass

    def show_dashboard(self, silent=False):
        """Handles both normal and background startup [cite: 13-01-2026]."""
        if self.window:
            if not silent:
                self.window.deiconify()
                self.window.lift()
                self.window.focus_force()
            return
        
        ctk.set_appearance_mode("dark")
        self.window = ctk.CTk()
        self.window.title("ITU Net Assistant")
        self.window.geometry("380x460")
        self.window.protocol("WM_DELETE_WINDOW", self.hide_dashboard)

        if os.path.exists(ICON_ICO_PATH):
            try: self.window.iconbitmap(ICON_ICO_PATH)
            except: pass
        
        if self.tray_img:
            self.icon_photo = ImageTk.PhotoImage(self.tray_img)
            self.window.after(100, self.set_icon_via_win32)

        # UI Components
        ctk.CTkLabel(self.window, text="DASHBOARD", font=("Arial", 22, "bold")).pack(pady=20)
        self.status_lbl = ctk.CTkLabel(self.window, text=f"Status: {self.worker.status}", font=("Arial", 16, "bold"))
        self.status_lbl.pack(pady=10)

        self.btn_act = ctk.CTkButton(self.window, text="ACTIVATE", fg_color="green", command=self.handle_act)
        self.btn_act.pack(pady=5)
        self.btn_deact = ctk.CTkButton(self.window, text="DEACTIVATE", fg_color="#911", command=self.handle_deact)
        self.btn_deact.pack(pady=5)
        
        ctk.CTkLabel(self.window, text="--- Manual Actions ---", font=("Arial", 12)).pack(pady=15)
        ctk.CTkButton(self.window, text="Force Adapter Reset", command=self.handle_reset).pack(pady=5)
        ctk.CTkButton(self.window, text="Open Logs", fg_color="gray", command=lambda: os.startfile(LOG_PATH)).pack(pady=5)
        ctk.CTkButton(self.window, text="Settings", fg_color="#444", command=self.open_settings).pack(pady=5)
        ctk.CTkButton(self.window, text="Exit Program", fg_color="#611", hover_color="#811", command=self.exit_app).pack(pady=(20, 5))

        # IF STARTING IN BACKGROUND, WITHDRAW WINDOW IMMEDIATELY [cite: 13-01-2026]
        if silent:
            self.window.withdraw()

        self.update_ui_loop()
        self.window.mainloop()

    def update_ui_loop(self):
        if self.window:
            color = "green" if self.worker.status == "Active" else "red"
            if "Resetting" in self.worker.status: color = "orange"
            if "Passive" in self.worker.status: color = "gray"
            self.status_lbl.configure(text=f"Status: {self.worker.status}", text_color=color)
            self.window.after(1000, self.update_ui_loop)

    def handle_act(self):
        self.btn_act.configure(state="disabled", text="Activating...")
        self.worker.is_active = True
        self.window.after(6000, lambda: self.btn_act.configure(state="normal", text="ACTIVATE"))

    def handle_deact(self):
        self.btn_deact.configure(state="disabled", text="Deactivating...")
        self.worker.is_active = False
        self.window.after(6000, lambda: self.btn_deact.configure(state="normal", text="DEACTIVATE"))

    def handle_reset(self):
        threading.Thread(target=self.worker.reset_adapter_logic, args=(True,), daemon=True).start()

    def open_settings(self):
        settings_win = ctk.CTkToplevel(self.window)
        settings_win.title("Settings")
        settings_win.geometry("320x420")
        settings_win.attributes("-topmost", True)
        if self.icon_photo: settings_win.wm_iconphoto(True, self.icon_photo)

        fields = {}
        schema = [("Adapter Name:", "adapter_name"), ("Check (s):", "check_interval"), ("Delay (s):", "startup_delay"), ("Log (MB):", "log_max_mb")]
        for l, k in schema:
            ctk.CTkLabel(settings_win, text=l).pack(pady=(5,0))
            ent = ctk.CTkEntry(settings_win, width=180); ent.insert(0, str(self.config.data[k])); ent.pack(); fields[k] = ent
        
        auto_start_var = ctk.BooleanVar(value=self.config.data["auto_start"])
        ctk.CTkCheckBox(settings_win, text="Start with Windows", variable=auto_start_var).pack(pady=15)

        def save():
            try:
                for k in ["adapter_name", "check_interval", "startup_delay", "log_max_mb"]:
                    self.config.data[k] = int(fields[k].get()) if k != "adapter_name" else fields[k].get()
                self.config.data["auto_start"] = auto_start_var.get()
                self.manage_task_scheduler(self.config.data["auto_start"])
                self.config.save(); self.worker.setup_logging(); settings_win.destroy()
            except: pass
        
        ctk.CTkButton(settings_win, text="Save & Apply", command=save, fg_color="green").pack(pady=15)

    def hide_dashboard(self):
        if self.window: self.window.withdraw()

    def exit_app(self):
        self.worker.running = False; self.icon.stop()
        if self.window: self.window.quit(); self.window.destroy()
        sys.exit()

if __name__ == "__main__":
    if not check_single_instance():
        sys.exit()
    
    app = ITUApp()
    # Check if we should start hidden [cite: 13-01-2026]
    is_background = "--background" in sys.argv
    app.show_dashboard(silent=is_background)