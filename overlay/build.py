#!/usr/bin/env python3
"""编出屏上程序：python3 -I overlay/build.py → overlay/build/panel.dex（两边通用；Linux 上 build.sh 也还在）。

需要 JDK 17、Android 33 的 android.jar 和 d8，默认在 ~/.local/share/ryuo-toolchain/，
也可以用 RYUO_TOOLCHAIN 指到别处。目录结构：jdk-17*/、android.jar、android-14/d8（Windows 上是 d8.bat）。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    tc = Path(os.environ.get("RYUO_TOOLCHAIN", Path.home() / ".local" / "share" / "ryuo-toolchain"))
    jdks = sorted(tc.glob("jdk-17*"))
    if not jdks:
        sys.exit(f"没找到 JDK 17：{tc}/jdk-17*")
    jdk, jar = jdks[0], tc / "android.jar"
    win = sys.platform == "win32"
    javac = jdk / "bin" / ("javac.exe" if win else "javac")
    d8 = tc / "android-14" / ("d8.bat" if win else "d8")
    classes, out = HERE / "build" / "classes", HERE / "build"
    shutil.rmtree(classes, ignore_errors=True)
    classes.mkdir(parents=True)
    subprocess.run([str(javac), "-encoding", "UTF-8", "--release", "11", "-nowarn", "-cp", str(jar), "-d", str(classes),
                    str(HERE / "src" / "RyuoPanel.java")], check=True)
    env = {**os.environ, "JAVA_HOME": str(jdk), "PATH": str(jdk / "bin") + os.pathsep + os.environ.get("PATH", "")}
    subprocess.run([str(d8), "--lib", str(jar), "--min-api", "33", "--output", str(out), *map(str, classes.glob("*.class"))],
                   check=True, env=env)
    dex = out / "panel.dex"
    (out / "classes.dex").replace(dex)
    return dex


if __name__ == "__main__":
    print(build())
