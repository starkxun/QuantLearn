"""LSR「聪明钱」信号高频归档器。

**这是整个 Monitor 里最先要跑起来的东西**，理由只有一条：
LSR 接口只返回当前快照，卖家不提供历史（最长回看 delta_4h）。
今天不存，三个月后依然是零。其他模块晚一天没有任何损失，这个有。

设计要点：
  - 一次请求拿全 9 个币（/api/lsr/v2/signals 支持 symbols 批量），所以
    一个快照只消耗 1 次配额，5 分钟一次远在 6 次/分钟的限额内。
  - 一天一个 parquet，**只追加不覆盖**。原子写（临时文件 + rename），
    进程被 kill 也不会写出半个文件。
  - 只存字段白名单（见 config.LSR_FIELDS），不存全部 75 个。
  - null 原样保留成 NaN，**绝不 fillna(0)** —— 手册明确说 null 不是 0。

用法：
    python archive_lsr.py            # 常驻循环，5 分钟一次
    python archive_lsr.py --once     # 抓一次就退出（给 cron / 任务计划用）
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from config import (ARCHIVE_INTERVAL, BASE_CG, BUCKET_LSR, COINS, LSR_ARCHIVE,
                    LSR_FIELDS)
from http_util import ApiError, check_lsr, get_json

SIGNALS_PATH = "/api/lsr/v2/signals"


def fetch_snapshot() -> pd.DataFrame:
    """拉一次 9 币信号，拍平成 DataFrame。一次请求，1 次配额。"""
    fetched_at = datetime.now(timezone.utc)

    payload = check_lsr(
        get_json(BASE_CG, SIGNALS_PATH, BUCKET_LSR,
                 params={"symbols": ",".join(COINS), "mode": "trader"}),
        "lsr/v2/signals",
    )

    rows = []
    for item in payload.get("data", []):
        # 白名单取字段；接口没给的留 None，让它落成 NaN 而不是 0
        row = {f: item.get(f) for f in LSR_FIELDS}
        row["fetched_at"] = fetched_at              # 我们看到它的时刻
        row["source_updated_at"] = payload.get("updated_at")   # 源数据自己的时间戳
        row["cache_age_seconds"] = payload.get("cache_age_seconds")
        rows.append(row)

    if not rows:
        raise ApiError("signals 返回空 data")

    df = pd.DataFrame(rows)
    # 列顺序固定，方便以后 concat 和肉眼看
    return df[["fetched_at", "source_updated_at", "cache_age_seconds"] + LSR_FIELDS]


def append_snapshot(df: pd.DataFrame) -> tuple[str, int]:
    """按 UTC 日期落盘。读旧文件 → 拼接 → 原子替换。"""
    day = df["fetched_at"].iloc[0].strftime("%Y-%m-%d")
    path = LSR_ARCHIVE / f"{day}.parquet"

    if path.exists():
        df = pd.concat([pd.read_parquet(path), df], ignore_index=True)

    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)                          # 原子，崩溃不会留半个文件
    return day, len(df)


def one_shot(prev_source_ts: float | None = None) -> float | None:
    """抓一次并落盘，返回本次的源数据时间戳（用于检测源是否卡住）。"""
    try:
        df = fetch_snapshot()
    except ApiError as e:
        print(f"[{_now()}] 抓取失败：{e}", file=sys.stderr)
        return prev_source_ts                       # 失败不改变状态，下一轮继续

    day, total = append_snapshot(df)
    src = df["source_updated_at"].iloc[0]

    btc = df[df["symbol"].str.startswith("BTC")]
    ratio = btc["ratio"].iloc[0] if len(btc) else float("nan")
    stale = " ⚠ 源数据未更新" if prev_source_ts is not None and src == prev_source_ts else ""

    # flush 是必须的：常驻进程重定向到文件时 stdout 会缓冲，
    # 不 flush 就看不到实时日志，出问题时完全瞎
    print(f"[{_now()}] {len(df)} 币 → {day}.parquet（累计 {total} 行）"
          f" | BTC ratio={ratio:.3f} | 源龄 {df['cache_age_seconds'].iloc[0]:.0f}s{stale}",
          flush=True)
    return src


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser(description="LSR 信号归档")
    ap.add_argument("--once", action="store_true", help="抓一次就退出（cron 用）")
    ap.add_argument("--interval", type=int, default=ARCHIVE_INTERVAL,
                    help=f"循环间隔秒数，默认 {ARCHIVE_INTERVAL}")
    args = ap.parse_args()

    if args.once:
        one_shot()
        return

    print(f"归档启动：每 {args.interval}s 一次，写入 {LSR_ARCHIVE}")
    print("Ctrl-C 停止。\n")

    started = time.monotonic()
    prev = None
    n = 0
    try:
        while True:
            prev = one_shot(prev)
            n += 1
            # 用绝对时刻排下一轮，避免每轮的抓取耗时累积成漂移
            target = started + n * args.interval
            time.sleep(max(0.0, target - time.monotonic()))
    except KeyboardInterrupt:
        print(f"\n已停止，本次共 {n} 个快照。")


if __name__ == "__main__":
    main()
