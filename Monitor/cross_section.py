"""第 3 层：横截面指标。

**这一层才是 9 个币真正的价值所在。** 单币指标 × 9 ≈ 9 份高度重复的噪音——
这些币两两相关常年 0.6 以上，把同一套均线在 9 个币上跑一遍，得到的不是 9 个观察，
是 1 个观察抄了 9 遍。新信息在横截面里：相对强弱、排名变化、离散度、平均相关性。

两个时间口径（Day 2 定的，见 docs.md 6.2b）：

  - **需要全部 9 个币同时在场**的指标（相关性 / 离散度 / 排名）→ 从 2023-05-03 起算。
    早于这天 SUI 还没上市，算出来是"拿 3 个币冒充 9 币平均"。
  - **广度**这类可以按有效样本折算的 → 用当天所有均线已就绪的币，同时记录分母。
    SUI 的 MA200 要到 2023-11 才有值，不折算的话广度会被系统性低估。

用法：
    python cross_section.py             # 算 → market/coin 两个 parquet
    python cross_section.py --show 15   # 算完看最近 15 天的市场状态
    python cross_section.py --verify    # 验收检查
"""

import argparse
import sys

import numpy as np
import pandas as pd

from config import COINS, DATA, RAW_SPOT, perp

COMMON_START = "2023-05-03"       # 公共起始日 = SUI 上市日
CORR_N = 60                       # 滚动相关窗口
REL_CHG_N = 20                    # 相对强弱的变化窗口
RANK_CHG_N = 20
BREADTH_MA = (60, 200)

OUT_MARKET = DATA / "cross_section_market.parquet"
OUT_COIN = DATA / "cross_section_coin.parquet"


def load_wide() -> pd.DataFrame:
    """9 币收盘价宽表，**保留各币全历史**（早期是 NaN）。

    不在这里截断的原因：均线要用上市日之前……不，是上市之后但早于公共起始日的数据。
    比如 BTC 的 MA200 在 2023-05-03 当天就该是就绪的，如果先截断再算均线，
    开头 200 天会白白变成 NaN。
    """
    return pd.DataFrame({
        c: pd.read_parquet(RAW_SPOT / f"{perp(c)}.parquet")
             .set_index("open_time")["close"]
        for c in COINS
    }).sort_index()


def avg_offdiag_corr(rets: pd.DataFrame, n: int = CORR_N) -> pd.Series:
    """滚动窗口内的平均两两相关性（相关矩阵去掉对角线后取均值）。

    这个数飙升 = 所有币一起动 = 系统性风险 = 分散化当场失效。
    是本项目里最值得做告警的一个指标。
    """
    corr = rets.rolling(n).corr()
    k = rets.shape[1]
    mask = ~np.eye(k, dtype=bool)

    def _mean(block):
        v = block.values
        if v.shape != (k, k):
            return np.nan
        off = v[mask]
        # 前 n 天窗口不满，整块是 NaN。直接返回，别让 nanmean 对空切片告警
        return np.nan if np.isnan(off).all() else np.nanmean(off)

    return corr.groupby(level=0).apply(_mean)


def compute() -> tuple[pd.DataFrame, pd.DataFrame]:
    wide = load_wide()
    rets_full = wide.pct_change()

    # ---------- 广度：用全历史算均线，再按当天有效的币折算 ----------
    breadth = {}
    for n in BREADTH_MA:
        ma = wide.rolling(n).mean()
        above = (wide > ma)                      # NaN 比较得 False
        valid = ma.notna() & wide.notna()        # 均线就绪且当天有价
        breadth[f"breadth_ma{n}"] = above.where(valid).sum(axis=1)
        breadth[f"breadth_ma{n}_n"] = valid.sum(axis=1)
        breadth[f"breadth_ma{n}_pct"] = (above.where(valid).sum(axis=1)
                                         / valid.sum(axis=1).replace(0, np.nan))

    # ---------- 需要 9 个币同时在场的指标 ----------
    w = wide.loc[COMMON_START:]
    rets = w.pct_change()

    market = pd.DataFrame(index=w.index)
    market["avg_corr60"] = avg_offdiag_corr(rets, CORR_N)
    market["dispersion"] = rets.std(axis=1)          # 当日 9 币收益的横截面标准差
    market["mkt_ret"] = rets.mean(axis=1)            # 等权市场收益，用作参照
    for k, v in breadth.items():
        market[k] = v.reindex(w.index)

    # ---------- 每个币的横截面位置 ----------
    # 相对 BTC 强弱：山寨的真实 alpha 只存在于这条线里。
    # SOL 涨 40% 但 SOL/BTC 在跌，说明还不如直接拿 BTC。
    rel = w.div(w["BTC"], axis=0)
    rel_chg = rel / rel.shift(REL_CHG_N) - 1

    mom3m = w / w.shift(90) - 1
    # rank 1 = 最强。ascending=False 让收益最高的排第 1
    rank = mom3m.rank(axis=1, ascending=False)
    # 正数 = 名次前进（数字变小）
    rank_chg = rank.shift(RANK_CHG_N) - rank

    coin = pd.concat(
        [rel.stack().rename("rel_btc"),
         rel_chg.stack().rename(f"rel_btc_chg{REL_CHG_N}"),
         mom3m.stack().rename("mom_3m"),
         rank.stack().rename("mom3m_rank"),
         rank_chg.stack().rename(f"mom3m_rank_chg{RANK_CHG_N}")],
        axis=1,
    )
    coin.index.names = ["date", "coin"]
    coin = coin.reset_index()

    market = market.reset_index().rename(columns={"open_time": "date"})

    for path, df in ((OUT_MARKET, market), (OUT_COIN, coin)):
        tmp = path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)

    return market, coin


# ================= 验收 =================
def verify() -> bool:
    ok = True
    wide = load_wide()
    w = wide.loc[COMMON_START:]
    rets = w.pct_change()

    # ---- 1. 相关矩阵性质 ----
    print("[1] 相关矩阵基本性质")
    corr = rets.rolling(CORR_N).corr()
    last = corr.loc[corr.index.get_level_values(0).max()]
    sym = np.allclose(last.values, last.values.T, equal_nan=True)
    diag = np.allclose(np.diag(last.values), 1.0)
    rng = bool(((last.values >= -1.000001) & (last.values <= 1.000001)).all())
    for name, good in (("对称", sym), ("对角线=1", diag), ("值域[-1,1]", rng)):
        print(f"    {'✔' if good else '✘'} {name}")
        ok = ok and good

    # ---- 2. 平均相关性在市场大跌时飙升 ----
    print("\n[2] 平均相关性 vs 市场大跌")
    ac = avg_offdiag_corr(rets, CORR_N)
    mkt20 = w.mean(axis=1).pct_change(20)            # 等权市场 20 日收益
    both = pd.concat([ac.rename("corr"), mkt20.rename("m20")], axis=1).dropna()
    crash = both[both["m20"] <= both["m20"].quantile(0.10)]   # 跌得最狠的 10% 时段
    calm = both[both["m20"] >= both["m20"].quantile(0.50)]
    print(f"    大跌时段(20日收益最差10%) 平均相关 {crash['corr'].mean():.3f}")
    print(f"    平稳/上涨时段(中位以上)   平均相关 {calm['corr'].mean():.3f}")
    good = crash["corr"].mean() > calm["corr"].mean()
    print(f"    {'✔' if good else '✘'} 大跌时相关性更高"
          f"（差 {(crash['corr'].mean()-calm['corr'].mean()):+.3f}）")
    ok = ok and good

    # ---- 3. 广度值域 ----
    print("\n[3] 广度")
    market, _ = compute()
    for n in BREADTH_MA:
        b = market.set_index("date")[f"breadth_ma{n}"]
        nn = market.set_index("date")[f"breadth_ma{n}_n"]
        in_range = bool(((b >= 0) & (b <= nn)).all())
        print(f"    {'✔' if in_range else '✘'} MA{n}: 取值 {int(b.min())}~{int(b.max())}"
              f"，有效币数 {int(nn.min())}~{int(nn.max())}")
        ok = ok and in_range
        # 极端值应该出现在明显的单边行情
        m20 = wide.loc[COMMON_START:].mean(axis=1).pct_change(20)
        z = pd.concat([b, m20.rename("m20")], axis=1).dropna()
        z.columns = ["b", "m20"]
        allup = z[z["b"] == nn.max()]["m20"].mean()
        alldn = z[z["b"] == 0]["m20"].mean()
        print(f"        全员站上均线时 市场20日收益均值 {allup*100:+.1f}%"
              f" | 全员跌破时 {alldn*100:+.1f}%")
        good = (allup > alldn)
        print(f"        {'✔' if good else '✘'} 广度方向与行情一致")
        ok = ok and good

    # ---- 4. 记录相关性水平 → 有效自由度 ----
    print("\n[4] 这 9 个币到底算几个独立标的")
    acd = ac.dropna()
    print(f"    平均两两相关: 均值 {acd.mean():.3f} 中位 {acd.median():.3f}"
          f" 区间 {acd.min():.3f}~{acd.max():.3f}")
    # 等相关模型下的有效标的数 N_eff = N / (1 + (N-1)*rho)
    for label, rho in (("均值", acd.mean()), ("最高", acd.max())):
        n_eff = len(COINS) / (1 + (len(COINS) - 1) * rho)
        print(f"    {label}相关 ρ={rho:.3f} → 有效标的数 N_eff = {n_eff:.2f}")
    print(f"    最近一期相关 {acd.iloc[-1]:.3f}")

    print("\n" + ("✔ 全部通过" if ok else "✘ 有检查未通过"))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="第 3 层：横截面")
    ap.add_argument("--show", type=int, metavar="N", help="打印最近 N 天市场状态")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if args.verify:
        sys.exit(0 if verify() else 1)

    market, coin = compute()
    print(f"市场级 {len(market)} 行 → {OUT_MARKET.name}")
    print(f"个币级 {len(coin)} 行 → {OUT_COIN.name}")
    print(f"区间 {market['date'].min():%Y-%m-%d} → {market['date'].max():%Y-%m-%d}")

    if args.show:
        pd.set_option("display.width", 220)
        cols = ["date", "avg_corr60", "dispersion", "mkt_ret",
                "breadth_ma60", "breadth_ma60_n", "breadth_ma200", "breadth_ma200_n"]
        print(f"\n最近 {args.show} 天：")
        print(market[cols].tail(args.show).to_string(
            index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
