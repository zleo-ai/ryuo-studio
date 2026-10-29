"""电脑这头跟操作系统打交道的地方都在这里：温度和占用、时区、找 Chrome 和 adb、开关后台服务。

Linux（Ubuntu）是真机验过的；Windows 那一半按官方接口写，单元测试覆盖，还没在真 Windows 上跑过。
读不到的值一律返回 None，屏上显示破折号，不编数。
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

IS_WIN = sys.platform == "win32"
ROOT = Path(__file__).resolve().parent.parent
OUT = Path.home() / ".local" / "share" / "ryuo-studio"
# Windows 上没有现成的温度接口，读 LibreHardwareMonitor 自带的网页服务（选项里打开 Remote Web Server，默认端口 8085）
LHM_URL = os.environ.get("RYUO_LHM_URL", "http://127.0.0.1:8085/data.json")


# ---------------- 时区 ----------------

def host_tz():
    """IANA 时区名（如 Asia/Shanghai）；拿不到就给「GMT+08:00」这种偏移写法，屏上的 Java 两种都认。"""
    if not IS_WIN:
        try:
            return str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1]
        except (OSError, IndexError):
            pass
    return gmt_offset(time.localtime().tm_gmtoff)


def gmt_offset(seconds):
    sign = "+" if seconds >= 0 else "-"
    m = abs(int(seconds)) // 60
    return f"GMT{sign}{m // 60:02d}:{m % 60:02d}"


# ---------------- 温度和占用 ----------------

def cpu_temp():
    if IS_WIN:
        return lhm_cpu_temp(lhm_data())
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (hw / "name").read_text().strip() in ("k10temp", "coretemp", "zenpower"):
                return round(int((hw / "temp1_input").read_text()) / 1000)
        except OSError:
            continue
    return None


def lhm_data():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址，不走代理
        with opener.open(LHM_URL, timeout=2) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def lhm_cpu_temp(tree):
    """在 LibreHardwareMonitor 的传感器树里找 CPU 温度：AMD 认 Tctl/Tdie，Intel 认 Package。"""
    if not tree:
        return None
    best = {}

    def walk(node, in_cpu):
        in_cpu = in_cpu or "/cpu" in str(node.get("HardwareId", "")) or "cpu" in str(node.get("ImageURL", ""))
        text, value = str(node.get("Text", "")), str(node.get("Value", ""))
        if in_cpu and "°C" in value:
            for rank, key in enumerate(("Tctl/Tdie", "Package", "Tdie", "Core (Tctl")):
                if key in text:
                    try:
                        best.setdefault(rank, float(value.split("°")[0].replace(",", ".").strip()))
                    except ValueError:
                        pass
        for child in node.get("Children", []):
            walk(child, in_cpu)

    walk(tree, False)
    return round(best[min(best)]) if best else None


_cpu_prev = None


def cpu_load():
    """两次采样之间 CPU 忙的比例；第一次没有上一次，返回 None。"""
    global _cpu_prev
    times = win_cpu_times() if IS_WIN else linux_cpu_times()
    if times is None:
        return None
    prev, _cpu_prev = _cpu_prev, times
    if not prev or times[1] == prev[1]:
        return None
    return round(100 * (1 - (times[0] - prev[0]) / (times[1] - prev[1])))


def linux_cpu_times():
    try:
        v = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
    except (OSError, ValueError):
        return None
    return v[3] + v[4], sum(v)


def win_cpu_times():
    """GetSystemTimes：内核时间里已经含空闲时间，所以总时间 = 内核 + 用户。"""
    ft = [ctypes.c_ulonglong() for _ in range(3)]
    try:
        if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(x) for x in ft)):
            return None
    except (AttributeError, OSError):
        return None
    idle, kernel, user = (x.value for x in ft)
    return idle, kernel + user


def mem():
    if IS_WIN:
        got = win_mem()
        if got is None:
            return None
        total, avail = got
    else:
        try:
            kv = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()}
            total, avail = kv["MemTotal"], kv["MemAvailable"]
        except (OSError, ValueError, IndexError, KeyError):
            return None
    gb = 1024 ** 3
    return {"pct": round(100 * (total - avail) / total), "usedGb": (total - avail) / gb, "totalGb": total / gb}


def win_mem():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MemoryStatus()
    st.dwLength = ctypes.sizeof(MemoryStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
    except (AttributeError, OSError):
        return None
    return st.ullTotalPhys, st.ullAvailPhys


def gpu():
    """N 卡温度和占用；nvidia-smi 两边都有。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, **no_window()).stdout
        temp, load = (int(x) for x in out.splitlines()[0].split(","))
        return {"temp": temp, "load": load}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


# ---------------- 找程序 ----------------

def no_window():
    """Windows 上从后台服务调命令行程序时别闪出黑窗口。"""
    return {"creationflags": 0x08000000} if IS_WIN else {}  # CREATE_NO_WINDOW


def adb():
    return os.environ.get("RYUO_ADB") or shutil.which("adb") or "adb"


def chrome():
    """无头出图和应用窗口用的浏览器：Chrome 优先，Windows 上没有就用自带的 Edge（同一个内核，参数一样）。"""
    env = os.environ.get("RYUO_CHROME")
    if env:
        return env
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        if found := shutil.which(name):
            return found
    if IS_WIN:
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            for rel in (("Google", "Chrome", "Application", "chrome.exe"), ("Microsoft", "Edge", "Application", "msedge.exe")):
                if base and (p := Path(base, *rel)).exists():
                    return str(p)
    return "google-chrome"


def python_cmd(script, *args):
    """用当前这个 Python 跑仓库里的脚本；-I 隔离模式，脚本自己把要用的目录加进 sys.path；
    -X utf8 让 Windows 上读写文件也按 UTF-8（不然默认是 GBK，中文和图标文件会读坏）。"""
    return [sys.executable, "-X", "utf8", "-I", str(script), *map(str, args)]


# ---------------- 后台服务（推送、管理器） ----------------
# Linux：systemd 用户服务 ryuo-<名字>.service（desktop/ 里）。
# Windows：计划任务 RyuoStudio-<名字>，登录时启动 tools/run_service.py，它把 pid 写在 OUT/<名字>.pid。

SERVICES = ("pusher", "manager")


def service(action, name):
    """action：start / stop / restart。"""
    assert action in ("start", "stop", "restart") and name in SERVICES
    if not IS_WIN:
        subprocess.run(["systemctl", "--user", action, f"ryuo-{name}.service"], timeout=30)
        return
    task = f"RyuoStudio-{name}"
    if action in ("stop", "restart"):
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True, timeout=30, **no_window())
        pid_kill(read_pid(name))  # /End 只停任务本身，保险起见再按 pid 停一次
    if action in ("start", "restart"):
        subprocess.run(["schtasks", "/Run", "/TN", task], capture_output=True, timeout=30, **no_window())


def service_active(name):
    if not IS_WIN:
        r = subprocess.run(["systemctl", "--user", "is-active", f"ryuo-{name}.service"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    return pid_alive(read_pid(name))


def service_autostart(name):
    if not IS_WIN:
        r = subprocess.run(["systemctl", "--user", "is-enabled", f"ryuo-{name}.service"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "enabled"
    # 装的时候加了 -NoAuto 的任务没有登录触发器，只在点图标时启动
    r = subprocess.run(["schtasks", "/Query", "/TN", f"RyuoStudio-{name}", "/XML"], capture_output=True, text=True,
                       timeout=10, errors="replace", **no_window())
    return r.returncode == 0 and "<LogonTrigger" in r.stdout


def read_pid(name):
    try:
        return int((OUT / f"{name}.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    if not pid:
        return False
    if not IS_WIN:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def pid_kill(pid):
    if not pid_alive(pid):
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30, **no_window())
    else:
        os.kill(pid, 15)
