# 量化学习 · 第 1 周验收：QC 环境、数据探索与成本模型验证

> 目标：在 QuantConnect 云端跑通「取数 → 质检 → 画图 → 验证成本模型」全流程，
> 理解引擎底层在做什么，而不是盲跑回测。
> 平台：QuantConnect 云端 IDE（LEAN Engine v2.5）。全程无需本地环境。

---

## 0. 本周结论（TL;DR）

- ✅ 环境：QC 云端已配好 Python / pandas / matplotlib / 数据，**不用在本地搭**。
- ✅ 数据：BTCUSD、ETHUSD 各约 1096 根日线（3 年），缺失 / 重复 / 缺口 / 异常价格全为 0。
- ✅ 成本模型：手续费**真实且偏保守**（引擎 0.80% vs Coinbase 真实 taker ≈ 0.60%）。
- ⚠️ 关键发现：**默认不算滑点**（`NullSlippageModel`，滑点 = 0）。已手动补上 0.05%。
- 📌 记住的数：**一个来回真实成本 ≈ 1.7%**，一笔交易涨不过这个数就是亏。

---

## 1. 环境：云端已经帮你搭好了

QC 云端 IDE 里已经装好了 Python、pandas、numpy、matplotlib 和数据，**不需要本地安装任何东西**。

一个 Project 里有两个地方，别搞混：

| 文件 | 用途 | 本周用哪个 |
|---|---|---|
| `research.ipynb`（Research） | Jupyter notebook，自由探索数据、画图 | ✅ 第 1 步 3-4 用它 |
| `main.py`（Algorithm） | 写 `initialize` / `on_data`，回测和实盘 | ✅ 成本验证 + 第 2 周用它 |

**关于本地：** 本地跑 LEAN 需要付费档 + Docker + 付费下载数据，不是免费捷径；纯免费的本地路线（ccxt 自搭）等于重造 QC 已经白送的轮子。**入门阶段留在云端最省力。**

**关于账户验证：** 免费档要求绑一张信用卡做防滥用验证（类似 AWS / GCP），**免费档不会扣费**。付费的 Researcher 档（约 $60/月）才是真升级，学习阶段不需要。免费档每天约 100 次回测，够用。

---

## 2. 拉数据（Research notebook）

```python
from datetime import datetime, timedelta
import pandas as pd, numpy as np
import matplotlib.pyplot as plt

qb = QuantBook()

# ---- CONFIG：只改这里 ----
MARKET  = Market.COINBASE          # 数据来源，也决定回测的手续费模型
TICKERS = ["BTCUSD", "ETHUSD"]
START   = datetime(2022, 1, 1)
END     = datetime(2025, 1, 1)
# --------------------------

# 必须先订阅，才能取历史数据
symbols = {t: qb.add_crypto(t, market=MARKET).symbol for t in TICKERS}

daily = qb.history(list(symbols.values()), START, END, Resolution.DAILY)
print("日线形状:", daily.shape)
daily.head()
```

**要点：**
- QC 现在的 Python API 用**下划线小写**（`add_crypto`、`history`），旧教程里的 `AddCrypto` 是 C# 写法，别照抄。
- `history` 传 list 返回**双层索引** DataFrame（symbol, time），取单个币用 `daily.loc[sym]`。
- 拉不到数据时先怀疑：market 选错、没先 `add_crypto`、日期早于数据起点、或套餐权限。

---

## 3. 数据质量检查（本周核心动作之一）

```python
def quality_report(df, name, expected_step):
    print(f"\n===== {name} 质量报告 =====")
    print("行数:", len(df), "| 范围:", df.index.min(), "→", df.index.max())

    na = df.isna().sum()
    print("缺失值:", dict(na[na > 0]) if na.sum() else "无")
    print("重复时间戳:", int(df.index.duplicated().sum()))

    gaps = df.index.to_series().diff().dropna()
    big = gaps[gaps > expected_step]
    print(f"时间缺口(>{expected_step}) 数量:", len(big))
    if len(big): print(big.head())

    print("非正收盘价:", int((df["close"] <= 0).sum()))
    ret = df["close"].pct_change()
    extreme = df.loc[ret.abs() > 0.3, ["close"]].assign(ret=ret[ret.abs() > 0.3])
    print("单日涨跌 >30% 的行数:", len(extreme))
    if len(extreme): print(extreme)

for t, sym in symbols.items():
    quality_report(daily.loc[sym], f"{t} 日线", expected_step=timedelta(days=1))
```

**我的结果：** 两个币各 1096 行，缺失 / 重复 / 缺口 / 非正价格 / 极端涨跌 **全为 0**。数据干净，过关。

> crypto 是 7×24 交易，日线不该有周末缺口；若 `gaps` 出现多天间隔就是数据洞，要记录。
> 单日涨跌 >30% 的先别急着删——可能是真实极端行情，逐个看一眼再定。

---

## 4. 画图

```python
# 注意：QC 的 matplotlib 默认无中文字体，标题用英文避免方框
for t, sym in symbols.items():
    df = daily.loc[sym].copy()
    df["ret"] = df["close"].pct_change().fillna(0)

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    df["close"].plot(ax=axes[0], title=f"{t} Close")
    df["volume"].plot(ax=axes[1], title=f"{t} Volume")
    (1 + df["ret"]).cumprod().plot(ax=axes[2], title=f"{t} Cumulative Return (buy & hold)")
    plt.tight_layout(); plt.show()
```

第三张「累计收益」就是「一直拿住」的净值曲线，以后每个策略都要跟它比。

---

## 5. 验证成本模型（本周最重要的一课）

**成本不在 Research 里，在回测（Algorithm）里。** 下单时由**券商模型**施加两块成本：
- **手续费模型（fee model）** — 每笔扣多少手续费
- **滑点模型（slippage model）** — 成交价比看到的价格差多少 ← 最容易被「默默算成 0」

### 5.1 目标交易所真实费率（2026 当前）

| 交易所 | maker | taker（低量账户） |
|---|---|---|
| Coinbase Advanced | 0.40% | **约 0.60%** |
| Binance 现货 | 0.10% | 0.10% |

选谁直接决定策略能不能活——差 5～6 倍。

### 5.2 用一个「只下 1 单」的回测把成本逼出来

```python
from AlgorithmImports import *

class CostModelCheck(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2024, 1, 1)
        self.set_end_date(2024, 1, 10)
        self.set_cash(100000)

        # 券商模型决定手续费和滑点，要和目标交易所对齐
        self.set_brokerage_model(BrokerageName.COINBASE, AccountType.CASH)
        self.btc = self.add_crypto("BTCUSD", Resolution.DAILY, Market.COINBASE).symbol
        self.done = False   # q - 这里是干什么的？

    def on_data(self, data):
        if not self.done and self.btc in data.bars:
            sec = self.securities[self.btc]
            self.log(f"手续费模型: {type(sec.fee_model).__name__}")
            self.log(f"滑点模型:   {type(sec.slippage_model).__name__}")
            self.reference_price = data.bars[self.btc].close
            self.log(f"下单参考价: {self.reference_price}")
            self.market_order(self.btc, 1)     # 市价买 1 BTC（taker）
            self.done = True

    def on_order_event(self, oe):
        if oe.status == OrderStatus.FILLED:
            fill = oe.fill_price; qty = abs(oe.fill_quantity)
            value = fill * qty
            fee = oe.order_fee.value.amount
            self.log(f"成交价: {fill}  成交额: {value:.2f}")
            self.log(f"手续费: {fee} {oe.order_fee.value.currency}")
            self.log(f"等效费率: {fee / value * 100:.4f}%")
            slip = (fill - self.reference_price) / self.reference_price * 100
            self.log(f"滑点: {slip:.4f}%（≈0 就说明引擎没算滑点）")
```

### 5.3 我的 Logs 实际输出

```
手续费模型: CoinbaseFeeModel
滑点模型:   NullSlippageModel
下单参考价: 44220.77
成交价: 44220.78  成交额: 44220.78
手续费: 353.76624 USD
等效费率: 0.8000%
滑点: 0.0000%（≈0 就说明引擎没算滑点）
```

**读法：**
- 手续费模型是 `CoinbaseFeeModel`，等效 **0.80%**，比真实 taker 0.60% 还高 → 偏保守，**没漏算** ✅
- 滑点模型是 `NullSlippageModel`，滑点 **0.0000%** → **引擎默认不算滑点**，成交价几乎等于参考价 ⚠️

> 教训：如果直接信回测，就等于在「零滑点」的理想世界里做策略，实盘一定更差。

---

## 6. 补上滑点（修正）

在 `initialize` 里 `add_crypto` 那行**后面**加：

```python
self.securities[self.btc].set_slippage_model(ConstantSlippageModel(0.0005))  # 0.05%
```

重跑后 Logs 里「滑点」应从 0 变成约 0.05%，成交价会略偏离参考价——说明你已能**手动控制回测的成本假设**。

**一个来回的真实成本（务必记住）：**

```
买 0.8% + 卖 0.8% = 1.6% 手续费
+ 买卖滑点各 0.05%   = 0.1%
------------------------------
一个来回 ≈ 1.7%
```

→ 一笔交易价格涨不过 **1.7%** 就是亏。频繁进出的策略会被成本吃垮。

---

## 7. 学到的坑（反面教材）

那个「只下 1 单」的回测，结果显示 Sharpe 4.47、Sortino 7.62、年化 80.8%——**全是假象**，要会识别：

- **好得离谱的回测先怀疑，别兴奋。** 那 80% 只是运气好，10 天里 BTC 刚好涨了 1.77%，年化后放大成 80%。
- **Win Rate 0% / Total Orders 1**：只买没卖，没有完整交易，无法统计胜负，不是真的没赢。
- **必须跑赢 buy & hold 基准。** Net Profit 1.767% 就是「拿住 BTC 10 天」的收益，真策略要能超过这个「啥也不做」的基准才有价值。
- **参数越多越危险。** QC 的 Research Guide 会提示「Possible Overfitting」，参数一多就要警惕过拟合。

---

## 8. 本周验收清单

- [x] 不看教程能独立新建 notebook、拉 BTC/ETH 数据
- [x] 完成数据质量检查（缺失 / 重复 / 缺口 / 异常）
- [x] 画出价格 / 成交量 / 累计收益三张图
- [x] 说清楚目标交易所 taker 费率（Coinbase ≈ 0.6%）
- [x] 验证引擎手续费与真实费率对得上（引擎 0.8%，偏保守）
- [x] 发现引擎默认不算滑点，并手动补上 0.05%
- [x] 记住一个来回真实成本 ≈ 1.7%

**第 1 周完成。**

---

## 9. 下周预告（第 2 周）

双均线（MA crossover）策略：有买有卖、会下多单。要点：
- 把本周的手续费 + 滑点设置直接写进 `initialize`
- crypto 用 UTC 时区：`self.set_time_zone(TimeZones.UTC)`
- 明确买 / 卖 / 空仓规则，禁止模糊表述
- 输出累计收益、最大回撤、胜率、盈亏比、Sharpe，**并与 buy & hold BTC 基准对比**
- 控制交易频率，别让 1.7% 的来回成本吃掉收益

---

*记录人：xun stark ｜ 第 1 周 ｜ QuantConnect 云端 · LEAN v2.5*
