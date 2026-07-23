from AlgorithmImports import *
import numpy as np


class DualMACrossover(QCAlgorithm):
    """第 2 周：双均线（SMA 20/60）趋势跟随策略

    规则（明确、无模糊）：
    - 金叉（fast 上穿 slow）且空仓 -> 95% 资金市价买入
    - 死叉（fast 下穿 slow）且持仓 -> 全部卖出
    - 其余时间不动；不做空（现货 CASH 账户）
    """

    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2025, 1, 1)
        self.set_cash(100000)
        self.set_time_zone(TimeZones.UTC)          # crypto 用 UTC

        # ---- 第 1 周验证过的成本模型：手续费 0.8% + 手动补 0.05% 滑点 ----
        self.set_brokerage_model(BrokerageName.COINBASE, AccountType.CASH)
        self.btc = self.add_crypto("BTCUSD", Resolution.DAILY, Market.COINBASE).symbol
        self.securities[self.btc].set_slippage_model(ConstantSlippageModel(0.0005))

        # ---- 指标：引擎自动喂数据，不用手动 update ----
        self.fast = self.sma(self.btc, 20, Resolution.DAILY)    # 快线指标
        self.slow = self.sma(self.btc, 60, Resolution.DAILY)    # 慢线指标
        self.set_warm_up(60, Resolution.DAILY)     # 先喂 60 根让慢线就绪

        self.set_benchmark(self.btc)               # 图表上叠加 BTC 基准线

        # ---- 状态与统计 ----
        self.prev_above = None       # 昨日 fast 是否在 slow 上方
        self.entry_cost = 0.0        # 本次入场总花费（含手续费）
        self.trades = []             # 每笔完整交易（买->卖）的净盈亏
        self.equity_curve = []       # 每日策略净值
        self.bh_start_price = None   # buy & hold 基准起点价
        self.last_price = None

    def on_data(self, data):
        if self.btc not in data.bars:
            return
        price = data.bars[self.btc].close
        self.last_price = price

        # 这里的判断是什么意思？
        if self.is_warming_up or not self.slow.is_ready:
            return

        # 基准和策略从同一天起跑，对比才公平
        if self.bh_start_price is None:
            self.bh_start_price = price

        above = self.fast.current.value > self.slow.current.value

        if self.prev_above is None:            # 第一天只记状态，不追入场
            self.prev_above = above
            return

        # q - 这里的self.portfolio[self.btc].invested是什么意思
        # 答：表示当前是否持有该标的的仓位，如果为 True，说明已经买入；为 False，说明未持仓
        if above and not self.prev_above and not self.portfolio[self.btc].invested:
            self.set_holdings(self.btc, 0.95)  # 金叉：留 5% 现金付手续费
        elif not above and self.prev_above and self.portfolio[self.btc].invested:
            self.liquidate(self.btc)           # 死叉：清仓

        self.prev_above = above

    def on_order_event(self, oe):
        # q - 这里的判断是什么意思？
        # 答：只有当订单被完全成交时，才会更新成本和记录盈亏
        if oe.status != OrderStatus.FILLED:
            return
        fee = oe.order_fee.value.amount
        value = abs(oe.fill_price * oe.fill_quantity)
        if oe.direction == OrderDirection.BUY:
            self.entry_cost = value + fee              # 买入：记下总成本
        else:
            pnl = (value - fee) - self.entry_cost      # 卖出：净收入 - 总成本
            self.trades.append(pnl)
            self.log(f"平仓 @ {oe.fill_price:.2f}  本笔净盈亏: {pnl:+.2f}")

    def on_end_of_day(self, symbol):
        if not self.is_warming_up:
            self.equity_curve.append(self.portfolio.total_portfolio_value)

    def on_end_of_algorithm(self):
        eq = np.array(self.equity_curve)
        if len(eq) < 2:
            self.log("数据不足，无法统计"); return
        rets = np.diff(eq) / eq[:-1]

        total_ret = eq[-1] / eq[0] - 1
        peak = np.maximum.accumulate(eq)
        max_dd = ((eq - peak) / peak).min()
        sharpe = rets.mean() / rets.std() * np.sqrt(365) if rets.std() > 0 else 0
        # crypto 7x24，年化用 365 而不是股票的 252

        wins = [t for t in self.trades if t > 0]
        losses = [t for t in self.trades if t <= 0]
        win_rate = len(wins) / len(self.trades) if self.trades else 0
        pl_ratio = (np.mean(wins) / abs(np.mean(losses))
                    if wins and losses else float("nan"))

        bh_ret = self.last_price / self.bh_start_price - 1

        self.log("========== 第 2 周验收指标 ==========")
        self.log(f"完整交易笔数: {len(self.trades)}")
        self.log(f"累计收益:   {total_ret * 100:+.2f}%")
        self.log(f"最大回撤:   {max_dd * 100:.2f}%")
        self.log(f"胜率:       {win_rate * 100:.1f}%")
        self.log(f"盈亏比:     {pl_ratio:.2f}")
        self.log(f"Sharpe(年化): {sharpe:.2f}")
        self.log("------- vs Buy & Hold BTC -------")
        self.log(f"基准收益:   {bh_ret * 100:+.2f}%")
        self.log(f"超额收益:   {(total_ret - bh_ret) * 100:+.2f}%  "
                 f"({'赢' if total_ret > bh_ret else '输'}基准)")
