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

def find_asset(filename):
    """
    Asset dosyalarini sirasiyla:
    1. PyInstaller onefile temp dizininde (sys._MEIPASS)
    2. Exe'nin veya script'in calistigi klasorde (BASE_DIR)
    3. Exe dist klasorundeyse bir ust proje klasorunde (os.path.dirname(BASE_DIR))
    arar.
    """
    candidates = []
    if hasattr(sys, '_MEIPASS'):
        candidates.append(sys._MEIPASS)
    candidates.append(BASE_DIR)
    candidates.append(os.path.dirname(BASE_DIR))

    for d in candidates:
        if d:
            path = os.path.join(d, filename)
            if os.path.exists(path):
                return os.path.abspath(path)
    return os.path.join(BASE_DIR, filename)

ICON_PATH     = find_asset("icon.png")
ICON_ICO_PATH = find_asset("icon.ico")
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
    NO_CABLE     = "Passive (No Cable)"
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
# 4.1 HOTSPOT REGISTRY & ADVAPI32 DEFINITIONS
# ==============================================================================
def configure_hotspot_registry():
    """Windows'un cihaz bagli degilken hotspot'u otomatik kapatmasini onler."""
    try:
        import winreg
        key_path = r"SYSTEM\CurrentControlSet\Services\icssvc\Settings"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PeerlessTimeoutEnabled", 0, winreg.REG_DWORD, 0)
    except Exception:
        pass

configure_hotspot_registry()

try:
    from ctypes import wintypes
    _advapi32 = ctypes.windll.advapi32
    _advapi32.OpenSCManagerW.restype = wintypes.HANDLE
    _advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _advapi32.OpenServiceW.restype = wintypes.HANDLE
    _advapi32.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    _advapi32.CloseServiceHandle.restype = wintypes.BOOL
    _advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]

    class _SERVICE_STATUS(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
        ]

    _advapi32.QueryServiceStatus.restype = wintypes.BOOL
    _advapi32.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(_SERVICE_STATUS)]
except Exception:
    _advapi32 = None

# ==============================================================================
# 4.2 WINDOWS NETWORK IPHLPAPI DEFINITIONS (ETHERNET LINK & IP STATUS)
# ==============================================================================
try:
    _iphlpapi = ctypes.windll.iphlpapi

    class _SOCKET_ADDRESS(ctypes.Structure):
        _fields_ = [
            ("lpSockaddr", ctypes.c_void_p),
            ("iSockaddrLength", ctypes.c_int),
        ]

    class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
        pass

    _IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
        ("Length", wintypes.ULONG),
        ("Flags", wintypes.DWORD),
        ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
        ("Address", _SOCKET_ADDRESS),
    ]

    class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
        pass

    _IP_ADAPTER_ADDRESSES._fields_ = [
        ("Length", wintypes.ULONG),
        ("IfIndex", wintypes.DWORD),
        ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", wintypes.LPWSTR),
        ("Description", wintypes.LPWSTR),
        ("FriendlyName", wintypes.LPWSTR),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.DWORD),
        ("Flags", wintypes.DWORD),
        ("Mtu", wintypes.DWORD),
        ("IfType", wintypes.DWORD),
        ("OperStatus", wintypes.DWORD),
    ]

    class _SOCKADDR_IN(ctypes.Structure):
        _fields_ = [
            ("sin_family", ctypes.c_short),
            ("sin_port", ctypes.c_ushort),
            ("sin_addr", ctypes.c_ubyte * 4),
            ("sin_zero", ctypes.c_char * 8),
        ]
except Exception:
    _iphlpapi = None

# ==============================================================================
# 5. CONFIG
# ==============================================================================
class Config:
    def __init__(self):
        self.defaults = {
            "adapter_name"  : "Ethernet",
            "check_interval": 5,
            "startup_delay" : 15,
            "log_max_mb"    : 5,
            "auto_start"    : True,
        }
        self.data = self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Settings must be a JSON object")
                merged = {**self.defaults, **data}
                for key in ("check_interval", "startup_delay", "log_max_mb"):
                    value = merged.get(key)
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        merged[key] = self.defaults[key]
                if not isinstance(merged.get("adapter_name"), str) or not merged["adapter_name"].strip():
                    merged["adapter_name"] = self.defaults["adapter_name"]
                if not isinstance(merged.get("auto_start"), bool):
                    merged["auto_start"] = self.defaults["auto_start"]
                return merged
            except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
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
    # Hotspot kapatildiginda en gec bir monitor turunda yeniden acmayi dene.
    HOTSPOT_CHECK_INTERVAL = 5
    HOTSPOT_RETRY_INTERVAL = 5

    def __init__(self, config):
        self.config = config

        # threading.Event: bool'dan farkli olarak gercek anlamda thread-safe
        self._running = threading.Event()
        self._running.set()
        # _running is set during normal work, so waits use a separate stop event.
        self._stop_event = threading.Event()
        self._active  = threading.Event()
        self._active.set()

        # Non-blocking acquire ile TOCTOU race condition'i onluyoruz
        self._reset_lock   = threading.Lock()
        self._hotspot_lock = threading.Lock()

        self._status      = Status.INITIALIZING
        self._status_lock = threading.Lock()

        self._hotspot_last_check    = 0.0
        self._hotspot_failures      = 0
        self._hotspot_backoff_until = 0.0
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
        if value:
            self._stop_event.clear()
            self._running.set()
        else:
            self._running.clear()
            self._stop_event.set()

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

    # --- Adaptor Durum ve Baglanti Kontrolu ---
    def get_adapter_info(self, adapter_name=None):
        """
        Istenen adaptorun durumunu (OperStatus: 1=Up, digerleri=Down) ve
        IPv4 adresini Win32 GetAdaptersAddresses ile 0.1 ms'de dondurur.
        Donus: (is_up: bool, ip_address: str | None)
        """
        if not adapter_name:
            adapter_name = self.config.data["adapter_name"]

        if not _iphlpapi:
            return True, None

        try:
            buflen = wintypes.ULONG(16384)
            buf    = ctypes.create_string_buffer(16384)
            # AF_INET = 2, Flags = 14 (SKIP_ANYCAST | SKIP_MULTICAST | SKIP_DNS_SERVER)
            ret = _iphlpapi.GetAdaptersAddresses(2, 14, None, ctypes.byref(buf), ctypes.byref(buflen))
            if ret != 0:
                return False, None

            curr = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
            while curr:
                a = curr.contents
                if a.FriendlyName and a.FriendlyName.lower() == adapter_name.lower():
                    is_up  = (a.OperStatus == 1)
                    ip_str = None
                    u = a.FirstUnicastAddress
                    while u:
                        sa_ptr = u.contents.Address.lpSockaddr
                        if sa_ptr:
                            sa = _SOCKADDR_IN.from_address(sa_ptr)
                            if sa.sin_family == 2:  # AF_INET
                                parsed_ip = socket.inet_ntoa(bytes(sa.sin_addr))
                                if not parsed_ip.startswith("169.254."):
                                    ip_str = parsed_ip
                                    break
                        u = u.contents.Next
                    return is_up, ip_str
                curr = a.Next
        except Exception as e:
            logging.debug(f"Adapter info query error: {e}")
        return False, None

    def _socket_check(self, ip, port, bind_ip, results, lock):
        # Per-socket timeout: global setdefaulttimeout() tum thread'leri etkiler
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            if bind_ip:
                s.bind((bind_ip, 0))
            s.connect((ip, port))
            with lock:
                results.append(True)
        except OSError:
            with lock:
                results.append(False)
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def _run_checks(self, bind_ip=None):
        """
        Guvenilir 3 hedefe (Cloudflare HTTP, Cloudflare Secondary, Google DNS TCP)
        Ethernet IP'sine bagli socket ile paralel baglanti dener.
        """
        # A check without the selected Ethernet IPv4 would silently use another
        # route (often Wi-Fi) and could conceal a broken Ethernet connection.
        if not bind_ip:
            return False

        results = []
        lock    = threading.Lock()
        targets = [
            ("1.1.1.1", 80),
            ("1.0.0.1", 80),
            ("8.8.8.8", 53),
        ]
        threads = [
            threading.Thread(
                target=self._socket_check, args=(ip, port, bind_ip, results, lock), daemon=True
            )
            for ip, port in targets
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=3)
        return any(results)

    def is_connected(self, bind_ip=None):
        """Baglantiyi kontrol eder ve status'u gunceller."""
        connected = self._run_checks(bind_ip=bind_ip)
        self.status = Status.ACTIVE if connected else Status.PASSIVE
        return connected

    def _raw_check(self, bind_ip=None):
        """
        Status'a DOKUNMADAN baglantiyi kontrol eder.
        reset_adapter_logic icindeki DHCP polling'de kullanilir:
        reset surecinde status "Resetting..." sabit kalmali;
        is_connected() bunu "Passive"/"Active" yapip UI'da flicker'a yol acardi.
        """
        return self._run_checks(bind_ip=bind_ip)

    # --- Hotspot Yonetimi ---
    def has_wifi_hardware(self):
        """
        Sistemde calisir durumda (IfType == 71 / 802.11) bir Wi-Fi adaptoru var mi?
        Wi-Fi karti Kod 10 (CM_PROB_FAILED_START) vermisse veya devre disiysa False doner.
        """
        if not _iphlpapi:
            return True
        try:
            buflen = wintypes.ULONG(16384)
            buf    = ctypes.create_string_buffer(16384)
            ret    = _iphlpapi.GetAdaptersAddresses(2, 14, None, ctypes.byref(buf), ctypes.byref(buflen))
            if ret != 0:
                return False
            curr = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
            while curr:
                a = curr.contents
                if a.IfType == 71:  # IF_TYPE_IEEE80211
                    return True
                curr = a.Next
        except Exception:
            pass
        return False

    def is_hotspot_active(self):
        """
        Hotspot'un aktif olup olmadigini Windows Mobile Hotspot Service (icssvc)
        durumundan 0.05 ms'de, hicbir process spawn etmeden ve CPU harcamadan sorgular.
        Wi-Fi donanimi yoksa veya Kod 10 ile cokmusse dogrudan False doner.
        """
        if not self.has_wifi_hardware():
            return False
        if not _advapi32:
            return False
        try:
            scm = _advapi32.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
            if not scm:
                return False
            try:
                svc = _advapi32.OpenServiceW(scm, "icssvc", 0x0004)  # SERVICE_QUERY_STATUS
                if not svc:
                    return False
                try:
                    status = _SERVICE_STATUS()
                    if _advapi32.QueryServiceStatus(svc, ctypes.byref(status)):
                        # 2: SERVICE_START_PENDING, 4: SERVICE_RUNNING
                        return status.dwCurrentState in (2, 4)
                finally:
                    _advapi32.CloseServiceHandle(svc)
            finally:
                _advapi32.CloseServiceHandle(scm)
        except Exception as e:
            logging.debug(f"Hotspot status check error: {e}")
        return False

    def _hotspot_should_be_active(self):
        """Check the actual tethering state, not just whether icssvc is running."""
        ps_script = (
            "$ErrorActionPreference = 'Stop'; "
            "$cp = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]::GetInternetConnectionProfile(); "
            "if (-not $cp) { Write-Output 'NO_PROFILE'; exit 0 }; "
            "$tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]::CreateFromConnectionProfile($cp); "
            "if (-not $tm) { Write-Output 'NO_MANAGER'; exit 0 }; "
            "Write-Output $tm.TetheringOperationalState.ToString()"
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        try:
            result = subprocess.run(
                ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                 "-Command", ps_script],
                startupinfo=startupinfo, capture_output=True, text=True,
                timeout=8, creationflags=0x08000000,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logging.warning(f"Could not query Windows hotspot state: {e}")
            return None
        if result.returncode != 0:
            logging.warning(f"Hotspot state query failed: {result.stderr.strip()}")
            return None
        state = result.stdout.strip().splitlines()
        if not state:
            return None
        if state[-1] == "On":
            return True
        if state[-1] == "Off":
            return False
        logging.info(f"Hotspot state unavailable ({state[-1]}).")
        return None

    def manage_hotspot(self, force=False):
        now = time.time()

        # The active state is queried through the packaged tethering API. The
        # icssvc service may remain running even when Mobile Hotspot is Off.
        if not self.has_wifi_hardware():
            if self._hotspot_failures == 0:
                logging.warning("No operational Wi-Fi adapter detected (hardware may be in Code 10 error or disabled). Hotspot skipped.")
            self._hotspot_failures += 1
            self._hotspot_backoff_until = now + self.HOTSPOT_RETRY_INTERVAL
            return

        # Do not let a previous failure suppress recovery indefinitely: retry
        # on each normal monitor interval, while still preventing overlap.
        if now < self._hotspot_backoff_until:
            return
        if not force and (now - self._hotspot_last_check < self.HOTSPOT_CHECK_INTERVAL):
            return
        if not self._hotspot_lock.acquire(blocking=False):
            return
        self._hotspot_last_check = now

        def _worker():
            try:
                state = self._hotspot_should_be_active()
                if state is True:
                    self._hotspot_failures = 0
                    self._hotspot_backoff_until = 0.0
                    return
                if state is None:
                    self._hotspot_failures += 1
                    self._hotspot_backoff_until = time.time() + self.HOTSPOT_RETRY_INTERVAL
                    return

                # Off is confirmed by Windows; issue StartTetheringAsync and
                # wait for its real operation result before declaring success.
                ps_script = (
                    "$ErrorActionPreference = 'Stop'; "
                    "$cp = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]::GetInternetConnectionProfile(); "
                    "if (-not $cp) { Write-Output 'NO_PROFILE'; exit 0 }; "
                    "$tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]::CreateFromConnectionProfile($cp); "
                    "if ($tm.TetheringOperationalState.ToString() -eq 'On') { Write-Output 'ALREADY_ON'; exit 0 }; "
                    "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
                    "$ext = [System.WindowsRuntimeSystemExtensions]; "
                    "$asTaskMethod = ($ext.GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
                    "$op = $tm.StartTetheringAsync(); "
                    "$asTask = $asTaskMethod.MakeGenericMethod([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]); "
                    "$task = $asTask.Invoke($null, @($op)); "
                    "if (-not $task.Wait(30000)) { throw 'Start_timed_out' }; "
                    "$result = $task.Result; "
                    "if ($result.Status.ToString() -eq 'Success') { Write-Output 'STARTED' } "
                    "else { throw ('Start_failed: ' + $result.Status.ToString() + ' ' + $result.AdditionalErrorMessage) }"
                )

                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                result = subprocess.run(
                    ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                     "-Command", ps_script],
                    startupinfo=startupinfo, capture_output=True, text=True,
                    timeout=35, creationflags=0x08000000,
                )
                if "STARTED" in result.stdout or "ALREADY_ON" in result.stdout:
                    logging.info("Mobile Hotspot was OFF -> successfully started.")
                    self._hotspot_failures = 0
                    self._hotspot_backoff_until = 0.0
                elif "NO_PROFILE" in result.stdout:
                    logging.warning("Hotspot start skipped: Windows has no internet connection profile.")
                    self._hotspot_backoff_until = time.time() + self.HOTSPOT_RETRY_INTERVAL
                else:
                    detail = result.stderr.strip() or result.stdout.strip() or f"PowerShell exit {result.returncode}"
                    self._hotspot_failures += 1
                    logging.error(f"Mobile Hotspot start failed: {detail}")
                    self._hotspot_backoff_until = time.time() + self.HOTSPOT_RETRY_INTERVAL
            except Exception as e:
                self._hotspot_failures += 1
                logging.exception(f"Mobile Hotspot recovery failed: {e}")
                self._hotspot_backoff_until = time.time() + self.HOTSPOT_RETRY_INTERVAL
            finally:
                self._hotspot_lock.release()

        threading.Thread(target=_worker, daemon=True).start()

    # --- Adaptor Reset ---
    def reset_adapter_logic(self, is_manual=False):
        # Non-blocking acquire: zaten reset varsa ikinci bir reset baslatma
        if not self._reset_lock.acquire(blocking=False):
            return
        try:
            adapter = self.config.data["adapter_name"]

            # Manuel degilse 1.5s bekle; kablo durumunu ve gecici kopuklugu teyit et
            if not is_manual:
                is_up, bind_ip = self.get_adapter_info(adapter)
                if not is_up:
                    logging.info(f"Reset cancelled: '{adapter}' cable is unplugged.")
                    self.status = Status.NO_CABLE
                    return

                time.sleep(1.5)
                is_up, bind_ip = self.get_adapter_info(adapter)
                if not is_up:
                    self.status = Status.NO_CABLE
                    return
                if self._raw_check(bind_ip=bind_ip):
                    return

            self.status = Status.RESETTING
            logging.warning(f"Connection lost on '{adapter}'. Resetting adapter...")

            # shell=False + liste argumanlari: injection riski yok, path guvenligi var
            disabled = None
            try:
                disabled = subprocess.run(
                    ["netsh", "interface", "set", "interface", adapter, "disable"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000
                )
                if disabled.returncode != 0:
                    raise RuntimeError(disabled.stderr.strip() or disabled.stdout.strip() or "netsh disable failed")
                time.sleep(2)
                enabled = subprocess.run(
                    ["netsh", "interface", "set", "interface", adapter, "enable"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000
                )
                if enabled.returncode != 0:
                    raise RuntimeError(enabled.stderr.strip() or enabled.stdout.strip() or "netsh enable failed")
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
                logging.error(f"Adapter reset command failed for '{adapter}': {e}")
                # If disable succeeded but enable failed, make one recovery
                # attempt so a transient netsh error does not strand the NIC.
                if disabled is not None and disabled.returncode == 0:
                    try:
                        subprocess.run(
                            ["netsh", "interface", "set", "interface", adapter, "enable"],
                            capture_output=True, text=True, timeout=10,
                            creationflags=0x08000000
                        )
                    except (OSError, subprocess.TimeoutExpired) as recovery_error:
                        logging.error(f"Adapter re-enable recovery failed: {recovery_error}")
                self.status = Status.PASSIVE
                return

            # --- Dinamik DHCP Polling ---
            # Golet senaryosu: ITU router oturumu sifirladiktan sonra
            # IP alma suresi degisken; sabit 8s yetersiz kaliyordu.
            # _raw_check() kullaniyoruz: reset surecinde "Resetting..." sabit kalsin
            logging.info("Reset complete. Waiting for DHCP (max 25s)...")
            deadline = time.time() + 25
            while time.time() < deadline:
                time.sleep(2)
                is_up, bind_ip = self.get_adapter_info(adapter)
                if is_up and self._raw_check(bind_ip=bind_ip):
                    logging.info("DHCP acquired. Connection restored on Ethernet.")
                    self.status = Status.ACTIVE
                    break
            else:
                # while dongusu break olmadan bittiyse: timeout
                logging.warning("DHCP timeout (25s). Main loop will retry.")
                self.status = Status.PASSIVE

            self._hotspot_last_check = 0.0  # Reset sonrasi hotspot hemen kontrol edilsin
            self.manage_hotspot(force=True)
        finally:
            self._reset_lock.release()

    # --- Ana Izleme Dongusu ---
    def run(self):
        """
        Sadece bilgisayar Ethernet ile internete bagliyken calisir.
        Kablo takili degilse reset atmaz, beklemede kalir.
        Golet'teki 'Silent Drop' sorununu periyodik Ethernet soket testiyle cozer.
        """
        logging.info("=== MONITORING STARTED ===")

        # Akilli baslangic beklemesi:
        # PC acilisinda Windows'un Ethernet kartini ayaga kaldirip DHCP IP almasi 10s surebiliyor.
        # Erken gelirse hemen baslar, gelmezse startup_delay suresince sabirla bekler.
        startup_delay = self.config.data.get("startup_delay", 15)
        logging.info(f"Waiting for Ethernet initialization (up to {startup_delay}s)...")
        deadline = time.time() + startup_delay
        while self._running.is_set() and time.time() < deadline:
            is_up, eth_ip = self.get_adapter_info()
            if is_up and eth_ip and self._raw_check(bind_ip=eth_ip):
                logging.info("Ethernet is ready and connected to internet.")
                break
            self._stop_event.wait(1.0)

        cable_unplugged_logged = False

        while self._running.is_set():
            if self._active.is_set():
                adapter = self.config.data["adapter_name"]
                is_up, eth_ip = self.get_adapter_info(adapter)

                if not is_up:
                    # ETHERNET KABLOSU TAKILI DEGIL
                    self.status = Status.NO_CABLE
                    if not cable_unplugged_logged:
                        logging.info(f"Ethernet cable unplugged or '{adapter}' is down. Suspending auto-repair and auto-hotspot.")
                        cable_unplugged_logged = True
                else:
                    # ETHERNET KABLOSU TAKILI
                    if cable_unplugged_logged:
                        logging.info(f"Ethernet cable reconnected on '{adapter}'. Resuming monitoring.")
                        cable_unplugged_logged = False

                    if not self._reset_lock.locked():
                        if not self.is_connected(bind_ip=eth_ip):
                            logging.warning("No internet on Ethernet -> starting reset...")
                            threading.Thread(
                                target=self.reset_adapter_logic, daemon=True
                            ).start()
                        else:
                            self.manage_hotspot()
            else:
                self.status = Status.PAUSED
                cable_unplugged_logged = False

            # time.sleep() yerine Event.wait():
            # exit_app() _running'i clear edince sleep bitmesini beklemez,
            # program aninda kapanir.
            self._stop_event.wait(timeout=self.config.data["check_interval"])

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
        for path in [find_asset("icon.png"), find_asset("icon.ico")]:
            if path and os.path.exists(path):
                try:
                    with Image.open(path) as pil_img:
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
        try:
            if enabled:
                if getattr(sys, "frozen", False):
                    action = f'"{os.path.abspath(sys.executable)}" --background'
                else:
                    action = f'"{os.path.abspath(sys.executable)}" "{os.path.abspath(sys.argv[0])}" --background'
                # shell=False + liste: path'de bosluk olsa bile guvenli
                result = subprocess.run(
                    [
                        "schtasks", "/create",
                        "/tn", task_name,
                        "/tr", action,
                        "/sc", "onlogon",
                        "/rl", "highest",
                        "/f",
                    ],
                    capture_output=True, text=True, timeout=20,
                    creationflags=0x08000000
                )
                if result.returncode == 0:
                    logging.info(f"Task Scheduler entry created: {action}")
                else:
                    logging.error(f"Task Scheduler create failed: {result.stderr.strip() or result.stdout.strip()}")
            else:
                result = subprocess.run(
                    ["schtasks", "/delete", "/tn", task_name, "/f"],
                    capture_output=True, text=True, timeout=20,
                    creationflags=0x08000000
                )
                if result.returncode == 0:
                    logging.info("Task Scheduler entry removed.")
                else:
                    logging.warning(f"Task Scheduler delete failed: {result.stderr.strip() or result.stdout.strip()}")
        except (OSError, subprocess.TimeoutExpired) as e:
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
            Status.NO_CABLE    : "#d19a66",
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
            if threading.current_thread() is threading.main_thread():
                self._finish_exit()
            else:
                self.window.after(0, self._finish_exit)

    def _finish_exit(self):
        if self.window:
            self.window.quit()
            self.window.destroy()
            self.window = None

# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    if not check_single_instance():
        sys.exit()

    app           = ITUApp()
    is_background = "--background" in sys.argv
    app.show_dashboard(silent=is_background)
