# ============================================================
# 第 1 周：QC Research 数据探索 notebook
# 用法：粘进 Research notebook，改一下下面的 CONFIG 就能跑
# ============================================================

# ---------- CELL 1: 配置 + 取数 ----------
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

qb = QuantBook()

# ---- CONFIG：你只需要改这里 ----
MARKET  = Market.COINBASE          # 数据来源交易所，也决定以后回测用哪家的手续费模型
TICKERS = ["BTCUSD", "ETHUSD"]
START   = datetime(2022, 1, 1)
END     = datetime(2025, 1, 1)
# --------------------------------

# 必须先订阅，才能取历史数据
symbols = {t: qb.add_crypto(t, market=MARKET).symbol for t in TICKERS}
print("已订阅:", list(symbols.keys()))

# 先拉日线跑通流程；小时线数据量大，放到最后一格再取
daily = qb.history(list(symbols.values()), START, END, Resolution.DAILY)
print("日线形状:", daily.shape)
daily.head()


# ---------- CELL 2: 数据质量检查 ----------
def quality_report(df, name, expected_step):
    """对单个币的 OHLCV 数据做质量检查。expected_step = 相邻两根 bar 的正常间隔。"""
    print(f"\n===== {name} 质量报告 =====")
    print("行数:", len(df), "| 范围:", df.index.min(), "→", df.index.max())

    # 1) 缺失值
    na = df.isna().sum()
    print("缺失值:", dict(na[na > 0]) if na.sum() else "无")

    # 2) 重复时间戳
    print("重复时间戳:", int(df.index.duplicated().sum()))

    # 3) 时间戳连续性：找出比正常间隔大的洞
    gaps = df.index.to_series().diff().dropna()
    big = gaps[gaps > expected_step]
    print(f"时间缺口 (>{expected_step}) 数量:", len(big))
    if len(big):
        print(big.head())

    # 4) 异常价格
    print("非正收盘价:", int((df["close"] <= 0).sum()))
    ret = df["close"].pct_change()
    extreme = df.loc[ret.abs() > 0.3, ["close"]].assign(ret=ret[ret.abs() > 0.3])
    print("单日涨跌 >30% 的行数:", len(extreme))
    if len(extreme):
        print(extreme)

# 执行质量检查
for t, sym in symbols.items():
    quality_report(daily.loc[sym], f"{t} 日线", expected_step=timedelta(days=1))


# ---------- CELL 3: 画图（价格 / 成交量 / 累计收益）----------
for t, sym in symbols.items():
    df = daily.loc[sym].copy()  # 复制数据，避免修改原始数据
    df["ret"] = df["close"].pct_change().fillna(0)  # 计算日收益率[收益率公式：(今天价格 - 昨天价格) / 昨天价格]

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    df["close"].plot(ax=axes[0], title=f"{t} Close")
    df["volume"].plot(ax=axes[1], title=f"{t} Volume")
    (1 + df["ret"]).cumprod().plot(ax=axes[2], title=f"{t} Cumulative Return (buy & hold)")
    plt.tight_layout()
    plt.show()


# ---------- CELL 4（可选）: 小时线 + 保存 ----------
hourly = qb.history(list(symbols.values()), START, END, Resolution.HOUR)
print("小时线形状:", hourly.shape)

# 存成 CSV（notebook 会话内的本地文件）
for t, sym in symbols.items():
    daily.loc[sym].to_csv(f"{t}_daily.csv")
    print(f"已保存 {t}_daily.csv")
# 想跨会话长期保存，用 QC 的 ObjectStore（qb.object_store），本地文件会话结束就没了