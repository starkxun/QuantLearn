"""Day 7 · 因子检验：funding 与大户多空比到底有没有预测力。

这是整个项目的分水岭。前 6 天在建管道，这里开始用 Week 3 学的方法
**检验数据有没有用**——而不是假设它有用。

## 方法论（先写死，跑之前不许改）

1. **训练/测试拆分**，`SPLIT = 2024-01-01`。测试集只看一次。
   看完发现不好就回去改阈值再看第二次，测试集立刻变成第二个训练集。

2. **分位数用 expanding，不用全样本 quantile。**
   拿 2026 年的 funding 分布去给 2021 年定"前 10% 阈值"，是最隐蔽的未来函数。

3. **正反两个方向都测。** 理论上极端正 funding = 多头拥挤 = 看空，
   但这是"理论上"。**不预设答案**，两个方向都跑，让数据说。

4. **样本量要按有效自由度折算。** Day 5 实测 9 个币的平均相关 0.687，
   N_eff = 1.38。把"9 币 × N 天"当独立样本会把显著性高估约 6.5 倍。
   再加上 h 日前瞻窗口重叠带来的序列相关，这里用：

       n_eff ≈ (相隔 > h 天的独立时间簇数) × N_eff

   为什么不是 `n_obs / h`：那样假定被选中的日期连成一片。实际上极端分位
   往往散落在几段行情里，简单除以 h 会把 357 个样本压成 2.7 个有效观测，
   t 值就没意义了。按"独立时间簇 × 有效标的数"折算更贴近真实信息量。

   这仍是粗折算不是严格推导。**宁可保守：t 算小了顶多漏掉一个因子，
   算大了会让你相信一个不存在的规律。**

5. **大户多空比分段跑。** 币安改过统计口径，一条曲线拉到底看不出断裂。

用法：
    python research/factor_tests.py            # 只看训练集
    python research/factor_tests.py --test     # 揭晓测试集（只运行一次！）
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import COINS, DATA, RAW_CG, RAW_SPOT, perp   # noqa: E402

SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
HORIZONS = (5, 10, 20)
PCT_MIN = 252          # 分位数至少要 1 年历史才给值
EXTREME = 0.10         # 前/后 10% 算极端
N_EFF = 1.38           # Day 5 实测：9 币等相关模型下的有效标的数


def load_panel() -> pd.DataFrame:
    """拼一张 (coin, date) 面板：因子值 + 未来 h 日收益。"""
    rows = []
    fund = pd.read_parquet(RAW_CG / "funding_rate.parquet")
    ls = pd.read_parquet(RAW_CG / "top_ls_ratio.parquet")

    for c in COINS:
        spot = pd.read_parquet(RAW_SPOT / f"{perp(c)}.parquet").set_index("open_time")
        close = spot["close"].sort_index()

        d = pd.DataFrame(index=close.index)
        d["close"] = close
        for h in HORIZONS:
            # 未来收益只用于**评估**，不参与任何信号计算，不构成未来函数
            d[f"fwd{h}"] = close.shift(-h) / close - 1

        f = fund[fund.coin == c].set_index("date")
        d["funding"] = f["funding_annualized"]
        d["funding_mid"] = f["funding_annualized_mid"]

        l = ls[ls.coin == c].set_index("date")
        d["ls_ratio"] = l["top_position_long_short_ratio"]

        d["coin"] = c
        rows.append(d.reset_index().rename(columns={"open_time": "date"}))

    return pd.concat(rows, ignore_index=True)


def add_percentiles(panel: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    """每个币各自算 expanding 分位。跨币混在一起排名是错的——
    BNB 的 funding 常年 0，和 SOL 放一起排会让 BNB 永远落在低分位。"""
    out = []
    for c, g in panel.groupby("coin", sort=False):
        g = g.sort_values("date").copy()
        for f in factors:
            g[f"{f}_pct"] = g[f].expanding(min_periods=PCT_MIN).rank(pct=True)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def _clusters(dates: pd.Series, h: int) -> int:
    """把选中的日期按"相隔超过 h 天"切成独立簇，返回簇数。

    同一波行情里连着 30 天都触发信号，前瞻窗口高度重叠，
    那基本只算 1~2 次独立观察，不是 30 次。"""
    d = pd.Series(sorted(dates.dropna().unique()))
    if d.empty:
        return 0
    return int((d.diff() > pd.Timedelta(days=h)).sum()) + 1


def _tstat(sample: pd.Series, dates: pd.Series, baseline: float,
           h: int) -> tuple[float, float, int]:
    """返回 (均值差, 折算后的 t, n_eff)。折算依据见模块开头第 4 条。"""
    x = sample.dropna()
    if len(x) < 30:
        return np.nan, np.nan, 0
    n_eff = max(_clusters(dates, h) * N_EFF, 2.0)
    diff = x.mean() - baseline
    se = x.std(ddof=1) / np.sqrt(n_eff)
    return diff, (diff / se if se > 0 else np.nan), int(round(n_eff))


def test_factor(df: pd.DataFrame, factor: str, label: str) -> pd.DataFrame:
    """对一个因子跑极端高/低分位 × 3 个前瞻期。"""
    pc = f"{factor}_pct"
    res = []
    for h in HORIZONS:
        fwd = f"fwd{h}"
        base = df[fwd].mean()
        for side, mask in (("高分位(前10%)", df[pc] >= 1 - EXTREME),
                           ("低分位(后10%)", df[pc] <= EXTREME)):
            sub = df.loc[mask, fwd]
            diff, t, n_eff = _tstat(sub, df.loc[mask, "date"], base, h)
            res.append({
                "因子": label, "档": side, "前瞻": f"{h}d",
                "样本": int(sub.notna().sum()), "n_eff": n_eff,
                "条件均值%": sub.mean() * 100,
                "基准均值%": base * 100,
                "超额%": diff * 100 if pd.notna(diff) else np.nan,
                "t(折算)": t,
            })
    return pd.DataFrame(res)


def verdict(t: float) -> str:
    if pd.isna(t):
        return "样本不足"
    a = abs(t)
    return "强" if a >= 2.5 else ("弱" if a >= 1.6 else "无")


def report(df: pd.DataFrame, name: str) -> None:
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
    d = df.copy()
    d["判定"] = d["t(折算)"].map(verdict)
    print(d.to_string(index=False, float_format=lambda x: f"{x:7.3f}"))


def run(panel: pd.DataFrame, period: str) -> None:
    if period == "train":
        df = panel[panel["date"] < SPLIT]
    else:
        df = panel[panel["date"] >= SPLIT]
    span = f"{df['date'].min():%Y-%m-%d} → {df['date'].max():%Y-%m-%d}"
    print(f"\n\n{'#' * 78}\n# {'训练集' if period == 'train' else '测试集'}  {span}"
          f"  ({len(df)} 行)\n{'#' * 78}")

    for factor, label in (("funding", "funding(close口径)"),
                          ("funding_mid", "funding(mid口径)"),
                          ("ls_ratio", "大户多空比")):
        report(test_factor(df, factor, label), label)

    # ---- 大户多空比分段：口径变过，一条曲线拉到底看不出断裂 ----
    print(f"\n{'=' * 78}\n大户多空比 · 分段稳定性（口径变更检查）\n{'=' * 78}")
    seg_rows = []
    for name, g in df.groupby(df["date"].dt.year):
        if len(g) < 500:
            continue
        r = test_factor(g, "ls_ratio", f"{name}")
        r = r[(r["前瞻"] == "20d") & (r["样本"] > 0)]
        if len(r):
            seg_rows.append(r)
    if seg_rows:
        seg = pd.concat(seg_rows)
        seg["判定"] = seg["t(折算)"].map(verdict)
        print(seg.to_string(index=False, float_format=lambda x: f"{x:7.3f}"))
        print("\n看点：各年的「超额%」符号是否一致。翻来翻去 = 因子不稳定，"
              "或口径变更造成了断裂。")


def main() -> None:
    ap = argparse.ArgumentParser(description="Day 7 因子检验")
    ap.add_argument("--test", action="store_true",
                    help="揭晓测试集。**只应该运行一次**")
    args = ap.parse_args()

    panel = load_panel()
    panel = add_percentiles(panel, ["funding", "funding_mid", "ls_ratio"])

    print(f"面板 {len(panel)} 行 | {panel['date'].min():%Y-%m-%d} → "
          f"{panel['date'].max():%Y-%m-%d} | 拆分点 {SPLIT:%Y-%m-%d}")
    print(f"有效自由度折算 N_eff={N_EFF}（Day 5 实测）。"
          f"t 值按「独立时间簇数 × N_eff」折算，不按原始样本数")

    run(panel, "train")
    if args.test:
        print("\n\n⚠️  测试集只能看一次。看完不许回头改阈值再看第二遍。")
        run(panel, "test")
    else:
        print("\n\n（未揭晓测试集。确认训练集结论后加 --test）")


if __name__ == "__main__":
    main()
