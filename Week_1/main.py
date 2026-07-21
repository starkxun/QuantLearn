from AlgorithmImports import *

class CostModelCheck(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2024, 1, 1)
        self.set_end_date(2024, 1, 10)
        self.set_cash(100000)

        # 关键：券商模型决定手续费和滑点，要和你的目标交易所对齐
        self.set_brokerage_model(BrokerageName.COINBASE, AccountType.CASH)
        self.btc = self.add_crypto("BTCUSD", Resolution.DAILY, Market.COINBASE).symbol
        self.done = False

    def on_data(self, data):
        if not self.done and self.btc in data.bars:
            sec = self.securities[self.btc]
            # 先看看引擎给这只标的挂了什么模型
            self.log(f"手续费模型: {type(sec.fee_model).__name__}")
            self.log(f"滑点模型:   {type(sec.slippage_model).__name__}")
            self.reference_price = data.bars[self.btc].close   # 下单时我看到的价格
            self.log(f"下单参考价: {self.reference_price}")
            self.market_order(self.btc, 1)                     # 市价买 1 BTC（taker）
            self.done = True

    def on_order_event(self, oe):
        if oe.status == OrderStatus.FILLED:
            fill = oe.fill_price
            qty = abs(oe.fill_quantity)
            value = fill * qty
            fee = oe.order_fee.value.amount          # 手续费金额
            cur = oe.order_fee.value.currency        # 手续费币种
            self.log(f"成交价: {fill}  数量: {qty}  成交额: {value:.2f}")
            self.log(f"手续费: {fee} {cur}")
            self.log(f"等效费率: {fee / value * 100:.4f}%")
            # 滑点：成交价和下单参考价的差
            slip = (fill - self.reference_price) / self.reference_price * 100
            self.log(f"滑点: {slip:.4f}%（≈0 就说明引擎没算滑点）")