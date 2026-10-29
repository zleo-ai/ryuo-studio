#!/usr/bin/env bash
# 编出屏上程序：bash overlay/build.sh → overlay/build/panel.dex
# 需要 JDK 17、Android 33 的 android.jar 和 d8，默认在 ~/.local/share/ryuo-toolchain/
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tc="${RYUO_TOOLCHAIN:-$HOME/.local/share/ryuo-toolchain}"
jdk="$(ls -d "$tc"/jdk-17* | head -1)"
rm -rf "$here/build/classes" && mkdir -p "$here/build/classes"
"$jdk/bin/javac" -encoding UTF-8 --release 11 -nowarn -cp "$tc/android.jar" -d "$here/build/classes" "$here/src/RyuoPanel.java"
JAVA_HOME="$jdk" PATH="$jdk/bin:$PATH" "$tc/android-14/d8" --lib "$tc/android.jar" --min-api 33 --output "$here/build" "$here"/build/classes/*.class
mv "$here/build/classes.dex" "$here/build/panel.dex"
echo "$here/build/panel.dex"
