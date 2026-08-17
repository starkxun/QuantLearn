"""现货日线抓取（data-api.binance.vision，无需 key）。

三条设计原则：

1. **只存已收盘的 K 线。** 当天那根还在变，存进来就违反了 raw 目录"只追加、
   永不修改"的约定，而且用它算出来的指标每小时都不一样，没法复现。
   判据：closeTime < 现在。

2. **增量。** 有旧文件就从最后一天的下一天接着拉，没有就从头。
   全量也就 3000 多根、4 次请求，但增量能让每天的定时任务只发 1 次请求。

3. **各币用各自的全历史。** 9 个币上市日差 6 年（BTC 2017-08，SUI 2023-05），
   在这里对齐是错的——单币指标不该因为 SUI 上市晚就砍掉 BTC 前 6 年数据。
   对齐是横截面计算那一步的事，见 quality.py 的公共起始日。

用法：
    python fetch_spot.py            # 增量更新全部 9 个币
    python fetch_spot.py --full     # 忽略旧文件，从头重拉
    python fetch_spot.py BTC ETH    # 只更新指定的币
"""

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from config import BASE_SPOT, BUCKET_SPOT, COINS, RAW_SPOT, perp
from http_util import ApiError, get_json

KLINES = "/api/v3/klines"
LIMIT = 1000                       # 币安单次上限
INTERVAL = "1d"

# klines 返回的是数组的数组，官方字段顺序
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
           "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "_ignore"]
NUMERIC = ["open", "high", "low", "close", "volume", "quote_volume",
           "taker_buy_base", "taker_buy_quote"]


def fetch_klines(symbol: str, start_ms: int) -> pd.DataFrame:
    """从 start_ms 起翻页拉到最新，返回已收盘的日线。"""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows, cursor = [], start_ms

    while True:
        batch = get_json(BASE_SPOT, KLINES, BUCKET_SPOT, auth=False,
                         params={"symbol": symbol, "interval": INTERVAL,
                                 "startTime": cursor, "limit": LIMIT})
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < LIMIT:
            break
        # 下一页从最后一根的下一毫秒开始，避免重复取到同一根
        cursor = batch[-1][0] + 1

    if not rows:
        return pd.DataFrame(columns=COLUMNS[:-1])

    df = pd.DataFrame(rows, columns=COLUMNS).drop(columns="_ignore")
    # 当天那根还没收盘，扔掉 —— 见模块开头第 1 条
    df = df[df["close_time"] < now_ms]

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df[NUMERIC] = df[NUMERIC].astype(float)
    df["trades"] = df["trades"].astype("int64")

    return (df.drop_duplicates(subset="open_time")
              .sort_values("open_time")
              .reset_index(drop=True))


def update(coin: str, full: bool = False) -> tuple[int, int]:
    """更新一个币，返回 (新增行数, 总行数)。"""
    symbol = perp(coin)
    path = RAW_SPOT / f"{symbol}.parquet"

    old = pd.read_parquet(path) if (path.exists() and not full) else None
    if old is not None and len(old):
        # 从最后一根的下一毫秒接着拉
        start_ms = int(old["open_time"].max().timestamp() * 1000) + 1
    else:
        start_ms = 0               # 0 = 从该交易对上市第一根开始

    new = fetch_klines(symbol, start_ms)

    if old is not None and len(old):
        merged = (pd.concat([old, new], ignore_index=True)
                    .drop_duplicates(subset="open_time")
                    .sort_values("open_time")
                    .reset_index(drop=True))
        added = len(merged) - len(old)
    else:
        merged, added = new, len(new)

    if len(merged):
        tmp = path.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, index=False)
        tmp.replace(path)          # 原子替换
    return added, len(merged)


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取现货日线")
    ap.add_argument("coins", nargs="*", default=None, help="只更新这些币，默认全部")
    ap.add_argument("--full", action="store_true", help="忽略旧文件从头重拉")
    args = ap.parse_args()

    targets = [c.upper() for c in args.coins] if args.coins else COINS
    unknown = set(targets) - set(COINS)
    if unknown:
        sys.exit(f"未知币种 {unknown}，可选：{COINS}")

    print(f"{'币':<6} {'新增':>6} {'总计':>7}  起始 → 结束")
    print("-" * 56)
    failed = []
    for coin in targets:
        try:
            added, total = update(coin, args.full)
        except ApiError as e:
            print(f"{coin:<6} 失败：{e}")
            failed.append(coin)
            continue
        df = pd.read_parquet(RAW_SPOT / f"{perp(coin)}.parquet", columns=["open_time"])
        print(f"{coin:<6} {added:>6} {total:>7}  "
              f"{df['open_time'].min():%Y-%m-%d} → {df['open_time'].max():%Y-%m-%d}")

    if failed:
        sys.exit(f"\n{len(failed)} 个币失败：{failed}")


if __name__ == "__main__":
    main()
