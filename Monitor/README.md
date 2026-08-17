# Monitor 使用文档

怎么跑、数据里有什么、每一列什么单位、哪些地方容易读错。

设计意图和分天计划在 [docs.md](docs.md)，那份是**为什么**；这份是**怎么用**。

---

## 1. 快速开始

```bash
cd ~/QuantLearn
source .venv/bin/activate          # 或直接用 .venv/bin/python
cd Monitor
python peek.py                     # 先看一眼有哪些数据
```

凭证和代理都在仓库根的 `.env`，`config.py` 导入时自动加载，**不用 export**。

---

## 2. 日常命令

| 命令 | 干什么 | 耗时 |
|---|---|---|
| `python peek.py` | 全局概览：有哪些数据、覆盖到哪天 | 秒 |
| `python peek.py BTC --days 30` | 单币详情：现货 + 4 个指标 | 秒 |
| `python peek.py BTC --csv` | 导出 CSV，拿 Excel 看 | 秒 |
| `python peek.py --lsr` | LSR 最新快照（9 币横排） | 秒 |
| `python peek.py --lsr BTC` | 某币的 LSR 时间序列 | 秒 |
| `python fetch_spot.py` | 更新现货日线（增量） | ~10s |
| `python quality.py` | 数据质检 | 秒 |
| `python quality.py --verbose` | 质检 + 问题明细 | 秒 |
| `python fetch_coinglass.py` | 更新 4 个 Coinglass 指标 | **~8 分钟** |
| `python fetch_coinglass.py funding_rate` | 只更新一个指标 | ~2 分钟 |
| `deploy/pull_data.sh <目标>` | 从服务器拉 LSR 归档 | ~10s |

`fetch_coinglass.py` 慢是因为限频 6 次/分钟、必须串行垫 11 秒，不是卡住了。

---

## 3. 数据在哪、每列是什么

所有文件都是 parquet，`pd.read_parquet()` 直接读。**时间一律 UTC。**

### 3.1 `data/raw/spot/{币}USDT.parquet` — 现货日线

来源 `data-api.binance.vision`，无需 key。9 个文件，各币全历史。

| 列 | 类型 | 单位 / 含义 |
|---|---|---|
| `open_time` | datetime UTC | 该日 00:00 UTC |
| `open` `high` `low` `close` | float | USDT 计价 |
| `volume` | float | **基础币数量**（BTC 个数，不是美元） |
| `quote_volume` | float | **USDT 成交额**（要看美元金额用这列） |
| `close_time` | datetime UTC | 该日 23:59:59.999 |
| `trades` | int | 成交笔数 |
| `taker_buy_base` | float | 主动买入的基础币数量 |
| `taker_buy_quote` | float | 主动买入的 USDT 金额 |

> ⚠️ **只含已收盘的 K 线，永远不含当天。** 当天那根还在变，存进来会让指标每小时
> 都不一样、没法复现。所以「最新」永远是昨天。

### 3.2 `data/raw/coinglass/*.parquet` — 4 个衍生品指标

每个文件含**全部 9 个币**，用 `coin` 列筛。都是日线。

**`funding_rate.parquet`** — 资金费率

| 列 | 单位 |
|---|---|
| `open` `high` `low` `close` | **百分数 / 每 8h**（`0.008579` = 0.0086%） |
| `funding_annualized` | 年化**小数**（`0.0733` = 7.33%），用 `close` 算 |
| `funding_annualized_mid` | 年化小数，用 `(high+low)/2` 算 |

> ⚠️ **两个口径经常不一致，甚至符号相反**（XRP 曾 close +9.14% / mid −1.22%）。
> 原因是 1d 那根是当天 3 次结算的聚合，`close` 只是日末最后一次。
> **做分析时两个都跑一遍**，不一致说明结论对口径敏感。详见 docs.md 6.2c。

> ⚠️ **BNB 有 49%、LINK 有 3.7% 的天数 funding 恰好为 0，这是真的不是缺失。**
> 币安对利率设为 0 的交易对，溢价在 ±0.05% 内时 funding 精确等于 0。
> 统计时别把 0 当缺失丢掉。

**`open_interest.parquet`** — 未平仓合约

| 列 | 单位 |
|---|---|
| `open` `high` `low` `close` | **USD**（BTC 约 7e9 = 70 亿美元） |

**`top_ls_ratio.parquet`** — 大户持仓多空比

| 列 | 单位 |
|---|---|
| `top_position_long_percent` | 百分数（`60.33` = 60.33%） |
| `top_position_short_percent` | 百分数 |
| `top_position_long_short_ratio` | 比值（`1.52` = 多头是空头的 1.52 倍） |

> ⚠️ 币安历史上改过这个数据的统计口径，**回测要分段看**，别一条曲线拉到底。

**`taker_volume.parquet`** — 主动买卖量

| 列 | 单位 |
|---|---|
| `taker_buy_volume_usd` `taker_sell_volume_usd` | USD |

> ⚠️ 起点比别的指标晚：多数币 2021-06，**公共起始日 2023-05-04**。

### 3.3 `data/lsr_archive/YYYY-MM-DD.parquet` — LSR 高频快照

**这份数据卖家不提供历史，只能自己攒**，所以由服务器 24×7 采集，每 5 分钟一条。
一天满额 288 个快照 × 9 币 = 2592 行。

| 列 | 含义 |
|---|---|
| `fetched_at` | **我们抓到它的时刻**（UTC） |
| `source_updated_at` | **源数据自己的时间戳**（unix 秒） |
| `cache_age_seconds` | 抓到时数据已经多旧 |
| `symbol` | `BTCUSDT` 这种写法 |
| `ratio` | 交易者多空比（按名义金额） |
| `trader_delta_2m/30m/4h` | 多空比的多周期变化率 |
| `whale_ratio` `whale_delta_*` | 大户口径的同类字段 |
| `ov_total_traders` | 样本里的交易者总数 |
| `ov_long_traders` `ov_short_traders` | 多/空**人数** |
| `ov_long_avg_entry` `ov_short_avg_entry` | 多/空**平均开仓成本** ← 币安公开 API 没有 |
| `ov_long_profit_traders` | 当前浮盈的多头人数 |
| `whale_long_count` `whale_short_count` | 大户多/空人数 |
| `oi_mcap_ratio` | 持仓量 / 市值 |

> ⚠️ **按 `source_updated_at` 去重，不要按 `fetched_at`。** 源不是严格 2 分钟一变，
> 实测 14 个快照里只有 13 个不同的源时间戳——有快照抓到的是同一份数据。

> ⚠️ **样本小且是自选的。** BTC 的 `ov_total_traders` 只有 1600 左右，来自主动公开
> 持仓的人。叫「公开晒单者的仓位」比「聪明钱」准确。分析时心里要有数。

> ⚠️ `null` 保留为 NaN，**不要 `fillna(0)`**。手册明确 null 不是 0。

---

## 4. 常用查询（可直接复制）

> 下面每一段都实测跑通过（2026-08-17）。

```python
import pandas as pd
import numpy as np
from config import RAW_SPOT, RAW_CG, LSR_ARCHIVE, perp

# ---- 读一个币的现货，算日收益 ----
btc = pd.read_parquet(RAW_SPOT / "BTCUSDT.parquet").set_index("open_time")
btc["ret"] = btc["close"].pct_change()

# ---- 读某个指标的某个币 ----
f = pd.read_parquet(RAW_CG / "funding_rate.parquet")
btc_f = f[f.coin == "BTC"].set_index("date")

# ---- 现货 + funding 对齐 ----
#（日期都是当日 00:00 UTC，直接 join 就对齐）
merged = btc.join(btc_f[["funding_annualized", "funding_annualized_mid"]])

# ---- 9 币收盘价拼成宽表（横截面分析的起点）----
wide = pd.DataFrame({
    c: pd.read_parquet(RAW_SPOT / f"{perp(c)}.parquet")
         .set_index("open_time")["close"]
    for c in ["BTC","ETH","SOL","BNB","XRP","LINK","AVAX","ADA","SUI"]
})
wide = wide.loc["2023-05-03":]          # 公共起始日，见 docs.md 6.2b
rets = wide.pct_change()

# ---- 60 日滚动平均两两相关性 ----
# 取相关矩阵的非对角线均值。实测区间 0.38 ~ 0.92，当前约 0.68
corr = rets.rolling(60).corr()

def _avg_offdiag(m):
    v = m.values
    return v[~np.eye(len(v), dtype=bool)].mean() if v.shape[0] == v.shape[1] else np.nan

avg_corr = corr.groupby(level=0).apply(_avg_offdiag)

# ---- LSR 归档：合并所有天，按源时间戳去重 ----
# 实测 144 行 → 126 行，去掉了 2 个抓到同一份源数据的快照
lsr = pd.concat([pd.read_parquet(f) for f in sorted(LSR_ARCHIVE.glob("*.parquet"))],
                ignore_index=True)
lsr = lsr.drop_duplicates(subset=["source_updated_at", "symbol"])
btc_lsr = lsr[lsr.symbol == "BTCUSDT"].sort_values("fetched_at")
```

---

## 5. 服务器运维

归档器跑在 `ubuntu@43.155.206.51`（首尔），systemd 服务 `lsr-archive`。

```bash
# 还活着吗
ssh ubuntu@43.155.206.51 'systemctl is-active lsr-archive'
ssh ubuntu@43.155.206.51 'tail -5 ~/QuantLearn/Monitor/data/archive.log'

# 拉数据回本地（合并去重，跑几次都安全）
deploy/pull_data.sh ubuntu@43.155.206.51:/home/ubuntu/QuantLearn

# 改了代码后重新部署（幂等）
deploy/deploy.sh -y ubuntu@43.155.206.51

# 重启服务
ssh ubuntu@43.155.206.51 'sudo systemctl restart lsr-archive'
```

> ⚠️ **限频 6 次/分钟是按 API key 计的，只能在一个地方跑归档。**
> 服务器已经在跑，本地不要再执行 `archive_lsr.py`，否则两边互相打 429。

**每周花 10 分钟检查**：服务还活着吗？归档天数在涨吗？磁盘够吗？

---

## 6. 坑速查

| 现象 | 原因 |
|---|---|
| 现货数据「少一天」 | 只存已收盘 K 线，最新永远是昨天 |
| funding 算出来符号反了 | 用了 `close` 口径，换 `_mid` 再看一遍 |
| BNB funding 一半是 0 | 真的，币安该对利率设为 0，不是缺失 |
| `fetch_coinglass.py` 跑 8 分钟 | 限频 6 次/分钟必须串行，正常 |
| 429「上游 Key 额度不足」 | 中转站上游的额度，不是我们的 6/min，等一会再试 |
| LSR 快照数少于 288 | 服务器那天没跑满，或还没到当天结束 |
| 相关性算出来是 NaN | 早于 2023-05-03，SUI 还没上市 |
| 请求偶发失败 | 本地代理丢包，重试机制会兜住；服务器上不走代理 |

---

## 7. 现在能做什么 / 还不能做什么

**能做**（Day 1~3 已完成）：读现货日线、funding、OI、大户多空比、主动买卖量的完整历史；
看 LSR 实时快照。

**还不能做**（Day 4~8）：趋势/波动指标、横截面指标、regime 判断。
这些还没写，别指望 `peek.py` 给结论——**它只摊开原始数据，不做任何判断**。

**永远不做**：买卖信号。见 docs.md 第 2 节红线清单。
