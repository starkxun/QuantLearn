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
| `python indicators.py` | 算第 1~2 层指标 → `data/indicators.parquet` | 秒 |
| `python indicators.py --show BTC` | 算完顺便看最近几天 | 秒 |
| `python indicators.py --verify` | 指标验收（无未来函数 / ER 合理性 / 对齐 Week 3） | 秒 |
| `python cross_section.py` | 算第 3 层横截面 → 两个 parquet | 秒 |
| `python cross_section.py --show 15` | 算完看最近 15 天市场状态 | 秒 |
| `python cross_section.py --verify` | 横截面验收 | 秒 |
| `python panel.py` | 组装面板 → 快照 + markdown 报告 | 秒 |
| `python panel.py --date 2026-08-10` | 补算历史某天 | 秒 |
| `./run_daily.sh` | **完整日频流水线**（取数→指标→面板） | ~9 分钟 |
| `./run_daily.sh --quick` | 同上但跳过 Coinglass | ~20 秒 |
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

### 3.4 `data/indicators.parquet` — 第 1~2 层指标

由 `indicators.py` 从现货日线算出。含全部 9 币（`coin` 列筛），**各币用各自的全历史**。
参数全部写死（MA 20/60、ER 20、ATR 14、波动 30 日），本项目不做参数优化。

| 列 | 含义 | 读法 |
|---|---|---|
| `dist_ma20` `dist_ma60` | `收盘/均线 − 1` | `+0.05` = 高于均线 5% |
| `ma20_vs_ma60` | `MA20/MA60 − 1` | `>0` 等价于金叉状态（已验证与 Week 3 零分歧） |
| `er20` | Kaufman 效率系数 | **0~1，越接近 1 越单边，越接近 0 越震荡** |
| `donchian20` | 20 日区间位置 | `0`=区间底 `1`=区间顶 |
| `pos_1y` | 365 日区间位置 | 同上 |
| `mom_1m` `mom_3m` `mom_6m` | 30/90/180 日收益 | 小数 |
| `atr_pct` | `ATR(14)/收盘` | **跨币可比的波动度量**，`0.02` = 日均真实波幅 2% |
| `rv30` | 30 日已实现波动率（年化） | `0.22` = 22% |
| `rv30_pct` | `rv30` 在**自身历史**中的分位 | expanding 计算，只跟过去比 |
| `dd_ath` | 距历史最高点的回撤 | `-0.49` = 腰斩 |
| `dd_1y` | 距 365 日最高点的回撤 | 同上 |

> ⚠️ **一律存连续距离，不存布尔金叉。** 「刚穿过 0.1%」和「远在上方 30%」是完全不同的
> 处境，布尔化会把这个信息丢光。要布尔自己 `> 0` 就行。

> ⚠️ 年化用 `sqrt(365)` 不是 252 —— crypto 全年无休，「52 周高点」也是 365 天。

> ✅ **无未来函数已验证**：把数据截断到某天重算，历史值与全样本完全一致。
> 加新指标后请重跑 `indicators.py --verify`。

### 3.5 `data/cross_section_market.parquet` — 市场级（每天一行）

由 `cross_section.py` 算出，**区间固定 2023-05-03 起**（公共起始日）。

| 列 | 含义 | 读法 |
|---|---|---|
| `avg_corr60` | 9 币 60 日平均两两相关 | **本项目最该告警的指标**，飙升=分散化失效 |
| `dispersion` | 当日 9 币收益的横截面标准差 | 低=纯 beta 行情，选币无意义 |
| `mkt_ret` | 等权 9 币当日收益 | 市场参照 |
| `breadth_ma60` `breadth_ma200` | 站上均线的币数 | 0~9 |
| `breadth_ma60_n` `breadth_ma200_n` | **当天均线已就绪的币数**（分母） | SUI 的 MA200 要到 2023-11 才有 |
| `breadth_ma60_pct` | 上面两者之比 | 跨时期可比，直接看这个 |

> ⚠️ 广度要看 `_pct` 或自己除以 `_n`。直接看 `breadth_ma200` 会在早期被系统性低估——
> 那时 SUI 的 200 日均线还没就绪，分母不是 9。

### 3.6 `data/cross_section_coin.parquet` — 个币级（币 × 日）

| 列 | 含义 | 读法 |
|---|---|---|
| `rel_btc` | 该币价格 / BTC 价格 | BTC 自己恒为 1 |
| `rel_btc_chg20` | 上面这个比值的 20 日变化 | **>0 = 近 20 日跑赢 BTC**，山寨 alpha 在这 |
| `mom_3m` | 90 日收益 | 小数 |
| `mom3m_rank` | 当日 9 币的动量排名 | **1 = 最强**，9 = 最弱 |
| `mom3m_rank_chg20` | 排名 20 日变化 | **正数 = 名次前进**（数字变小） |

> ⚠️ 一个已验证的基线：3.3 年下来 **9 个币里 8 个跑输 BTC**，只有 SOL 跑赢（+54.8%）。
> 拿山寨的隐含假设是"我能选对"。

### 3.7 `data/snapshots/YYYY-MM-DD.{parquet,md}` — 每日面板快照

由 `panel.py` 生成，**一天一个，永不覆盖**（`--force` 才会盖）。

- `.parquet` — 9 行（每币一行），含第 1~3 层全部指标 + Coinglass 四件套 +
  市场级字段（随行复制，一个文件读全）
- `.md` — 人看的报告，同样内容排成表格

面板日期锚定**现货最后一根已收盘 K 线**，所以永远是「昨天」。
Coinglass 当天虽有值，但那是没走完的一天，混进来快照就不可复现了。

> ⚠️ **这份快照是三个月后才显出价值的东西。** 每一次静默覆盖都是在毁掉未来的样本，
> 所以默认跳过已存在的日期。

**面板只呈现数据，不做判断。** 没有"该买/该卖"，也没有"当前是趋势/震荡"——
状态标签属于 Day 8，且只允许接入 Day 7 验证过的因子。

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

# 日频面板：排程 / 上次结果 / 日志
ssh ubuntu@43.155.206.51 'systemctl list-timers panel-daily --no-pager'
ssh ubuntu@43.155.206.51 'systemctl status panel-daily.service --no-pager'
ssh ubuntu@43.155.206.51 'tail -20 ~/QuantLearn/Monitor/data/daily.log'

# 手动触发一次日频（不等 timer）
ssh ubuntu@43.155.206.51 'sudo systemctl start panel-daily.service'
```

> ⚠️ **限频 6 次/分钟是按 API key 计的，只能在一个地方跑。**
> 服务器已经在跑归档器和日频流水线，本地不要再执行 `archive_lsr.py` 或
> `fetch_coinglass.py`，否则两边互相打 429。本地要新数据就用 `pull_data.sh` 拉。

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
| 相关性算出来是 NaN | 早于 2023-05-03，或窗口不满 60 天 |
| 广度看着偏低 | 用了绝对计数，该看 `_pct` 或除以 `_n` |
| 请求偶发失败 | 本地代理丢包，重试机制会兜住；服务器上不走代理 |

---

## 7. 现在能做什么 / 还不能做什么

**能做**（Day 1~6 已完成）：读现货日线、funding、OI、大户多空比、主动买卖量的完整历史；
看 LSR 实时快照；查单币的趋势/波动状态（`indicators.parquet`）；
查市场级横截面状态（相关性/离散度/广度）与个币相对强弱；
出每日面板快照（服务器每天 00:30 UTC 自动跑）。

**还不能做**（Day 7~8）：因子验证、regime 判断。
这些还没写，别指望 `peek.py` 给结论——**它只摊开原始数据，不做任何判断**。

**永远不做**：买卖信号。见 docs.md 第 2 节红线清单。
