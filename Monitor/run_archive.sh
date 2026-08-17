#!/bin/bash
# 给 Windows 任务计划程序调用的包装脚本。
#
# 为什么要包装而不是让任务计划直接调 python：
#   1. 任务计划通过 wsl.exe 起进程时，工作目录和环境都不确定，这里显式定死
#   2. 日志要落盘 —— 定时任务失败时没人看着屏幕，不写日志就是瞎跑
#   3. 以后要加别的定时任务，改这个文件比改任务计划的 GUI 方便
#
# 代理/凭证不在这里设，统一由 .env + config.py 注入（见 config.py 底部注释）。

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/../.venv/bin/python"
LOG="$DIR/data/archive.log"

cd "$DIR" || exit 1

# 日志超过 20MB 就轮转一次，免得跑一年撑爆磁盘
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 20971520 ]; then
    mv -f "$LOG" "$LOG.1"
fi

"$PY" archive_lsr.py --once >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    echo "[$(date -u +'%Y-%m-%d %H:%M:%S')] 退出码 $rc" >> "$LOG"
fi
exit $rc
