"""Day 7 · ER 作为 regime filter + 成本重算。

两个问题：

**问题 1：ER 能不能救均线策略？**
Week 3 证明了 20/60 双均线跑不赢 buy&hold，且成本吃掉大半收益。
本项目的核心假设是"均线亏钱主要亏在震荡市"——如果成立，
加一条"仅在 ER ≥ 阈值时持仓"应该能改善。**这是假设，不是结论，要验。**

**问题 2：Week 3 的结论对成本假设有多敏感？**
Week 1 验的是 QC/Coinbase 单边 0.85%，币安现货 taker 只有 0.10%，差 8.5 倍。
Week 3 那些"被手续费打死"的结论，换到币安成本下还成立吗？

## 纪律

- `ER_MIN = 0.30` **跑之前就写死**（docs.md Day 7 建议值），看完结果不许回头改。
  改了再看一遍，测试集就变成第二个训练集了。
- 回测逻辑逐行对齐 Week_3/code.py，三条铁律不变：
  rolling 只看过去 / `held = signal.shift(1)` / 每次换仓扣一次单边成本。

用法：
    python research/regime_filter.py
    python research/regime_filter.py --coin ETH
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import COINS, RAW_SPOT, perp                      # noqa: E402
from indicators import efficiency_ratio, ER_N                 # noqa: E402

SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
FAST, SLOW = 20, 60
ER_MIN = 0.30                      # 写死，不许调
COST_QC = 0.0085                   # Week 1 实测：QC/Coinbase 单边（0.80% 费 + 0.05% 滑点）
COST_BINANCE = 0.0010              # 币安现货 taker 0.10%
YEAR = 365


def backtest(close: pd.Series, cost: float, er_filter: bool) -> pd.DataFrame:
    """向量化双均线。逻辑与 Week_3/code.py 的 backtest() 一致。"""
    ret = close.pct_change().fillna(0.0)
    fast = close.rolling(FAST).mean()
    slow = close.rolling(SLOW).mean()

    sig = (fast > slow).astype(float)
    sig[fast.isna() | slow.isna()] = np.nan

    if er_filter:
        er = efficiency_ratio(close, ER_N)
        # 震荡市不持仓。ER 未就绪时也不持仓（保守，不猜）
        sig = sig.where(er >= ER_MIN, 0.0)
        sig[er.isna()] = np.nan

    held = sig.shift(1)                        # 今天收盘的信号，最早赚明天的钱
    turnover = held.diff().abs().fillna(0.0)
    gross = held.fillna(0.0) * ret
    # 成本按成交额比例扣，是乘法不是减法（漏掉 cost×ret 交叉项会和逐日模拟对不上）
    net = (1 + gross) * (1 - turnover * cost) - 1

    return pd.DataFrame({"ret": ret, "held": held, "turnover": turnover,
                         "gross": gross, "net": net})


def metrics(df: pd.DataFrame, cost: float) -> dict | None:
    d = df.dropna(subset=["held"])
    if len(d) < 60:
        return None
    r = d["net"]
    eq = (1 + r).cumprod()
    years = len(d) / YEAR
    mdd = ((eq - eq.cummax()) / eq.cummax()).min()
    bh = (1 + d["ret"]).cumprod()

    blk = (d["held"] != d["held"].shift()).cumsum()
    trades = sum(1 for _, g in d.groupby(blk) if g["held"].iloc[0] == 1)

    return {
        "总收益%": (eq.iloc[-1] - 1) * 100,
        "年化%": (eq.iloc[-1] ** (1 / years) - 1) * 100,
        "回撤%": mdd * 100,
        "Sharpe": r.mean() / r.std() * np.sqrt(YEAR) if r.std() > 0 else np.nan,
        "笔数": trades,
        "持仓占比%": d["held"].mean() * 100,
        "成本拖累%": d["turnover"].sum() * cost * 100,
        "BH总收益%": (bh.iloc[-1] - 1) * 100,
        "BH回撤%": ((bh - bh.cummax()) / bh.cummax()).min() * 100,
    }


def run_coin(coin: str) -> pd.DataFrame:
    close = (pd.read_parquet(RAW_SPOT / f"{perp(coin)}.parquet")
               .set_index("open_time")["close"].sort_index())
    rows = []
    for cost_name, cost in (("QC 0.85%", COST_QC), ("币安 0.10%", COST_BINANCE)):
        for filt_name, filt in (("裸 20/60", False), (f"+ER≥{ER_MIN}", True)):
            df = backtest(close, cost, filt)
            for period, sub in (("训练", df[df.index < SPLIT]),
                                ("测试", df[df.index >= SPLIT])):
                m = metrics(sub, cost)
                if m:
                    rows.append({"币": coin, "成本": cost_name, "策略": filt_name,
                                 "区间": period, **m})
    return pd.DataFrame(rows)


def show(df: pd.DataFrame, title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    print(df.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))


def main() -> None:
    ap = argparse.ArgumentParser(description="ER regime filter + 成本重算")
    ap.add_argument("--coin", default="BTC", help="主分析用哪个币，默认 BTC")
    args = ap.parse_args()

    print(f"参数：{FAST}/{SLOW} 均线，ER_MIN={ER_MIN}（跑前写死），"
          f"拆分点 {SPLIT:%Y-%m-%d}")
    print(f"成本：QC/Coinbase 单边 {COST_QC*100:.2f}% vs 币安 taker {COST_BINANCE*100:.2f}%")

    main_coin = run_coin(args.coin)
    show(main_coin, f"{args.coin} · 四种组合 × 训练/测试")

    # ---- 问题 1：ER 过滤有没有用（只看测试区间，币安成本）----
    print(f"\n{'=' * 100}\n问题 1：ER 过滤器有没有用？（9 币，测试区间，币安 0.10% 成本）\n{'=' * 100}")
    rows = []
    for c in COINS:
        d = run_coin(c)
        sel = d[(d["区间"] == "测试") & (d["成本"] == "币安 0.10%")]
        naked = sel[sel["策略"] == "裸 20/60"]
        filt = sel[sel["策略"] == f"+ER≥{ER_MIN}"]
        if naked.empty or filt.empty:
            continue
        n, f = naked.iloc[0], filt.iloc[0]
        rows.append({
            "币": c,
            "裸年化%": n["年化%"], "过滤年化%": f["年化%"], "Δ年化": f["年化%"] - n["年化%"],
            "裸Sharpe": n["Sharpe"], "过滤Sharpe": f["Sharpe"],
            "裸回撤%": n["回撤%"], "过滤回撤%": f["回撤%"],
            "裸笔数": n["笔数"], "过滤笔数": f["笔数"],
            "过滤持仓%": f["持仓占比%"],
            "BH总收益%": n["BH总收益%"],
        })
    cmp = pd.DataFrame(rows)
    print(cmp.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))
    win = int((cmp["Δ年化"] > 0).sum())
    win_s = int((cmp["过滤Sharpe"] > cmp["裸Sharpe"]).sum())
    win_d = int((cmp["过滤回撤%"] > cmp["裸回撤%"]).sum())
    print(f"\n  ER 过滤改善年化的币：{win}/{len(cmp)}  |  "
          f"Δ年化中位数 {cmp['Δ年化'].median():+.2f}pp")
    print(f"  改善 Sharpe：{win_s}/{len(cmp)}    改善回撤：{win_d}/{len(cmp)}")
    print("  ⚠ 回撤改善主要来自**仓位暴露从 ~55% 降到 ~15%**，不是择时。")
    print("    收益随仓位线性缩放，Sharpe 不变 —— 所以 Sharpe 才是暴露中性的比较。")
    print(f"  交易笔数：裸 {cmp['裸笔数'].sum()} → 过滤 {cmp['过滤笔数'].sum()}"
          f"（{cmp['过滤笔数'].sum()/max(cmp['裸笔数'].sum(),1)-1:+.0%}）")

    # ---- 问题 2：成本敏感度 ----
    print(f"\n{'=' * 100}\n问题 2：成本假设从 0.85% 换成 0.10%，结论翻不翻面？\n{'=' * 100}")
    rows = []
    for c in COINS:
        d = run_coin(c)
        for period in ("训练", "测试"):
            sel = d[(d["区间"] == period) & (d["策略"] == "裸 20/60")]
            if len(sel) < 2:
                continue
            qc = sel[sel["成本"] == "QC 0.85%"].iloc[0]
            bn = sel[sel["成本"] == "币安 0.10%"].iloc[0]
            rows.append({"币": c, "区间": period,
                         "QC成本总收益%": qc["总收益%"], "币安成本总收益%": bn["总收益%"],
                         "差值pp": bn["总收益%"] - qc["总收益%"],
                         "成本拖累 QC%": qc["成本拖累%"], "成本拖累 币安%": bn["成本拖累%"],
                         "翻面": "是" if qc["总收益%"] <= 0 < bn["总收益%"] else ""})
    cost_df = pd.DataFrame(rows)
    print(cost_df.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))
    flip = int((cost_df["翻面"] == "是").sum())
    print(f"\n  由负转正的组合：{flip}/{len(cost_df)}")
    print(f"  成本拖累中位数：QC {cost_df['成本拖累 QC%'].median():.1f}% → "
          f"币安 {cost_df['成本拖累 币安%'].median():.1f}%")


if __name__ == "__main__":
    main()
