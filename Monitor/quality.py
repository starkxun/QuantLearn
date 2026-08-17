"""现货数据质检。Week 1 那套搬过来，加了两组本项目特有的检查。

Week 1 的检查（缺失 / 重复 / 缺口 / 非正价 / 极端涨跌）针对的是单个币。
这里多了：

  - **OHLC 内部一致性**：high 必须 ≥ open/close/low，low 必须 ≤ open/close。
    Week 1 没查这个，但拼接分页数据时如果串了行，最容易在这里露馅。
  - **不等长历史**：9 个币上市日差了 6 年。横截面指标（相关性、排名、离散度）
    必须从**公共起始日**算起，否则前几年是在拿 3 个币算"9 币平均相关性"。
    单币指标不受影响，各用各的全历史。

用法：
    python quality.py
    python quality.py --verbose     # 打印每个问题的具体行
"""

import argparse
from datetime import timedelta

import pandas as pd

from config import COINS, RAW_SPOT, perp

EXTREME_RET = 0.30                 # Week 1 用的阈值：单日涨跌 >30% 视为可疑
STEP = timedelta(days=1)


def load(coin: str) -> pd.DataFrame | None:
    path = RAW_SPOT / f"{perp(coin)}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path).set_index("open_time")


def check(coin: str, df: pd.DataFrame) -> dict:
    """对一个币跑全部检查，返回计数字典。"""
    ret = df["close"].pct_change()

    gaps = df.index.to_series().diff().dropna()
    big_gaps = gaps[gaps > STEP]

    # OHLC 内部一致性：任何一条不成立都说明数据错行或源头有问题
    bad_ohlc = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"]) | (df["high"] < df["close"])
        | (df["low"] > df["open"]) | (df["low"] > df["close"])
    )

    return {
        "行数": len(df),
        "起始": df.index.min().strftime("%Y-%m-%d"),
        "结束": df.index.max().strftime("%Y-%m-%d"),
        "缺失值": int(df.isna().sum().sum()),
        "重复时间戳": int(df.index.duplicated().sum()),
        "时间倒流": int((gaps <= timedelta(0)).sum()),
        "缺口": len(big_gaps),
        "非正价格": int((df[["open", "high", "low", "close"]] <= 0).sum().sum()),
        "OHLC矛盾": int(bad_ohlc.sum()),
        "零成交量": int((df["volume"] == 0).sum()),
        f"|涨跌|>{EXTREME_RET:.0%}": int((ret.abs() > EXTREME_RET).sum()),
        "_gaps": big_gaps,
        "_bad_ohlc": df[bad_ohlc],
        "_extreme": df.loc[ret.abs() > EXTREME_RET, ["close"]].assign(ret=ret),
    }


# 这些计数不为 0 就是真问题，必须查；"缺口"和"极端涨跌"是提示不是错误
FATAL = ["缺失值", "重复时间戳", "时间倒流", "非正价格", "OHLC矛盾"]


def main() -> None:
    ap = argparse.ArgumentParser(description="现货数据质检")
    ap.add_argument("--verbose", action="store_true", help="打印问题明细")
    args = ap.parse_args()

    results, frames = {}, {}
    for coin in COINS:
        df = load(coin)
        if df is None:
            print(f"⚠ {coin}: 没有数据文件，先跑 fetch_spot.py")
            continue
        frames[coin] = df
        results[coin] = check(coin, df)

    if not results:
        return

    # ---------- 汇总表 ----------
    cols = [c for c in next(iter(results.values())) if not c.startswith("_")]
    table = pd.DataFrame({c: {k: results[c][k] for k in cols} for c in results}).T
    print("========== 单币质检 ==========")
    print(table.to_string())

    # ---------- 致命问题 ----------
    print("\n========== 结论 ==========")
    bad = {c: {k: r[k] for k in FATAL if r[k]} for c, r in results.items()}
    bad = {c: v for c, v in bad.items() if v}
    if bad:
        print("✘ 发现致命问题，先修再往下做：")
        for c, v in bad.items():
            print(f"    {c}: {v}")
    else:
        print(f"✔ {len(results)} 个币的 缺失/重复/时间倒流/非正价/OHLC矛盾 全为 0")

    # 缺口和极端涨跌单独说：它们未必是错误
    gap_coins = {c: r["缺口"] for c, r in results.items() if r["缺口"]}
    if gap_coins:
        print(f"⚠ 时间缺口（可能是交易所维护，非错误）：{gap_coins}")
        if args.verbose:
            for c in gap_coins:
                print(f"\n  {c} 的缺口：")
                print(results[c]["_gaps"].to_string())
    else:
        print("✔ 无时间缺口，日线完全连续")

    ext = {c: r[f"|涨跌|>{EXTREME_RET:.0%}"] for c, r in results.items()
           if r[f"|涨跌|>{EXTREME_RET:.0%}"]}
    if ext:
        print(f"⚠ 单日涨跌 >{EXTREME_RET:.0%} 的天数（crypto 里是真行情，不是脏数据）：{ext}")
        if args.verbose:
            for c in ext:
                print(f"\n  {c}:")
                print(results[c]["_extreme"].to_string())

    # ---------- 不等长历史 ----------
    starts = {c: pd.Timestamp(results[c]["起始"]) for c in results}
    common = max(starts.values())
    latest = max(starts, key=lambda c: starts[c])

    print("\n========== 不等长历史 ==========")
    for c in sorted(starts, key=lambda x: starts[x]):
        n = len(frames[c])
        after = int((frames[c].index >= common.tz_localize("UTC")).sum())
        print(f"  {c:<5} 上市 {starts[c]:%Y-%m-%d}  全历史 {n:>5} 根 | "
              f"公共区间内 {after:>5} 根")

    print(f"\n  公共起始日 = {common:%Y-%m-%d}（受 {latest} 限制）")
    print(f"  横截面指标（相关性/排名/离散度/广度）**必须**从这天起算，")
    print(f"  否则早年是在拿 3 个币算「9 币平均相关性」。")
    print(f"  单币指标（均线/ATR/动量）不受影响，各用各的全历史。")

    span = (frames[latest].index.max() - common.tz_localize("UTC")).days
    print(f"\n  → 横截面可用跨度约 {span} 天（{span/365:.1f} 年）")
    if span < 730:
        print("    ⚠ 不足 2 年，横截面结论的样本量有限，下结论要保守")


if __name__ == "__main__":
    main()
