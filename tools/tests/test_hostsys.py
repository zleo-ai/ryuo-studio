"""跟操作系统打交道那一层的测试：Linux 分支在本机真跑，Windows 分支把系统接口换成假的，检查调用和换算对不对。
这台电脑没有 Windows，所以这里只能证明逻辑和命令写对了，证明不了在真 Windows 上一定能跑。"""
import json
import subprocess
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hostsys  # noqa: E402


# ---------------- 时区 ----------------

def test_gmt_offset():
    assert hostsys.gmt_offset(8 * 3600) == "GMT+08:00"
    assert hostsys.gmt_offset(-(3 * 3600 + 30 * 60)) == "GMT-03:30"
    assert hostsys.gmt_offset(0) == "GMT+00:00"


def test_tz_on_windows_is_offset(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    assert hostsys.host_tz().startswith("GMT")


def test_tz_on_linux_is_iana():
    if Path("/etc/localtime").exists():
        assert "/" in hostsys.host_tz()


# ---------------- 温度和占用 ----------------

LHM_AMD = {"Text": "Sensor", "Children": [{"Text": "ROG-PC", "Children": [
    {"Text": "AMD Ryzen 9 9950X3D", "ImageURL": "images_icon/cpu.png", "HardwareId": "/amdcpu/0", "Children": [
        {"Text": "Temperatures", "Children": [
            {"Text": "Core (Tctl/Tdie)", "Value": "71,4 °C"},
            {"Text": "CCD1 (Tdie)", "Value": "65,0 °C"}]}]},
    {"Text": "NVIDIA GeForce RTX 5090", "ImageURL": "images_icon/nvidia.png", "HardwareId": "/gpu-nvidia/0", "Children": [
        {"Text": "Temperatures", "Children": [{"Text": "GPU Core", "Value": "44.0 °C"}]}]}]}]}

LHM_INTEL = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
    {"Text": "Intel Core i9", "ImageURL": "images_icon/cpu.png", "HardwareId": "/intelcpu/0", "Children": [
        {"Text": "Temperatures", "Children": [
            {"Text": "CPU Core #1", "Value": "60.0 °C"},
            {"Text": "CPU Package", "Value": "66.0 °C"}]}]}]}]}


def test_lhm_amd_picks_tctl_and_handles_decimal_comma():
    assert hostsys.lhm_cpu_temp(LHM_AMD) == 71


def test_lhm_intel_picks_package_not_gpu():
    assert hostsys.lhm_cpu_temp(LHM_INTEL) == 66


def test_lhm_missing_gives_none():
    assert hostsys.lhm_cpu_temp(None) is None
    assert hostsys.lhm_cpu_temp({"Text": "Sensor", "Children": []}) is None


def test_cpu_temp_windows_reads_lhm(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    monkeypatch.setattr(hostsys, "lhm_data", lambda: LHM_AMD)
    assert hostsys.cpu_temp() == 71
    monkeypatch.setattr(hostsys, "lhm_data", lambda: None)  # LibreHardwareMonitor 没开：不编数
    assert hostsys.cpu_temp() is None


def test_cpu_load_windows(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    monkeypatch.setattr(hostsys, "_cpu_prev", None)
    samples = iter([(1000, 4000), (1750, 5000)])  # 1000 个单位里空闲 750 → 忙 25%
    monkeypatch.setattr(hostsys, "win_cpu_times", lambda: next(samples))
    assert hostsys.cpu_load() is None  # 第一次没有上一次
    assert hostsys.cpu_load() == 25


def test_cpu_load_linux_real():
    hostsys._cpu_prev = None
    if Path("/proc/stat").exists():
        hostsys.cpu_load()
        v = hostsys.cpu_load()
        assert v is None or 0 <= v <= 100


def test_mem_windows(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    gb = 1024 ** 3
    monkeypatch.setattr(hostsys, "win_mem", lambda: (64 * gb, 48 * gb))
    m = hostsys.mem()
    assert m["pct"] == 25 and round(m["usedGb"]) == 16 and round(m["totalGb"]) == 64
    monkeypatch.setattr(hostsys, "win_mem", lambda: None)
    assert hostsys.mem() is None


def test_mem_linux_real():
    if Path("/proc/meminfo").exists():
        m = hostsys.mem()
        assert 0 < m["pct"] < 100 and m["totalGb"] > 1


def test_win_calls_fail_soft_off_windows():
    """不在 Windows 上时 ctypes.windll 不存在，必须返回 None 而不是崩。"""
    if sys.platform != "win32":
        assert hostsys.win_cpu_times() is None
        assert hostsys.win_mem() is None


def test_gpu_parses_nvidia_smi(monkeypatch):
    monkeypatch.setattr(hostsys.shutil, "which", lambda n: "/x/nvidia-smi" if n == "nvidia-smi" else None)
    monkeypatch.setattr(hostsys.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="46, 3\n"))
    assert hostsys.gpu() == {"temp": 46, "load": 3}
    monkeypatch.setattr(hostsys.shutil, "which", lambda n: None)
    assert hostsys.gpu() is None


# ---------------- 找程序 ----------------

def test_chrome_windows_falls_back_to_edge(monkeypatch, tmp_path):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    monkeypatch.delenv("RYUO_CHROME", raising=False)
    monkeypatch.setattr(hostsys.shutil, "which", lambda n: None)
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "nothing"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    assert hostsys.chrome() == str(edge)


def test_chrome_env_override(monkeypatch):
    monkeypatch.setenv("RYUO_CHROME", "/opt/c")
    assert hostsys.chrome() == "/opt/c"


def test_no_window_flag(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    assert hostsys.no_window() == {"creationflags": 0x08000000}
    monkeypatch.setattr(hostsys, "IS_WIN", False)
    assert hostsys.no_window() == {}


def test_python_cmd_forces_utf8():
    cmd = hostsys.python_cmd(Path("x.py"), 1, "a")
    assert cmd[1:4] == ["-X", "utf8", "-I"] and cmd[-2:] == ["1", "a"]


# ---------------- 后台服务 ----------------

class Recorder:
    def __init__(self, stdout="", returncode=0):
        self.calls, self.stdout, self.returncode = [], stdout, returncode

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return types.SimpleNamespace(stdout=self.stdout, returncode=self.returncode)


def test_service_linux_uses_systemd(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", False)
    rec = Recorder(stdout="active\n")
    monkeypatch.setattr(hostsys.subprocess, "run", rec)
    hostsys.service("restart", "pusher")
    assert rec.calls[-1] == ["systemctl", "--user", "restart", "ryuo-pusher.service"]
    assert hostsys.service_active("manager") is True
    assert rec.calls[-1] == ["systemctl", "--user", "is-active", "ryuo-manager.service"]


def test_service_windows_uses_schtasks(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    rec = Recorder()
    monkeypatch.setattr(hostsys.subprocess, "run", rec)
    killed = []
    monkeypatch.setattr(hostsys, "read_pid", lambda n: 4242)
    monkeypatch.setattr(hostsys, "pid_kill", killed.append)
    hostsys.service("restart", "pusher")
    assert rec.calls == [["schtasks", "/End", "/TN", "RyuoStudio-pusher"], ["schtasks", "/Run", "/TN", "RyuoStudio-pusher"]]
    assert killed == [4242]
    rec.calls.clear()
    hostsys.service("start", "manager")
    assert rec.calls == [["schtasks", "/Run", "/TN", "RyuoStudio-manager"]]


def test_autostart_windows_needs_logon_trigger(monkeypatch):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    monkeypatch.setattr(hostsys.subprocess, "run", Recorder(stdout="<Triggers><LogonTrigger>"))
    assert hostsys.service_autostart("pusher") is True
    monkeypatch.setattr(hostsys.subprocess, "run", Recorder(stdout="<Triggers />"))  # 装时加了 -NoAuto
    assert hostsys.service_autostart("pusher") is False
    monkeypatch.setattr(hostsys.subprocess, "run", Recorder(returncode=1))  # 根本没装
    assert hostsys.service_autostart("pusher") is False


def test_service_active_windows_reads_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(hostsys, "IS_WIN", True)
    monkeypatch.setattr(hostsys, "OUT", tmp_path)
    monkeypatch.setattr(hostsys, "pid_alive", lambda pid: pid == 99)
    assert hostsys.service_active("pusher") is False  # 没有 pid 文件
    (tmp_path / "pusher.pid").write_text("99")
    assert hostsys.service_active("pusher") is True


def test_service_rejects_unknown():
    for bad in (("kill", "pusher"), ("start", "openrgb")):
        try:
            hostsys.service(*bad)
        except AssertionError:
            continue
        raise AssertionError(f"{bad} 应该被拒")


def test_pid_alive_linux_real():
    import os
    if sys.platform != "win32":
        assert hostsys.pid_alive(os.getpid()) is True
        assert hostsys.pid_alive(None) is False


# ---------------- 脚本 ----------------

def test_render_builds_file_uri(monkeypatch, tmp_path):
    import render
    rec = Recorder()
    monkeypatch.setattr(render.subprocess, "run", rec)
    monkeypatch.setattr(render.hostsys, "chrome", lambda: "chrome")
    render.render(tmp_path / "o.png", "theme=aurora&view=strip", 634, 1080)
    cmd = rec.calls[0]
    assert cmd[0] == "chrome" and "--window-size=634,1080" in cmd
    assert cmd[-1].startswith("file:///") and cmd[-1].endswith("panel.html?theme=aurora&view=strip")


def test_finish_video_two_outputs(monkeypatch):
    import finish_video
    rec = Recorder()
    monkeypatch.setattr(finish_video.subprocess, "run", rec)
    full, main = finish_video.finish("raw.mp4", "out/x.mp4")
    assert [c[-1] for c in rec.calls] == ["out/x-full.mp4", "out/x-main.mp4"]
    assert "crop=1606:1080:iw-1606:0" in " ".join(rec.calls[1])
    assert (full.name, main.name) == ("x-full.mp4", "x-main.mp4")


def test_run_service_knows_both():
    import run_service
    assert set(run_service.SCRIPTS) == set(hostsys.SERVICES)
    assert all(p.exists() for p in run_service.SCRIPTS.values())


def test_install_script_is_utf8_bom_crlf():
    """Windows PowerShell 5.1 没有 BOM 会按 GBK 读，中文提示会乱。"""
    raw = (hostsys.ROOT / "desktop" / "install_windows.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("utf-8")
    assert "$args" not in text  # PowerShell 自带变量，不能拿来当参数表
    assert "run_service.py" in text and "launch.py" in text


def test_font_source_falls_back(monkeypatch, tmp_path):
    import native_panel
    monkeypatch.setattr(native_panel, "SYSTEM_FONTS", [tmp_path])
    assert native_panel.font_source("Sora-600.ttf") is not None  # 仓库自带
    assert native_panel.font_source("NotoSerifCJK-Medium.ttc") is None
    (tmp_path / "NotoSerifCJK-Medium.ttc").write_bytes(b"x")
    assert native_panel.font_source("NotoSerifCJK-Medium.ttc") == tmp_path / "NotoSerifCJK-Medium.ttc"


def test_theme_spec_drops_missing_font(monkeypatch, tmp_path):
    import native_panel
    monkeypatch.setattr(native_panel, "SYSTEM_FONTS", [tmp_path])
    spec = native_panel.theme_spec("armillary")  # 用了中文衬线体
    assert any(v is None for v in spec["fonts"].values())
    json.dumps(spec)
