# QuantLearn · 量化学习实战笔记

在 [QuantConnect](https://www.quantconnect.com/) 云端 IDE（LEAN Engine v2.5）上，按周推进的量化交易学习记录。目标是**理解引擎底层在做什么，而不是盲跑回测**——每一周都有明确的验收清单、可复现的代码，以及踩过的坑。

标的以加密货币（BTC / ETH）为主，数据、回测、成本模型全部在 QC 云端完成，**无需本地环境**。

---

## 目录结构

```
QuantLearn/
├── 量化学习实战路线图.docx        # 总体学习路线图
├── Week_1/
│   ├── week01-quantconnect-setup-and-cost-model.md   # 第 1 周完整笔记（主文档）
│   ├── code.py                    # 第 1 周 Research notebook 代码（可直接粘贴运行）
│   └── docs.md                    # 补充笔记：验收清单 + 滑点概念图解
└── README.md
```

## 进度总览

| 周次 | 主题 | 状态 |
|---|---|---|
| Week 1 | QC 环境搭建、数据探索、成本模型验证 | ✅ 完成 |
| Week 2 | 双均线（MA crossover）策略：有买有卖、完整回测指标 | 🔜 规划中 |

---

## 第 1 周：QC 环境、数据探索与成本模型验证

跑通「**取数 → 质检 → 画图 → 验证成本模型**」全流程。核心结论：

- **环境**：QC 云端已配好 Python / pandas / matplotlib / 数据，不用在本地搭。
- **数据**：BTCUSD、ETHUSD 各约 1096 根日线（3 年），缺失 / 重复 / 缺口 / 异常价格全为 0。
- **成本模型**：手续费真实且偏保守（引擎 0.80% vs Coinbase 真实 taker ≈ 0.60%）。
- **关键发现**：QC 默认 `NullSlippageModel`——**回测默认不算滑点**，已手动补上 0.05%。
- **务必记住的数**：一个来回真实成本 ≈ **1.7%**，一笔交易涨不过这个数就是亏。

📖 详见 [Week_1/week01-quantconnect-setup-and-cost-model.md](Week_1/week01-quantconnect-setup-and-cost-model.md)

### 代码怎么用

[Week_1/code.py](Week_1/code.py) 是分好格子（CELL）的 Research notebook 代码，直接粘进 QC 的 `research.ipynb`，改一下顶部 CONFIG 就能跑：

```python
MARKET  = Market.COINBASE          # 数据来源，也决定回测的手续费模型
TICKERS = ["BTCUSD", "ETHUSD"]
START   = datetime(2022, 1, 1)
END     = datetime(2025, 1, 1)
```

包含四步：配置取数 → 数据质量检查 → 价格/成交量/累计收益画图 →（可选）小时线 + 保存 CSV。

---

## 环境与前置

- **平台**：QuantConnect 云端 IDE，LEAN Engine v2.5，全程无需本地安装。
- **账户**：免费档即可（需绑卡做防滥用验证，不扣费；每天约 100 次回测够用）。
- **API 风格**：QC 现行 Python API 用下划线小写（`add_crypto`、`history`），旧教程里的 `AddCrypto` 是 C# 写法，别照抄。

## 学习理念

- 好得离谱的回测先怀疑，别兴奋——先查是不是漏算了成本。
- 任何策略都要跑赢 **buy & hold** 基准才有价值。
- 参数越多越危险，警惕过拟合。

---

*记录人：xun stark ｜ QuantConnect 云端 · LEAN v2.5*
