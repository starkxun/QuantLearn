"""数据速览工具 —— 用来肉眼检查抓下来的东西对不对。

不做任何计算和判断，只负责把 parquet 里的内容摊开给人看。
分析逻辑在 indicators.py / cross_section.py，别混进来。

用法：
    python peek.py                  # 全局概览：有哪些数据、覆盖到哪天
    python peek.py BTC              # 单币详情：现货 + 4 个指标的最近若干天
    python peek.py BTC --days 30    # 看更多天
    python peek.py --lsr            # LSR 高频归档概览
    python peek.py --lsr BTC        # 某个币的 LSR 快照序列
    python peek.py BTC --csv        # 导出 CSV，拿 Excel 看
"""

import argparse
import sys

import pandas as pd

from config import COINS, LSR_ARCHIVE, RAW_CG, RAW_SPOT, perp

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)

CG_METRICS = ["funding_rate", "open_interest", "top_ls_ratio", "taker_volume"]


def _fmt_usd(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"{x/div:,.2f}{unit}"
    return f"{x:,.0f}"


# ---------- 全局概览 ----------
def overview() -> None:
    print("=" * 78)
    print("现货日线  data/raw/spot/")
    print("=" * 78)
    rows = []
    for c in COINS:
        p = RAW_SPOT / f"{perp(c)}.parquet"
        if not p.exists():
            rows.append({"币": c, "状态": "缺失，跑 fetch_spot.py"})
            continue
        d = pd.read_parquet(p, columns=["open_time", "close", "quote_volume"])
        rows.append({
            "币": c, "根数": len(d),
            "起始": f"{d['open_time'].min():%Y-%m-%d}",
            "最新": f"{d['open_time'].max():%Y-%m-%d}",
            "最新收盘": f"{d['close'].iloc[-1]:,.4f}",
            "最新成交额": _fmt_usd(d["quote_volume"].iloc[-1]),
        })
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 78)
    print("Coinglass 指标  data/raw/coinglass/")
    print("=" * 78)
    for m in CG_METRICS:
        p = RAW_CG / f"{m}.parquet"
        if not p.exists():
            print(f"  {m:<15} 缺失，跑 fetch_coinglass.py")
            continue
        d = pd.read_parquet(p)
        cols = [c for c in d.columns if c not in ("coin", "date")]
        print(f"  {m:<15} {len(d):>6} 行 | {d['date'].min():%Y-%m-%d} → "
              f"{d['date'].max():%Y-%m-%d} | 缺失 {int(d.isna().sum().sum())}")
        print(f"  {'':<15} 列: {cols}")

    print("\n" + "=" * 78)
    print("LSR 高频归档  data/lsr_archive/")
    print("=" * 78)
    files = sorted(LSR_ARCHIVE.glob("*.parquet"))
    if not files:
        print("  (空。数据在服务器上，用 deploy/pull_data.sh 拉回来)")
        return
    total = 0
    for f in files:
        d = pd.read_parquet(f, columns=["fetched_at"])
        n = d["fetched_at"].nunique()
        total += n
        print(f"  {f.stem}  {n:>4} 快照 {len(d):>6} 行 | "
              f"{d['fetched_at'].min():%H:%M} → {d['fetched_at'].max():%H:%M} UTC")
    print(f"  合计 {total} 快照（每 5 分钟一个，满一天应为 288）")


# ---------- 单币详情 ----------
def coin_detail(coin: str, days: int, to_csv: bool) -> None:
    sym = perp(coin)

    print("=" * 78)
    print(f"{coin} 现货日线（最近 {days} 天）")
    print("=" * 78)
    p = RAW_SPOT / f"{sym}.parquet"
    if not p.exists():
        print("  缺失"); return
    spot = pd.read_parquet(p).set_index("open_time")
    view = spot[["open", "high", "low", "close", "volume", "quote_volume", "trades"]].tail(days).copy()
    view["涨跌%"] = spot["close"].pct_change().tail(days) * 100
    print(view.to_string())

    for m in CG_METRICS:
        p = RAW_CG / f"{m}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d = d[d["coin"] == coin].set_index("date").drop(columns="coin")
        if d.empty:
            continue
        print(f"\n{'=' * 78}\n{coin} · {m}（最近 {days} 天）\n{'=' * 78}")
        print(d.tail(days).to_string())

    if to_csv:
        out = RAW_SPOT.parent.parent / f"peek_{coin}.csv"
        spot.to_csv(out)
        print(f"\n已导出现货全历史 → {out}")


# ---------- LSR ----------
def lsr_view(coin: str | None, days: int) -> None:
    files = sorted(LSR_ARCHIVE.glob("*.parquet"))
    if not files:
        print("LSR 归档为空。数据在服务器，先跑：")
        print("  deploy/pull_data.sh ubuntu@43.155.206.51:/home/ubuntu/QuantLearn")
        return

    d = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if coin is None:
        print("=" * 78)
        print("LSR 最新一个快照（全部 9 币）")
        print("=" * 78)
        latest = d[d["fetched_at"] == d["fetched_at"].max()]
        cols = ["symbol", "ratio", "whale_ratio", "ov_total_traders",
                "ov_long_traders", "ov_short_traders",
                "ov_long_avg_entry", "ov_short_avg_entry", "oi_mcap_ratio"]
        print(f"时间 {d['fetched_at'].max():%Y-%m-%d %H:%M:%S} UTC\n")
        print(latest[cols].to_string(index=False))
        print(f"\n共 {d['fetched_at'].nunique()} 个快照，"
              f"{d['fetched_at'].min():%m-%d %H:%M} → {d['fetched_at'].max():%m-%d %H:%M} UTC")
        return

    sym = perp(coin)
    sub = d[d["symbol"] == sym].sort_values("fetched_at")
    if sub.empty:
        print(f"归档里没有 {sym}"); return
    print("=" * 78)
    print(f"{coin} LSR 快照序列（最近 {days} 条）")
    print("=" * 78)
    cols = ["fetched_at", "ratio", "trader_delta_30m", "whale_ratio", "whale_delta_30m",
            "ov_long_traders", "ov_short_traders", "ov_long_avg_entry", "ov_short_avg_entry"]
    print(sub[cols].tail(days).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="数据速览")
    ap.add_argument("coin", nargs="?", help=f"币种，可选 {COINS}")
    ap.add_argument("--days", type=int, default=10, help="显示多少行，默认 10")
    ap.add_argument("--lsr", action="store_true", help="看 LSR 高频归档")
    ap.add_argument("--csv", action="store_true", help="导出 CSV")
    args = ap.parse_args()

    coin = args.coin.upper() if args.coin else None
    if coin and coin not in COINS:
        sys.exit(f"未知币种 {coin}，可选：{COINS}")

    if args.lsr:
        lsr_view(coin, args.days)
    elif coin:
        coin_detail(coin, args.days, args.csv)
    else:
        overview()


if __name__ == "__main__":
    main()
