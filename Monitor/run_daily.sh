#!/bin/bash
# 日频流水线：取数 → 质检 → 指标 → 横截面 → 面板快照。
#
# 给 systemd timer 调用。设计要点：
#   1. **顺序有依赖**，前一步失败就别跑后面的——拿旧数据算出来的面板比没有更糟，
#      因为它看起来是新的。所以取数失败直接退出。
#   2. 但 Coinglass 那步**允许降级**：它自己会沿用已存历史，
#      拿不到 funding 不该让整个面板停摆（前 3 层指标不依赖它）。
#   3. 全部日志落盘。定时任务失败时没人看着屏幕。
#
# 用法：
#   ./run_daily.sh            # 完整跑一遍（约 9 分钟，主要耗在 Coinglass 限频）
#   ./run_daily.sh --quick    # 跳过 Coinglass，用于验证流水线本身（约 20 秒）

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/../.venv/bin/python"
LOG="$DIR/data/daily.log"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

cd "$DIR" || exit 1

# 日志超 20MB 轮转，跑一年不撑爆磁盘
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 20971520 ]; then
    mv -f "$LOG" "$LOG.1"
fi

# 日志去向要看是谁在跑：
#   systemd 下 StandardOutput=append: 已经把 stdout 接到 $LOG，再 tee 一次就写两遍
#   手动跑时终端和文件都要有，才用 tee
# systemd 会给服务进程设 INVOCATION_ID，拿它判断最可靠
if [ -n "${INVOCATION_ID:-}" ]; then
    log() { echo "[$(date -u +'%Y-%m-%d %H:%M:%S')] $*"; }
else
    log() { echo "[$(date -u +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
fi

step() {
    local name="$1"; shift
    local fatal="$1"; shift
    log "▶ $name"
    if "$@" >>"$LOG" 2>&1; then
        log "  ✔ $name"
        return 0
    fi
    local rc=$?
    log "  ✘ $name 失败（退出码 $rc）"
    if [ "$fatal" = "fatal" ]; then
        log "✘ 流水线中止 —— 拿旧数据算出来的面板看起来是新的，比没有更危险"
        exit "$rc"
    fi
    log "  ⚠ 降级继续（该步非致命）"
    return 0
}

log "===== 日频流水线开始 $([ $QUICK -eq 1 ] && echo '(quick)') ====="

step "现货日线"   fatal    "$PY" fetch_spot.py
step "数据质检"   nonfatal "$PY" quality.py
[ $QUICK -eq 0 ] && step "Coinglass 指标" nonfatal "$PY" fetch_coinglass.py
step "第1~2层指标" fatal   "$PY" indicators.py
step "第3层横截面" fatal   "$PY" cross_section.py
step "面板快照"   fatal    "$PY" panel.py

log "===== 完成 ====="
