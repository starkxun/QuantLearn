"""第 1~2 层指标：趋势 + 波动（单币，各用各的全历史）。

这一层只回答"这个币现在处在什么状态"，**不产生任何买卖判断**。
状态标签的组装在 Day 8 的 regime filter，别提前混进来。

三条铁律（和 Week 3 一脉相承）：

1. **只用过去。** 所有 rolling / expanding 天然向后看；分位数用 expanding 而不是
   对全样本排名——后者是最隐蔽的未来函数：拿 2026 年的波动率去给 2020 年排名，
   等于告诉当年的你"未来三年最高波动是多少"。
2. **不做布尔化。** 存 `close/ma20 - 1` 这种连续距离，不存"是否金叉"。
   "刚穿过 0.1%" 和 "远在上方 30%" 是完全不同的处境，布尔化会把这个信息丢光。
3. **crypto 一年 365 天**，不是股票的 252。年化用 sqrt(365)，"52 周高点"是 365 天。
   Week 3 的 code.py 也是这么算的，保持一致。

用法：
    python indicators.py              # 全部 9 币 → data/indicators.parquet
    python indicators.py --show BTC   # 算完顺便看最近几天
    python indicators.py --verify     # 跑验收检查（ER 合理性 / 无未来函数 / 对齐 Week 3）
"""

import argparse
import sys

import numpy as np
import pandas as pd

from config import COINS, DATA, RAW_SPOT, perp

# ---- 参数（全部写死，本项目不做参数优化，见 docs.md 红线第 6 条）----
MA_FAST, MA_SLOW = 20, 60
ER_N = 20                 # 效率系数窗口
DONCHIAN_N = 20
YEAR_N = 365              # crypto 全年无休，"52 周" = 365 天
ATR_N = 14
VOL_N = 30
MOM = {"1m": 30, "3m": 90, "6m": 180}
VOL_PCT_MIN = 180         # 波动率分位至少要这么多历史才给值

OUT = DATA / "indicators.parquet"


def efficiency_ratio(close: pd.Series, n: int = ER_N) -> pd.Series:
    """Kaufman 效率系数：净位移 / 总路程。

    接近 1 = 一路直着走（单边趋势）；接近 0 = 来回抽（震荡）。
    这是本项目最核心的指标——均线策略亏钱主要亏在震荡市，
    ER 直接度量"现在是不是震荡"，比 MACD 那类价格平滑变换有用得多。
    """
    direction = (close - close.shift(n)).abs()      # 净位移
    volatility = close.diff().abs().rolling(n).sum()  # 总路程
    return direction / volatility.replace(0, np.nan)


def atr(df: pd.DataFrame, n: int = ATR_N) -> pd.Series:
    """Wilder 的 ATR。真实波幅要考虑跳空，所以不是简单的 high-low。"""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder 平滑 = alpha 1/n 的 EMA。min_periods 保证前 n 天不给半成品值
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _range_position(close, low, high, n):
    """价格在过去 n 天区间里的位置，0=区间底部，1=区间顶部。"""
    lo = low.rolling(n).min()
    hi = high.rolling(n).max()
    span = (hi - lo).replace(0, np.nan)
    return (close - lo) / span


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """对单个币的 OHLCV 算全部第 1~2 层指标。df 需以 open_time 为索引。"""
    close, high, low = df["close"], df["high"], df["low"]
    ret = close.pct_change()

    ma_fast = close.rolling(MA_FAST).mean()
    ma_slow = close.rolling(MA_SLOW).mean()

    rv = ret.rolling(VOL_N).std() * np.sqrt(YEAR_N)      # 30 日已实现波动率（年化）

    out = pd.DataFrame(index=df.index)
    out["close"] = close
    out["ret"] = ret

    # ---------- 第 1 层：趋势 ----------
    # 连续距离，不是布尔金叉
    out["dist_ma20"] = close / ma_fast - 1
    out["dist_ma60"] = close / ma_slow - 1
    out["ma20_vs_ma60"] = ma_fast / ma_slow - 1
    out["er20"] = efficiency_ratio(close, ER_N)
    out["donchian20"] = _range_position(close, low, high, DONCHIAN_N)
    out["pos_1y"] = _range_position(close, low, high, YEAR_N)
    for name, n in MOM.items():
        out[f"mom_{name}"] = close.pct_change(n)

    # ---------- 第 2 层：波动与风险 ----------
    out["atr_pct"] = atr(df, ATR_N) / close          # 用比例而非绝对值，跨币才可比
    out["rv30"] = rv
    # 历史分位：只跟"到今天为止"的自己比。用 expanding 而不是全样本 rank
    out["rv30_pct"] = rv.expanding(min_periods=VOL_PCT_MIN).rank(pct=True)
    out["dd_ath"] = close / close.cummax() - 1        # cummax 是 expanding，只看过去
    out["dd_1y"] = close / close.rolling(YEAR_N).max() - 1

    return out


def build() -> pd.DataFrame:
    frames = []
    for coin in COINS:
        p = RAW_SPOT / f"{perp(coin)}.parquet"
        if not p.exists():
            print(f"  ⚠ {coin} 无现货数据，跳过（先跑 fetch_spot.py）")
            continue
        df = pd.read_parquet(p).set_index("open_time").sort_index()
        ind = compute(df)
        ind.insert(0, "coin", coin)
        frames.append(ind.reset_index().rename(columns={"open_time": "date"}))
        print(f"  {coin:<5} {len(ind):>5} 行 | 最新 {ind.index[-1]:%Y-%m-%d}")

    out = pd.concat(frames, ignore_index=True).sort_values(["coin", "date"])
    tmp = OUT.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(OUT)
    return out


# ================= 验收检查 =================
def verify() -> bool:
    ok = True
    btc = pd.read_parquet(RAW_SPOT / "BTCUSDT.parquet").set_index("open_time").sort_index()

    # ---- 1. 无未来函数：截断重算，历史值必须一模一样 ----
    print("[1] 无未来函数检查（截断重算对比）")
    full = compute(btc)
    cut = pd.Timestamp("2024-06-30", tz="UTC")
    trunc = compute(btc.loc[:cut])
    common = trunc.index
    diff_cols = []
    for c in full.columns:
        a, b = full.loc[common, c], trunc[c]
        # 两边都是 NaN 算相等；数值比较用容差
        both_nan = a.isna() & b.isna()
        neq = ~(both_nan | np.isclose(a.fillna(0), b.fillna(0), rtol=1e-9, atol=1e-12))
        if neq.any():
            diff_cols.append((c, int(neq.sum())))
    if diff_cols:
        print(f"    ✘ 这些列用到了未来数据：{diff_cols}")
        ok = False
    else:
        print(f"    ✔ {len(full.columns)} 列在 {cut:%Y-%m-%d} 之前的值完全一致，无未来函数")

    # ---- 2. ER 合理性 ----
    # 早先这里写的是"某某年某月是单边行情，ER 应该 > 0.35"，那是在测我对行情的记忆
    # 不是在测指标：2024-10→2024-12 BTC 涨了 71.8%，但 75 天里方向翻转 43 次，
    # 滚动 20 日 ER 的均值本来就该偏低。换成不依赖任何行情假设的构造验证。
    print("\n[2] ER 合理性检查")

    # 2a 构造数据：单调上涨必须恰好是 1，完美锯齿必须恰好是 0
    mono = pd.Series(np.arange(100, dtype=float))
    zig = pd.Series([100.0 + (i % 2) for i in range(100)])
    for name, got, want in (("单调上涨", efficiency_ratio(mono, ER_N).iloc[-1], 1.0),
                            ("完美锯齿", efficiency_ratio(zig, ER_N).iloc[-1], 0.0)):
        good = abs(got - want) < 1e-12
        print(f"    {'✔' if good else '✘'} 构造数据·{name}: ER={got:.6f}（应为 {want:.0f}）")
        ok = ok and good

    # 2b 真实数据：值域必须落在 [0,1]
    er = full["er20"].dropna()
    in_range = bool(((er >= 0) & (er <= 1)).all())
    print(f"    {'✔' if in_range else '✘'} 值域 [0,1]: 实际 {er.min():.3f}~{er.max():.3f}"
          f"，均值 {er.mean():.3f}")
    ok = ok and in_range

    # 2c 极值必须对应真实的单边/震荡（从数据里找，不写死日期）
    cl = btc["close"]
    def _net20(t):
        w = cl.loc[:t].tail(ER_N + 1)
        return abs(w.iloc[-1] / w.iloc[0] - 1) * 100
    hi_net = np.mean([_net20(t) for t in er.nlargest(20).index])
    lo_net = np.mean([_net20(t) for t in er.nsmallest(20).index])
    good = hi_net > lo_net * 5
    print(f"    {'✔' if good else '✘'} ER 最高的 20 天：|20日净变动| 均值 {hi_net:.1f}%"
          f" ｜ ER 最低的 20 天：{lo_net:.1f}%")
    ok = ok and good

    # ---- 3. 与 Week 3 的向量化逻辑对齐（20/60 金叉死叉时点）----
    print("\n[3] 与 Week_3/code.py 的 20/60 信号对齐")
    c = btc["close"]
    w3_signal = (c.rolling(20).mean() > c.rolling(60).mean()).astype(float)
    w3_signal[c.rolling(20).mean().isna() | c.rolling(60).mean().isna()] = np.nan
    mine = (full["ma20_vs_ma60"] > 0).astype(float)
    mine[full["ma20_vs_ma60"].isna()] = np.nan
    both = pd.concat([w3_signal, mine], axis=1).dropna()
    mismatch = int((both.iloc[:, 0] != both.iloc[:, 1]).sum())
    xover = int((both.iloc[:, 0].diff() != 0).sum()) - 1
    print(f"    对比 {len(both)} 天，信号不一致 {mismatch} 天，共 {xover} 次金叉/死叉")
    if mismatch == 0:
        print("    ✔ 完全一致（我的 ma20_vs_ma60>0 等价于 Week 3 的 fast>slow）")
    else:
        print("    ✘ 存在不一致"); ok = False

    # ---- 4. 跨币可比性：ATR% 应该拉开档次 ----
    print("\n[4] 波动差异（支撑 Day 8 的波动率平价）")
    rows = []
    for coin in COINS:
        d = pd.read_parquet(RAW_SPOT / f"{perp(coin)}.parquet").set_index("open_time")
        i = compute(d)
        rows.append((coin, i["atr_pct"].iloc[-1], i["rv30"].iloc[-1]))
    r = pd.DataFrame(rows, columns=["coin", "atr_pct", "rv30"]).set_index("coin")
    print(r.assign(**{"atr%": (r.atr_pct * 100).round(2),
                      "年化波动%": (r.rv30 * 100).round(1)})[["atr%", "年化波动%"]].to_string())
    spread = r["atr_pct"].max() / r["atr_pct"].min()
    print(f"    最高/最低 ATR% = {spread:.2f}x")

    print("\n" + ("✔ 全部通过" if ok else "✘ 有检查未通过"))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="第 1~2 层指标")
    ap.add_argument("--show", metavar="COIN", help="算完打印该币最近几天")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--verify", action="store_true", help="只跑验收检查")
    args = ap.parse_args()

    if args.verify:
        sys.exit(0 if verify() else 1)

    print("计算第 1~2 层指标：")
    out = build()
    print(f"\n→ {len(out)} 行 × {len(out.columns)} 列 写入 {OUT.name}")

    if args.show:
        coin = args.show.upper()
        cols = ["date", "close", "dist_ma20", "dist_ma60", "ma20_vs_ma60", "er20",
                "donchian20", "pos_1y", "mom_1m", "mom_3m", "atr_pct", "rv30",
                "rv30_pct", "dd_ath", "dd_1y"]
        sub = out[out.coin == coin][cols].tail(args.days)
        pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
        print(f"\n{coin} 最近 {args.days} 天：")
        print(sub.to_string(index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
