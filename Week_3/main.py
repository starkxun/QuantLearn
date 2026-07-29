from AlgorithmImports import *
import numpy as np


class DualMAParamTest(QCAlgorithm):
    """第 3 周：参数化的双均线，用来交叉验证 code.py 的向量化回测

    和第 2 周的区别：均线周期、回测区间、是否收手续费全部走 QC Parameter，
    不用改代码就能换一组跑。作用有两个：
      1. 验证 code.py 里自己写的向量化回测没算错（两边数字应该很接近）
      2. 需要逐笔订单明细时，还是真引擎更可信

    在 QC 的 Parameters 面板里加这几个 key（不加就用下面的默认值）：
      fast=20  slow=60
      start=2023-01-01  end=2026-01-01     # 训练区间就填 2019-01-01 / 2023-01-01
      fees=1                               # 1=正常收费  0=零手续费零滑点
    """

    def initialize(self):
        # ---- 全部参数化 ----
        self.fast_n = int(self.get_parameter("fast") or 20)
        self.slow_n = int(self.get_parameter("slow") or 60)
        start = self._date(self.get_parameter("start") or "2023-01-01")
        end   = self._date(self.get_parameter("end")   or "2026-01-01")
        self.with_fees = (self.get_parameter("fees") or "1") == "1"

        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_cash(100000)
        self.set_time_zone(TimeZones.UTC)

        self.set_brokerage_model(BrokerageName.COINBASE, AccountType.CASH)
        self.btc = self.add_crypto("BTCUSD", Resolution.DAILY, Market.COINBASE).symbol

        if self.with_fees:
            # 第 1 周验证过：引擎手续费 0.80%，滑点默认是 Null，要手动补 0.05%
            self.securities[self.btc].set_slippage_model(ConstantSlippageModel(0.0005))
        else:
            # 零成本对照组：这才是"看起来赚钱"的那条曲线
            self.securities[self.btc].set_fee_model(ConstantFeeModel(0))
            self.securities[self.btc].set_slippage_model(NullSlippageModel())

        self.fast = self.sma(self.btc, self.fast_n, Resolution.DAILY)
        self.slow = self.sma(self.btc, self.slow_n, Resolution.DAILY)
        # 预热要按慢线长度走，写死 60 的话换成 slow=120 会静默少喂一半数据
        self.set_warm_up(self.slow_n, Resolution.DAILY)

        self.set_benchmark(self.btc)
        self.plot_indicator("BTC Moving Averages", self.fast, self.slow)

        self.prev_above = None
        self.entry_cost = 0.0
        self.total_fees = 0.0
        self.trades = []
        self.equity_curve = []
        self.bh_prices = []
        self.last_price = None

        self.log(f"配置: {self.fast_n}/{self.slow_n} | {start.date()} → {end.date()} | "
                 f"{'含费' if self.with_fees else '零成本'}")

    @staticmethod
    def _date(s):
        from datetime import datetime
        return datetime.strptime(s.strip(), "%Y-%m-%d")

    def on_data(self, data):
        if self.btc not in data.bars:
            return
        price = data.bars[self.btc].close
        self.last_price = price

        if self.is_warming_up or not self.slow.is_ready:
            return

        above = self.fast.current.value > self.slow.current.value
        if self.prev_above is None:
            self.prev_above = above
            return

        if above and not self.prev_above and not self.portfolio[self.btc].invested:
            self.set_holdings(self.btc, 0.95)
        elif not above and self.prev_above and self.portfolio[self.btc].invested:
            self.liquidate(self.btc)

        self.prev_above = above

    def on_order_event(self, oe):
        if oe.status != OrderStatus.FILLED:
            return
        fee = oe.order_fee.value.amount
        value = abs(oe.fill_price * oe.fill_quantity)
        self.total_fees += fee
        if oe.direction == OrderDirection.BUY:
            self.entry_cost = value + fee
        else:
            self.trades.append((value - fee) - self.entry_cost)

    def on_end_of_day(self, symbol):
        if not self.is_warming_up and self.last_price is not None:
            self.equity_curve.append(self.portfolio.total_portfolio_value)
            self.bh_prices.append(self.last_price)

    @staticmethod
    def _max_drawdown(curve):
        peak = np.maximum.accumulate(curve)
        return ((curve - peak) / peak).min()

    def on_end_of_algorithm(self):
        eq = np.array(self.equity_curve)
        if len(eq) < 2:
            self.log("数据不足，无法统计"); return
        rets = np.diff(eq) / eq[:-1]

        total_ret = eq[-1] / eq[0] - 1
        years = len(eq) / 365.0
        cagr = (eq[-1] / eq[0]) ** (1 / years) - 1
        max_dd = self._max_drawdown(eq)
        sharpe = rets.mean() / rets.std() * np.sqrt(365) if rets.std() > 0 else 0

        wins = [t for t in self.trades if t > 0]
        losses = [t for t in self.trades if t <= 0]
        win_rate = len(wins) / len(self.trades) if self.trades else 0
        pl_ratio = (np.mean(wins) / abs(np.mean(losses))
                    if wins and losses else float("nan"))

        bh = np.array(self.bh_prices)

        self.log(f"===== {self.fast_n}/{self.slow_n} "
                 f"{'含费' if self.with_fees else '零成本'} =====")
        self.log(f"总收益 {total_ret*100:+.2f}%  年化 {cagr*100:+.2f}%  "
                 f"回撤 {max_dd*100:.2f}%  Sharpe {sharpe:.2f}")
        self.log(f"笔数 {len(self.trades)}  胜率 {win_rate*100:.1f}%  "
                 f"盈亏比 {pl_ratio:.2f}")
        self.log(f"累计手续费 {self.total_fees:,.0f}  "
                 f"= 初始资金的 {self.total_fees/100000*100:.2f}%")
        self.log(f"BH 基准 {(bh[-1]/bh[0]-1)*100:+.2f}%  "
                 f"回撤 {self._max_drawdown(bh)*100:.2f}%")
