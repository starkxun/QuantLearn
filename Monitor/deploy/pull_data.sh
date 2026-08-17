#!/bin/bash
# 从服务器把归档数据拉到本地做分析。
#
# 设计成单向只读同步：服务器是数据的唯一权威，本地只是副本。
# 用 rsync 增量传输 —— 只有当天的 parquet 在变，历史文件不会重复传。
#
# 用法：
#   ./pull_data.sh                      # 用下面的默认目标
#   ./pull_data.sh user@1.2.3.4:/opt/QuantLearn
#
# 建议在 ~/.ssh/config 里给服务器配个 Host 别名，这样这里只写别名。

set -euo pipefail

REMOTE="${1:-${QL_REMOTE:-}}"
if [ -z "$REMOTE" ]; then
    echo "用法: $0 user@host:/path/to/QuantLearn" >&2
    echo "  或先设 export QL_REMOTE=user@host:/path/to/QuantLearn" >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL="$DIR/data"

echo "从 $REMOTE 拉取归档数据 → $LOCAL"

# 先落到暂存区，**绝不直接覆盖** lsr_archive/。
# 原因：同一天本地和服务器可能各自产生过快照（例如本地测试跑过一段），
# 直接 rsync 覆盖会把其中一份悄悄抹掉。正确语义是按行合并 + 去重。
STAGE="$LOCAL/_incoming"
mkdir -p "$STAGE" "$LOCAL/lsr_archive"

rsync -az --partial --info=stats1 \
    --exclude '*.tmp' \
    --exclude 'archive.log*' \
    "$REMOTE/Monitor/data/lsr_archive/" "$STAGE/"

echo
echo "合并暂存区 → 归档："
cd "$DIR"
../.venv/bin/python - <<'PY'
import glob, os
import pandas as pd

STAGE, ARCH = "data/_incoming", "data/lsr_archive"
KEY = ["fetched_at", "symbol"]        # 一次抓取的一个币 = 一行，天然唯一

for src in sorted(glob.glob(f"{STAGE}/*.parquet")):
    name = os.path.basename(src)
    dst = os.path.join(ARCH, name)
    new = pd.read_parquet(src)

    if os.path.exists(dst):
        old = pd.read_parquet(dst)
        before = len(old)
        merged = (pd.concat([old, new], ignore_index=True)
                    .drop_duplicates(subset=KEY)
                    .sort_values(KEY)
                    .reset_index(drop=True))
        added = len(merged) - before
        note = f"{before} + {len(new)} → {len(merged)} 行（新增 {added}）"
    else:
        merged = new.sort_values(KEY).reset_index(drop=True)
        note = f"新文件 {len(merged)} 行"

    tmp = dst + ".tmp"
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, dst)              # 原子替换
    print(f"  {name:<20} {note}")

print("\n本地归档现状：")
total = 0
for f in sorted(glob.glob(f"{ARCH}/*.parquet")):
    d = pd.read_parquet(f, columns=["fetched_at"])
    n = d["fetched_at"].nunique()
    total += n
    print(f"  {os.path.basename(f):<20} {n:5d} 快照  {len(d):6d} 行")
print(f"  {'合计':<18} {total:5d} 快照")
PY

rm -rf "$LOCAL/_incoming"
