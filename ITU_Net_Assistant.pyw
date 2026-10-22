import subprocess
import time
import socket
import os
import json
import threading
import logging
import sys
import ctypes
import platform
import atexit
from ctypes import wintypes
from datetime import datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import pystray
from pystray import MenuItem as item

APP_VERSION = "2026.10.22"

# ==============================================================================
# 1. WINDOWS IDENTITY
# ==============================================================================
WM_SETICON = 0x80
ICON_SMALL  = 0
ICON_BIG    = 1
CREATE_NO_WINDOW = 0x08000000

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
# 1.1 LOGGING
# Temiz, insan tarafindan okunabilir, sadece gercek olaylarda yazan logger.
# ==============================================================================
LOG_FORMAT         = "[%(asctime)s] %(levelname)s: %(message)s"
LOG_DATE_FORMAT    = "%Y-%m-%d %H:%M:%S"
LOG_BACKUP_COUNT   = 3
DEFAULT_LOG_MAX_MB = 10

def setup_logging(max_mb=DEFAULT_LOG_MAX_MB):
    try:
        max_bytes = int(max_mb) * 1024 * 1024
    except (TypeError, ValueError):
        max_bytes = DEFAULT_LOG_MAX_MB * 1024 * 1024

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    try:
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=max_bytes, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    root.addHandler(handler)
    # Varsayilan log seviyesi INFO: stabil durumda gereksiz ping/hotspot spam'i yapmaz
    root.setLevel(logging.INFO)
    for noisy in ("PIL", "pystray", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

def _log_unhandled(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logging.critical("Beklenmeyen ana iş parçacığı hatası:", exc_info=(exc_type, exc_value, exc_tb))

def _log_thread_exception(args):
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "?"
    logging.critical(
        f"İş parçacığında beklenmeyen hata ('{name}'):",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )

sys.excepthook       = _log_unhandled
threading.excepthook = _log_thread_exception
setup_logging()

def _one_line(text, limit=1000):
    """Cok satirli komut ciktilarini tek log satirina indirger."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("oem", errors="replace")
    s = " | ".join(line.strip() for line in str(text).splitlines() if line.strip())
    if len(s) > limit:
        s = s[:limit] + "..."
    return s

def run_cmd(args, timeout, label=None):
    """Gizli pencereyle komut calistirir; yalnizca hata durumunda log yazar."""
    label = label or args[0]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="oem", errors="replace",
            timeout=timeout, startupinfo=startupinfo, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        logging.error(f"Komut zaman aşımına uğradı ({timeout}s): {label}")
        raise
    except OSError as e:
        logging.error(f"Komut başlatılamadı ({label}): {e}")
        raise
    if result.returncode != 0:
        err = _one_line(result.stderr or result.stdout)
        logging.warning(f"Komut hata döndürdü ({label}, kod={result.returncode}): {err}")
    return result

# ==============================================================================
# 2. STATUS ENUM
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
            0, "ITU Net Assistant zaten arka planda çalışıyor!", "Bilgi", 0x40
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
    if getattr(sys, "frozen", False):
        _params = subprocess.list2cmdline(sys.argv[1:])
    else:
        _params = subprocess.list2cmdline(sys.argv)
    _rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, _params, None, 0)
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
# 4.2 WINDOWS NETWORK IPHLPAPI DEFINITIONS
# ==============================================================================
AF_UNSPEC                 = 0
AF_INET                   = 2
GAA_FLAG_SKIP_ANYCAST     = 0x0002
GAA_FLAG_SKIP_MULTICAST   = 0x0004
GAA_FLAG_INCLUDE_GATEWAYS = 0x0080
ERROR_BUFFER_OVERFLOW     = 111
ERROR_NO_DATA             = 232
IF_TYPE_IEEE80211         = 71

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
        ("FirstDnsServerAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
        ("DnsSuffix", wintypes.LPWSTR),
        ("Description", wintypes.LPWSTR),
        ("FriendlyName", wintypes.LPWSTR),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.DWORD),
        ("Flags", wintypes.DWORD),
        ("Mtu", wintypes.DWORD),
        ("IfType", wintypes.DWORD),
        ("OperStatus", wintypes.DWORD),
        ("Ipv6IfIndex", wintypes.DWORD),
        ("ZoneIndices", wintypes.DWORD * 16),
        ("FirstPrefix", ctypes.c_void_p),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("FirstWinsServerAddress", ctypes.c_void_p),
        ("FirstGatewayAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
        ("Ipv4Metric", wintypes.ULONG),
        ("Ipv6Metric", wintypes.ULONG),
        ("Luid", ctypes.c_uint64),
        ("Dhcpv4Server", _SOCKET_ADDRESS),
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

def _sockaddr_to_ipv4(sock_address):
    ptr = sock_address.lpSockaddr
    if not ptr:
        return None
    sa = _SOCKADDR_IN.from_address(ptr)
    if sa.sin_family != AF_INET:
        return None
    return socket.inet_ntoa(bytes(sa.sin_addr))

def _walk_ipv4_list(first):
    out, node, guard = [], first, 0
    while node and guard < 64:
        ip = _sockaddr_to_ipv4(node.contents.Address)
        if ip:
            out.append(ip)
        node = node.contents.Next
        guard += 1
    return out

def enumerate_adapters():
    if not _iphlpapi:
        return None
    flags = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_INCLUDE_GATEWAYS
    size  = 32768
    buf   = None
    for _ in range(4):
        buflen = wintypes.ULONG(size)
        buf    = ctypes.create_string_buffer(size)
        ret    = _iphlpapi.GetAdaptersAddresses(AF_UNSPEC, flags, None, ctypes.byref(buf), ctypes.byref(buflen))
        if ret == ERROR_BUFFER_OVERFLOW:
            size = buflen.value + 4096
            continue
        if ret == ERROR_NO_DATA:
            return []
        if ret != 0:
            return None
        break
    else:
        return None

    adapters  = []
    full_size = ctypes.sizeof(_IP_ADAPTER_ADDRESSES)
    curr = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while curr:
        a        = curr.contents
        extended = a.Length >= full_size
        ipv4_all = _walk_ipv4_list(a.FirstUnicastAddress)
        adapters.append({
            "name"       : a.FriendlyName or "",
            "description": a.Description or "",
            "if_type"    : a.IfType,
            "oper_status": a.OperStatus,
            "ipv4"       : [ip for ip in ipv4_all if not ip.startswith("169.254.")],
            "gateways"   : _walk_ipv4_list(a.FirstGatewayAddress) if extended else [],
        })
        curr = a.Next
    return adapters

# ==============================================================================
# 5. CONFIG
# ==============================================================================
class Config:
    def __init__(self):
        self.defaults = {
            "adapter_name"  : "Ethernet",
            "check_interval": 5,
            "startup_delay" : 15,
            "log_max_mb"    : DEFAULT_LOG_MAX_MB,
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
            except Exception:
                return dict(self.defaults)
        return dict(self.defaults)

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)
        except OSError as e:
            logging.error(f"Ayarlar kaydedilemedi: {e}")
            raise

# ==============================================================================
# 6. NETWORK WORKER
# ==============================================================================
class NetworkWorker:
    HOTSPOT_ON_INTERVAL    = 60  # Hotspot 'On' iken kontrol periyodu (CPU/işlem tasarrufu)
    HOTSPOT_OFF_INTERVAL   = 10  # Hotspot kapalıyken veya bilinmiyorken kontrol periyodu
    HOTSPOT_RETRY_INTERVAL = 10
    HOTSPOT_MAX_BACKOFF    = 120

    FAILS_BEFORE_RESET = 3
    POST_ACTION_GRACE  = 30
    RESET_BACKOFF      = [0, 30, 60, 120, 300, 600]
    STABLE_RESET_CLEAR = 180
    NETSH_TIMEOUT      = 30
    DHCP_WAIT          = 25

    CHECK_TARGETS = [
        ("1.1.1.1", 80),
        ("1.0.0.1", 80),
        ("8.8.8.8", 53),
    ]

    def __init__(self, config):
        self.config = config

        self._running    = threading.Event()
        self._running.set()
        self._stop_event = threading.Event()
        self._active     = threading.Event()
        self._active.set()

        self._reset_lock   = threading.Lock()
        self._hotspot_lock = threading.Lock()

        self._status      = Status.INITIALIZING
        self._status_lock = threading.Lock()

        # Hotspot
        self._hotspot_last_check    = 0.0
        self._hotspot_failures      = 0
        self._hotspot_backoff_until = 0.0
        self._hotspot_last_state    = None
        self._wifi_hw_present       = None

        # Reset & Baglanti
        self._consecutive_failures = 0
        self._first_failure_at     = None
        self._grace_until          = 0.0
        self._grace_reason         = ""
        self._reset_streak         = 0
        self._next_reset_allowed   = 0.0
        self._backoff_logged       = False
        self._healthy_since        = None
        self._cable_unplugged_logged = False
        self._last_adapter_ip      = None

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
        logging.info(f"İzleme kullanıcı tarafından {'AKTİF EDİLDİ' if value else 'DURDURULDU'}.")
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

    def setup_logging(self):
        setup_logging(self.config.data["log_max_mb"])

    def _set_grace(self, reason):
        self._grace_until  = time.time() + self.POST_ACTION_GRACE
        self._grace_reason = reason

    def get_adapter_info(self, adapter_name=None):
        """Donus: (is_up: bool, ip_address: str | None)"""
        if not adapter_name:
            adapter_name = self.config.data["adapter_name"]

        if not _iphlpapi:
            return True, None

        adapters = enumerate_adapters()
        if adapters is None:
            return False, None

        for d in adapters:
            if d["name"].lower() == adapter_name.lower():
                is_up = (d["oper_status"] == 1)
                ip = d["ipv4"][0] if d["ipv4"] else None
                if is_up and ip and ip != self._last_adapter_ip:
                    if self._last_adapter_ip is not None:
                        logging.info(f"Ethernet IP adresi değişti: {self._last_adapter_ip} -> {ip}")
                    self._last_adapter_ip = ip
                return is_up, ip
        return False, None

    def _run_checks(self, bind_ip=None):
        """
        Ethernet IP'sine bağlı soket ile hedefleri sırayla dener (kısa devre / short-circuit).
        İlk hedef başarılı olursa hemen döner (ortalama 2-15 ms, 0 thread oluşturma, minimum ağ yükü).
        İlk hedef başarısız olursa diğer hedeflere sırayla geçilir.
        """
        if not bind_ip:
            return False

        for ip, port in self.CHECK_TARGETS:
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                s.bind((bind_ip, 0))
                s.connect((ip, port))
                return True
            except OSError:
                continue
            finally:
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
        return False

    def is_connected(self, bind_ip=None):
        connected = self._run_checks(bind_ip=bind_ip)
        self.status = Status.ACTIVE if connected else Status.PASSIVE
        return connected

    def _raw_check(self, bind_ip=None):
        return self._run_checks(bind_ip=bind_ip)

    def _diagnose_failure(self, adapter):
        adapters = enumerate_adapters() or []
        for d in adapters:
            if d["name"].lower() == adapter.lower():
                gws = d["gateways"]
                if not gws:
                    return "Varsayılan ağ geçidi (Gateway) yok"
                gw = gws[0]
                try:
                    r = run_cmd(["ping", "-n", "1", "-w", "1000", gw], timeout=3, label="ping-gw")
                    if r.returncode == 0 and "TTL=" in r.stdout.upper():
                        return f"Gateway ({gw}) yanıt veriyor, dış internet erişimi yok"
                    return f"Gateway ({gw}) yanıt vermiyor (yerel hat kopuk)"
                except Exception:
                    return f"Gateway ({gw}) pinglenemedi"
        return "Adaptör bilgisi alınamadı"

    def has_wifi_hardware(self):
        if not _iphlpapi:
            return True
        adapters = enumerate_adapters()
        if adapters is None:
            return True
        wifi = [
            a for a in adapters
            if a["if_type"] == IF_TYPE_IEEE80211 and "wi-fi direct" not in a["description"].lower()
        ]
        return bool(wifi)

    def _query_hotspot_state(self):
        ps_script = (
            "$ErrorActionPreference = 'Stop'; "
            "$cp = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]::GetInternetConnectionProfile(); "
            "if (-not $cp) { Write-Output 'NO_PROFILE'; exit 0 }; "
            "$tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]::CreateFromConnectionProfile($cp); "
            "if (-not $tm) { Write-Output 'NO_MANAGER'; exit 0 }; "
            "Write-Output $tm.TetheringOperationalState.ToString()"
        )
        try:
            result = run_cmd(
                ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                 "-Command", ps_script],
                timeout=8, label="ps-hotspot-state",
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        return lines[-1] if lines else None

    def manage_hotspot(self, force=False):
        now = time.time()
        wifi = self.has_wifi_hardware()
        if wifi != self._wifi_hw_present:
            if wifi:
                logging.info("Kullanılabilir Wi-Fi bağdaştırıcısı algılandı. Hotspot yönetimi devrede.")
                self._hotspot_failures      = 0
                self._hotspot_backoff_until = 0.0
            else:
                logging.warning("Kullanılabilir Wi-Fi bağdaştırıcısı bulunamadı (devre dışı veya Kod 10 hatası). Hotspot atlandı.")
            self._wifi_hw_present = wifi
        if not wifi:
            return

        if now < self._hotspot_backoff_until:
            return

        # Hotspot zaten On ise seyrek kontrol et (60s), kapaliysa hizla acabilmek icin sik kontrol et (10s)
        interval = self.HOTSPOT_ON_INTERVAL if self._hotspot_last_state == "On" else self.HOTSPOT_OFF_INTERVAL
        if not force and (now - self._hotspot_last_check < interval):
            return
        if not self._hotspot_lock.acquire(blocking=False):
            return
        self._hotspot_last_check = now

        threading.Thread(target=self._hotspot_worker, daemon=True).start()

    def _hotspot_worker(self):
        try:
            state = self._query_hotspot_state()
            if state != self._hotspot_last_state:
                if state in ("On", "Off"):
                    # Sadece Off iken veya On'a dondugunde bilgi ver
                    if state == "Off" and self._hotspot_last_state == "On":
                        logging.warning("Mobil Etkin Nokta kapandı -> tekrar açılacak.")
                self._hotspot_last_state = state

            if state == "On":
                self._hotspot_failures      = 0
                self._hotspot_backoff_until = 0.0
                return

            if state != "Off":
                # InTransition vb. durumlarda bekle
                self._hotspot_failures += 1
                delay = min(self.HOTSPOT_RETRY_INTERVAL * (2 ** (self._hotspot_failures - 1)), self.HOTSPOT_MAX_BACKOFF)
                self._hotspot_backoff_until = time.time() + delay
                return

            # Off -> Başlat
            self._set_grace("hotspot açılıyor")
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
                "else { throw ('Start_failed: ' + $result.Status.ToString()) }"
            )
            result = run_cmd(
                ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                 "-Command", ps_script],
                timeout=35, label="ps-hotspot-start"
            )
            if "STARTED" in result.stdout or "ALREADY_ON" in result.stdout:
                logging.info("Mobil Etkin Nokta (Hotspot) kapalıydı -> başarıyla başlatıldı.")
                self._hotspot_failures      = 0
                self._hotspot_backoff_until = 0.0
                self._hotspot_last_state    = "On"
                self._set_grace("hotspot başlatıldı")
            else:
                self._hotspot_failures += 1
                delay = min(self.HOTSPOT_RETRY_INTERVAL * (2 ** (self._hotspot_failures - 1)), self.HOTSPOT_MAX_BACKOFF)
                self._hotspot_backoff_until = time.time() + delay
                logging.error(f"Mobil Etkin Nokta başlatılamadı ({_one_line(result.stderr or result.stdout)}). {delay}s sonra tekrar denenecek.")
        except Exception as e:
            self._hotspot_failures += 1
            self._hotspot_backoff_until = time.time() + self.HOTSPOT_RETRY_INTERVAL
            logging.error(f"Hotspot işlemi sırasında hata: {e}")
        finally:
            self._hotspot_lock.release()

    def reset_adapter_logic(self, is_manual=False):
        if not self._reset_lock.acquire(blocking=False):
            logging.info("Reset isteği atlandı: zaten bir reset işlemi yürütülüyor.")
            return

        adapter = self.config.data["adapter_name"]
        trigger = "Manuel" if is_manual else "Otomatik"
        restored = False

        try:
            if not is_manual:
                is_up, bind_ip = self.get_adapter_info(adapter)
                if not is_up:
                    logging.info(f"Reset iptal edildi: '{adapter}' kablosu takılı değil.")
                    self.status = Status.NO_CABLE
                    return

                time.sleep(1.5)
                is_up, bind_ip = self.get_adapter_info(adapter)
                if not is_up:
                    self.status = Status.NO_CABLE
                    return
                if self._raw_check(bind_ip=bind_ip):
                    logging.info("Reset iptal edildi: bağlantı yeniden kontrol sırasında kendiliğinden düzeldi.")
                    self._consecutive_failures = 0
                    self._first_failure_at     = None
                    return

            self.status = Status.RESETTING
            logging.warning(f"Bağlantı kesildi. '{adapter}' adaptörü resetleniyor ({trigger})...")

            try:
                run_cmd(["netsh", "interface", "set", "interface", adapter, "disable"], timeout=self.NETSH_TIMEOUT, label="netsh-disable")
                time.sleep(2)
                run_cmd(["netsh", "interface", "set", "interface", adapter, "enable"], timeout=self.NETSH_TIMEOUT, label="netsh-enable")
            except Exception as e:
                logging.error(f"Adaptör reset komutu başarısız oldu: {e}")
                # Güvenlik: Kartın devre dışı kalmasını önlemek için kurtarma amaçlı tekrar enable dene
                try:
                    run_cmd(["netsh", "interface", "set", "interface", adapter, "enable"], timeout=self.NETSH_TIMEOUT, label="netsh-enable-recovery")
                except Exception:
                    pass
                self.status = Status.PASSIVE
                return

            logging.info(f"Reset komutları tamamlandı. DHCP'den IP bekleniyor (en fazla {self.DHCP_WAIT}s)...")
            deadline = time.time() + self.DHCP_WAIT
            while time.time() < deadline:
                if self._stop_event.wait(2):
                    break
                is_up, bind_ip = self.get_adapter_info(adapter)
                if is_up and bind_ip and self._raw_check(bind_ip=bind_ip):
                    restored = True
                    break

            if restored:
                logging.info(f"DHCP alındı. Bağlantı tekrar kuruldu (IP: {bind_ip}).")
                self.status = Status.ACTIVE
            else:
                logging.warning(f"DHCP zaman aşımı ({self.DHCP_WAIT}s). IP alınamadı; ana döngü tekrar deneyecek.")
                self.status = Status.PASSIVE
        finally:
            try:
                if not is_manual:
                    self._reset_streak += 1
                    delay = self.RESET_BACKOFF[min(self._reset_streak, len(self.RESET_BACKOFF) - 1)]
                    self._next_reset_allowed = time.time() + delay
                    self._backoff_logged     = False
                    if delay > 0:
                        logging.warning(f"Arka arkaya otomatik reset önleme devrede: Sonraki reset en erken {delay}s sonra yapılabilecek.")
                self._consecutive_failures = 0
                self._first_failure_at     = None
                self._set_grace("reset sonrası")
            finally:
                self._reset_lock.release()

        if restored:
            self._hotspot_last_check    = 0.0
            self._hotspot_backoff_until = 0.0
            self.manage_hotspot(force=True)

    def _monitor_tick(self):
        if not self._active.is_set():
            self.status = Status.PAUSED
            self._cable_unplugged_logged = False
            self._consecutive_failures   = 0
            self._first_failure_at       = None
            return

        if self._reset_lock.locked():
            return

        adapter = self.config.data["adapter_name"]
        is_up, eth_ip = self.get_adapter_info(adapter)

        if not is_up:
            self.status = Status.NO_CABLE
            if not self._cable_unplugged_logged:
                logging.info(f"Ethernet kablosu takılı değil veya '{adapter}' devre dışı. Onarım ve hotspot duraklatıldı.")
                self._cable_unplugged_logged = True
            self._consecutive_failures = 0
            self._first_failure_at     = None
            self._healthy_since        = None
            return

        if self._cable_unplugged_logged:
            logging.info(f"Ethernet kablosu takıldı ('{adapter}'). İzleme sürdürülüyor.")
            self._cable_unplugged_logged = False

        connected = self.is_connected(bind_ip=eth_ip)
        now = time.time()

        if connected:
            if self._consecutive_failures or self._first_failure_at:
                outage = int(now - self._first_failure_at) if self._first_failure_at else 0
                logging.info(f"Bağlantı reset gerekmeden kendiliğinden düzeldi (~{outage}s kesinti sonrasında).")
            self._consecutive_failures = 0
            self._first_failure_at     = None
            if self._healthy_since is None:
                self._healthy_since = now
            if self._reset_streak and now - self._healthy_since >= self.STABLE_RESET_CLEAR:
                self._reset_streak       = 0
                self._next_reset_allowed = 0.0
            self.manage_hotspot()
            return

        # Baglanti yok
        self._healthy_since = None
        if self._first_failure_at is None:
            self._first_failure_at = now

        if now < self._grace_until:
            # Koruma suresinde log basma
            return

        self._consecutive_failures += 1
        n = self._consecutive_failures
        diag = self._diagnose_failure(adapter)
        logging.warning(f"İnternet bağlantısı kurulamadı ({n}/{self.FAILS_BEFORE_RESET}). Durum: {diag}")

        if n < self.FAILS_BEFORE_RESET:
            return

        if now < self._next_reset_allowed:
            remaining = int(self._next_reset_allowed - now)
            if not self._backoff_logged:
                logging.warning(f"Reset fırtınası koruması: {remaining}s bekleniyor. Süre bitince bağlantı hala yoksa reset atılacak.")
                self._backoff_logged = True
            return

        threading.Thread(target=self.reset_adapter_logic, daemon=True).start()

    def run(self):
        logging.info("=== İZLEME BAŞLATILDI ===")

        startup_delay = self.config.data.get("startup_delay", 15)
        logging.info(f"Ethernet bağlantısının oturması bekleniyor (en fazla {startup_delay}s)...")
        t0       = time.time()
        deadline = t0 + startup_delay
        ready    = False
        is_up, eth_ip = False, None

        while self._running.is_set() and time.time() < deadline:
            is_up, eth_ip = self.get_adapter_info()
            if is_up and eth_ip and self._raw_check(bind_ip=eth_ip):
                logging.info(f"Ethernet hazır ve internete bağlı (IP: {eth_ip}).")
                ready = True
                break
            self._stop_event.wait(1.0)

        if not ready:
            logging.warning(f"Ethernet {startup_delay}s içinde hazır olamadı; izleme döngüsüne geçiliyor.")

        while self._running.is_set():
            try:
                self._monitor_tick()
            except Exception as e:
                logging.exception(f"İzleme döngüsünde beklenmeyen hata: {e}")

            self._stop_event.wait(timeout=self.config.data["check_interval"])

        logging.info("=== İZLEME DURDURULDU ===")

# ==============================================================================
# 7. UI APP
# ==============================================================================
class ITUApp:
    def __init__(self, config=None):
        self.config     = config or Config()
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
                item("Force Reset", self._tray_force_reset),
                pystray.Menu.SEPARATOR,
                item("Exit Program", self._tray_exit),
            )
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_open(self, *_):
        if self.window:
            self.window.after(0, self._bring_to_front)

    def _tray_force_reset(self, *_):
        threading.Thread(
            target=self.worker.reset_adapter_logic, args=(True,), daemon=True
        ).start()

    def _tray_exit(self, *_):
        self.exit_app()

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
                except Exception:
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
                result = run_cmd(
                    [
                        "schtasks", "/create",
                        "/tn", task_name,
                        "/tr", action,
                        "/sc", "onlogon",
                        "/rl", "highest",
                        "/f",
                    ],
                    timeout=20, label="schtasks-create",
                )
                if result.returncode == 0:
                    logging.info("Görev Zamanlayıcı başlangıç kaydı güncellendi.")
            else:
                result = run_cmd(
                    ["schtasks", "/delete", "/tn", task_name, "/f"],
                    timeout=20, label="schtasks-delete",
                )
                if result.returncode == 0:
                    logging.info("Görev Zamanlayıcı başlangıç kaydı kaldırıldı.")
        except Exception as e:
            logging.error(f"Görev Zamanlayıcı hatası: {e}")

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
            try:
                self.window.wm_iconphoto(True, self.icon_photo)
            except Exception:
                pass

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
            command=self.handle_open_logs
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

    def handle_open_logs(self):
        try:
            os.startfile(LOG_PATH)
        except OSError as e:
            logging.error(f"Log dosyası açılamadı: {e}")

    def open_settings(self):
        win = ctk.CTkToplevel(self.window)
        win.title("Settings")
        win.geometry("320x420")
        win.attributes("-topmost", True)
        if self.icon_photo:
            try:
                win.wm_iconphoto(True, self.icon_photo)
            except Exception:
                pass

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
                            raise ValueError("Adapter Name boş bırakılamaz.")
                        new_data[key] = raw
                    else:
                        val = int(raw)
                        if val <= 0:
                            raise ValueError(f"'{key}' pozitif bir tam sayı olmalı.")
                        new_data[key] = val
                new_data["auto_start"] = auto_var.get()
                self.config.data.update(new_data)
                self.config.save()
                self.worker.setup_logging()
                logging.info(f"Ayarlar güncellendi: {new_data}")
                threading.Thread(
                    target=self.manage_task_scheduler,
                    args=(new_data["auto_start"],),
                    daemon=True
                ).start()
                win.destroy()
            except ValueError as e:
                error_lbl.configure(text=str(e))
            except OSError as e:
                error_lbl.configure(text=f"Kaydetme hatası: {e}")

        ctk.CTkButton(win, text="Save & Apply", command=save, fg_color="green").pack(pady=10)

    def hide_dashboard(self):
        if self.window:
            self.window.withdraw()

    def exit_app(self, *_):
        logging.info("Çıkış yapılıyor...")
        self.worker.running = False
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
# 8. STARTUP BANNER
# ==============================================================================
def log_startup_banner(config):
    logging.info("=" * 70)
    logging.info(f"ITU Net Assistant v{APP_VERSION} başlatıldı")
    logging.info(f"İzlenen Adaptör: '{config.data['adapter_name']}' | Kontrol Aralığı: {config.data['check_interval']}s")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    config = Config()
    setup_logging(config.data["log_max_mb"])
    log_startup_banner(config)

    if not check_single_instance():
        sys.exit()

    atexit.register(lambda: logging.info("Program kapandı."))

    app           = ITUApp(config)
    is_background = "--background" in sys.argv
    app.show_dashboard(silent=is_background)
