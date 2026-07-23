- [x] 用 QC / LEAN 实现最简单的双均线策略
- [x] 明确买入、卖出、空仓规则，禁止模糊表述
- [x] 确认引擎已计入手续费和基础滑点
- [x] 输出累计收益、最大回撤、胜率、盈亏比、Sharpe，并加一条 buy-and-hold BTC 基准做对比

### 双均线金叉/死叉策略

```bash
价格趋势判断：
- 金叉（Golden Cross）：快线上穿慢线 → 上涨趋势 → 买入
- 死叉（Death Cross）：快线下穿慢线 → 下跌趋势 → 卖出
- 其余时间：持仓不动（避免频繁交易）

# 快线（Fast MA）：20日均线（短期趋势）
# 慢线（Slow MA）：60日均线（长期趋势）
```

**预热（Warm Up）**
```bash
如果没有预热：
第1天：需要60天数据计算慢线 → 没有数据 → 无法交易 

有预热（60天）：
回测开始前先加载60天历史数据
第1天：慢线已有60天数据 → 可以立即交易
```

```python
# 创建均线指标（引擎自动更新）
self.fast = self.sma(self.btc, 20, Resolution.DAILY)  # 20日均线
self.slow = self.sma(self.btc, 60, Resolution.DAILY)  # 60日均线

# 预热：提前加载60天数据，让慢线有值
self.set_warm_up(60, Resolution.DAILY)

# 设置基准（用于对比策略表现）
self.set_benchmark(self.btc)
```


`self.is_warming_up` 的含义:
```bash
 预热阶段：
 - 引擎在加载前60天的历史数据
 - 此时 is_warming_up = True
 - 策略不执行交易（避免使用不完整的指标）

 预热结束后：
 - is_warming_up = False
 - 指标已经有足够数据
 - 开始正常交易
```

```python
def on_data(self, data):
    # 检查是否有BTC数据
    if self.btc not in data.bars:
        return
    price = data.bars[self.btc].close
    self.last_price = price

    # 预热判断：是否还在加载历史数据？
    if self.is_warming_up or not self.slow.is_ready:
        return
```

### 金叉（Golden Cross）和死叉（Death Cross）

**金叉：**

快线从下往上穿过慢线 → 短跑选手超过长跑选手
含义：短期趋势转强，可能开启上涨行情
信号：买入

**死叉:**
快线从上往下穿过慢线 → 短跑选手被长跑选手反超
含义：短期趋势转弱，可能开启下跌行情
信号：卖出

**均线是如何计算**
简单移动平均线（SMA）:
```bash
# 20日均线 = 最近20天收盘价的平均值
SMA_20 = (Day1 + Day2 + ... + Day20) / 20

# 60日均线 = 最近60天收盘价的平均值
SMA_60 = (Day1 + Day2 + ... + Day60) / 60
```
举例：
```bash
# 最近5天的价格
prices = [100, 102, 101, 103, 105]

# 5日均线
SMA_5 = (100 + 102 + 101 + 103 + 105) / 5 = 102.2

# 每天更新（滚动计算）
新的一天：价格 = 106
新的5日均线 = (102 + 101 + 103 + 105 + 106) / 5 = 103.4
```

金叉（买入信号）的逻辑
```bash
金叉形成过程：
1. 价格开始上涨 → 快线（短期）先反应，开始上升
2. 慢线（长期）反应慢，还在低位
3. 快线从下方穿过慢线 → 金叉
4. 说明上涨趋势已经确立，短期动能强于长期

市场心理：
- 早期买家已经入场
- 趋势可能持续
- 是跟随趋势的好时机
```

死叉（卖出信号）的逻辑
```bash
死叉形成过程：
1. 价格开始下跌 → 快线（短期）先反应，开始下降
2. 慢线（长期）反应慢，还在高位
3. 快线从上方穿过慢线 → 死叉
4. 说明下跌趋势已经确立，短期动能弱于长期

市场心理：
- 早期卖家已经离场
- 下跌趋势可能持续
- 是规避风险的好时机
```

示例流程：
```bash
# 金叉例子：
昨天：快线(18) < 慢线(20) → prev_above = False
今天：快线(22) > 慢线(20) → above = True
信号：金叉！买入 

# 死叉例子：
昨天：快线(22) > 慢线(20) → prev_above = True
今天：快线(18) < 慢线(20) → above = False
信号：死叉！卖出 
```

### OnOrderEvent - 订单事件处理

```python
def on_order_event(self, oe):
    # 只处理完全成交的订单
    if oe.status != OrderStatus.FILLED:
        return
    
    fee = oe.order_fee.value.amount  # 手续费
    value = abs(oe.fill_price * oe.fill_quantity)  # 成交金额
    
    if oe.direction == OrderDirection.BUY:
        # 买入：记录总成本（含手续费）
        self.entry_cost = value + fee
    else:
        # 卖出：计算盈亏
        pnl = (value - fee) - self.entry_cost  # 净收入 - 总成本
        self.trades.append(pnl)
        self.log(f"平仓 @ {oe.fill_price:.2f}  本笔净盈亏: {pnl:+.2f}")
```

模拟手动计算：
```bash
    # 买入场景：
    买入100股 @ $100/股
    成交金额 = $10,000
    手续费 = $80 (0.8%)
    总成本 = $10,080
    self.entry_cost = $10,080 

    # 卖出场景：
    卖出100股 @ $120/股
    成交金额 = $12,000
    手续费 = $96 (0.8%)
    净收入 = $11,904
    盈亏 = $11,904 - $10,080 = +$1,824 
```

### 最终绩效统计
```python
def on_end_of_algorithm(self):
    eq = np.array(self.equity_curve)
    
    # 计算每日收益率
    rets = np.diff(eq) / eq[:-1]
    
    # 累计收益
    total_ret = eq[-1] / eq[0] - 1
    
    # 最大回撤
    peak = np.maximum.accumulate(eq)
    max_dd = ((eq - peak) / peak).min()
    
    # 夏普比率（年化）
    sharpe = rets.mean() / rets.std() * np.sqrt(365)
    
    # 胜率
    wins = [t for t in self.trades if t > 0]
    losses = [t for t in self.trades if t <= 0]
    win_rate = len(wins) / len(self.trades)
    
    # 盈亏比
    pl_ratio = np.mean(wins) / abs(np.mean(losses))
```

其中，夏普比率衡量"每承担1单位风险，能获得多少超额收益".`sharpe = rets.mean() / rets.std() * np.sqrt(365)`rets.mean()：每日平均收益率：

```bash
rets.mean() # 每日平均收益率
rets.std()  # 每日收益率的标准差（风险）
sqrt(365)   # 年化系数（加密货币7×24交易）
```
举例：平均日收益 0.1%， 日收益标准差 2%，则夏普 = 0.001 / 0.02 × 19.1 = 0.955

### 问题回答

- 1. 为什么赚：「策略在 ____ 年 __ 月的趋势段持仓 __ 天，单笔赚了 __%，因为趋势跟随吃到了主升段。」

- 2. 为什么亏：「在 ____ 年 __ 月的震荡段被假信号来回打脸 __ 次，每次穿越成本 1.7% 加上高买低卖，合计亏 __%。」


- 3. 相对拿住 BTC 是赢还是输：直接引用最后一行 log。注意：2022-2025 这段 BTC 整体大涨，双均线很可能输给 buy & hold——这是正常且重要的认知，趋势策略的价值往往在回撤控制（对比两者的最大回撤），而不是绝对收益。输了但回撤小一半，也是有意义的结论。