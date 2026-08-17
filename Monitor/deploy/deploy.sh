#!/bin/bash
# 一键部署归档器到服务器。幂等 —— 重复跑就是一次更新。
#
# 用法：
#   ./deploy.sh <ssh目标> [远程路径]
#   ./deploy.sh vps                        # 用 ~/.ssh/config 里的别名，默认装到 ~/QuantLearn
#   ./deploy.sh root@1.2.3.4 /opt/QuantLearn
#
# 做这几件事：
#   1. rsync 代码上去（不传 data/、.venv、本地 .env）
#   2. 远程建 venv 装依赖
#   3. 生成服务器专属 .env —— 只含 key，**不含代理**（海外 VPS 直连）
#   4. 装 systemd 服务并启动
#   5. 验证：等第一个快照落盘
#
# 注意：API key 限频 6 次/分钟是**按 key 计**的，服务器和本地同时跑会互相挤兑配额。
#      部署后本地就别再跑归档了。

set -euo pipefail

ASSUME_YES=0
if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then
    ASSUME_YES=1; shift
fi

TARGET="${1:-}"
REMOTE_DIR="${2:-\$HOME/QuantLearn}"
[ -z "$TARGET" ] && { echo "用法: $0 [-y] <ssh目标> [远程路径]" >&2; exit 1; }

LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

say() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }

# ---------- 0. 连通性 + 环境探测 ----------
say "探测服务器"
$SSH "$TARGET" 'echo "  主机: $(hostname)"
  echo "  系统: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -s)"
  echo "  Python: $(python3 -V 2>&1)"
  # 查 ensurepip 而不是 venv：Ubuntu 上 `import venv` 能过但缺 ensurepip，
  # 到建 venv 那步才报错，白跑一遍
  echo "  ensurepip: $(python3 -c "import ensurepip" 2>/dev/null && echo 有 || echo "缺 → sudo apt install python3-venv")"
  echo "  用户: $(whoami)"
  echo "  时区: $(date +%Z)  UTC时间: $(date -u +%H:%M)"'

say "探测服务器到数据源的连通性（决定 .env 要不要配代理）"
$SSH "$TARGET" '
for u in https://api.coinglass.site/ https://data-api.binance.vision/api/v3/ping; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 15 "$u" 2>/dev/null || echo 000)
  printf "  %-50s %s\n" "$u" "$code"
done'

if [ "$ASSUME_YES" -eq 0 ]; then
    read -rp $'\n上面两个都是 200 吗？(y/N) ' ok
    [ "$ok" = "y" ] || {
        echo "中止。不通的话服务器上也要配代理，改远程 .env 的 http_proxy。"; exit 1; }
fi

# ---------- 1. 传代码 ----------
say "同步代码 → $TARGET:$REMOTE_DIR"
REMOTE_DIR_EXPANDED=$($SSH "$TARGET" "eval echo $REMOTE_DIR")
$SSH "$TARGET" "mkdir -p '$REMOTE_DIR_EXPANDED'"
rsync -az --info=stats1 \
    --exclude 'data/' --exclude '.venv/' --exclude '.env' \
    --exclude '__pycache__/' --exclude '.git/' \
    "$LOCAL_ROOT/Monitor" "$TARGET:$REMOTE_DIR_EXPANDED/"

# ---------- 2. venv ----------
say "建 venv 装依赖"
$SSH "$TARGET" "cd '$REMOTE_DIR_EXPANDED' && {
    [ -d .venv ] || python3 -m venv .venv || {
        echo '建 venv 失败，多半缺 python3-venv：'
        echo '  sudo apt-get update && sudo apt-get install -y python3-venv'
        exit 1
    }
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet pandas pyarrow
    echo \"  pandas \$(./.venv/bin/python -c 'import pandas;print(pandas.__version__)')\"
}"
# 服务器上不画图，不装 matplotlib

# ---------- 3. 服务器专属 .env ----------
say "写入服务器 .env（只含 key，不含代理）"
KEY=$(grep '^COINGLASS_KEY=' "$LOCAL_ROOT/.env" | cut -d= -f2-)
[ -z "$KEY" ] && { echo "本地 .env 里没找到 COINGLASS_KEY" >&2; exit 1; }
# 经 stdin 传，不走命令行参数——参数会出现在远程的 ps 输出里
printf 'COINGLASS_KEY=%s\n' "$KEY" | \
    $SSH "$TARGET" "cat > '$REMOTE_DIR_EXPANDED/.env' && chmod 600 '$REMOTE_DIR_EXPANDED/.env' && echo '  已写入，权限 600'"

# ---------- 4. 冒烟测试 ----------
say "冒烟测试：抓一次"
$SSH "$TARGET" "cd '$REMOTE_DIR_EXPANDED/Monitor' && ../.venv/bin/python archive_lsr.py --once"

# ---------- 5. systemd ----------
say "安装 systemd 服务"
$SSH "$TARGET" "
    SUDO=''; [ \"\$(id -u)\" -ne 0 ] && SUDO=sudo
    sed 's#%ROOT%#$REMOTE_DIR_EXPANDED#g' '$REMOTE_DIR_EXPANDED/Monitor/deploy/lsr-archive.service' \
      | \$SUDO tee /etc/systemd/system/lsr-archive.service > /dev/null
    \$SUDO systemctl daemon-reload
    \$SUDO systemctl enable --now lsr-archive
    sleep 3
    \$SUDO systemctl is-active lsr-archive && echo '  服务已启动'
"

say "完成。验证命令："
cat <<EOF
  ssh $TARGET 'systemctl status lsr-archive --no-pager'
  ssh $TARGET 'journalctl -u lsr-archive -n 20 --no-pager'
  ssh $TARGET 'tail -5 $REMOTE_DIR_EXPANDED/Monitor/data/archive.log'

拉数据回本地：
  ./pull_data.sh $TARGET:$REMOTE_DIR_EXPANDED
EOF
