from AlgoAPI import AlgoAPIUtil, AlgoAPI_Backtest
import datetime
import math


class AlgoEvent:
    def __init__(self):
        self.price_history = {}  # 价格序列
        self.volume_history = {}  # 成交量序列
        self.last_trade_time = {}  # 上次交易时间
        self.rsi_period = 14
        self.macd_short = 12
        self.macd_long = 26
        self.macd_signal = 9
        self.invest_amount = 1000  # 基础投资金额

        # 资金管理增强
        self.actual_balance = 100000  # 实际账户余额
        self.frozen_funds = 0  # 冻结资金（已下单未成交）
        self.initial_cash = 100000  # 初始资金记录
        self.positions = {}  # 持仓跟踪
        self.pending_orders = {}  # 待处理订单跟踪
        self.trading_frozen = False  # 交易冻结标志（余额不足时）

        # 风险控制参数 - 调整为更宽松的设置
        self.buffer_factor = 1.02  # 2%缓冲（降低缓冲比例）
        self.min_buffer_amount = 50  # 最少缓冲金额（降低）

    def start(self, mEvt):
        self.evt = AlgoAPI_Backtest.AlgoEvtHandler(self, mEvt)
        self.evt.start()
        self.evt.consoleLog(f"[SYSTEM] 策略启动，初始余额: {self.actual_balance:.2f}")

    def update_available_balance(self):
        """计算并返回当前可用余额（扣除冻结资金）"""
        available = self.actual_balance - self.frozen_funds
        return max(available, 0)  # 确保不会为负

    def freeze_funds(self, amount):
        """冻结资金用于待处理订单"""
        self.frozen_funds += amount
        self.evt.consoleLog(
            f"冻结资金: {amount:.2f}, 冻结总额: {self.frozen_funds:.2f}, 实际余额: {self.actual_balance:.2f}")

    def unfreeze_funds(self, amount):
        """释放冻结资金"""
        self.frozen_funds -= amount
        self.frozen_funds = max(self.frozen_funds, 0)  # 确保不会为负

    def calculate_max_buy_quantity(self, price, instrument):
        """安全计算最大可买数量，包括多重检查"""
        available_balance = self.update_available_balance()

        # 如果余额不足或交易冻结
        if available_balance <= 0 or self.trading_frozen:
            return 0

        # 计算需求资金（带缓冲）
        required_funds_per_unit = price * self.buffer_factor

        # 最大可买数量（基于可用资金）
        max_by_funds = math.floor(available_balance / required_funds_per_unit)

        # 最大不超过投资金额限制
        invest_qty = math.floor(self.invest_amount / price)

        return min(max_by_funds, invest_qty) if max_by_funds > 0 else 0

    def calculate_rsi(self, prices, period):
        if len(prices) < period + 1:
            return None

        gains, losses = [], []
        for i in range(1, period + 1):
            delta = prices[-i] - prices[-i - 1]
            if delta > 0:
                gains.append(delta)
            else:
                losses.append(abs(delta))

        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_macd(self, prices):
        if len(prices) < self.macd_long + self.macd_signal:
            return None, None, None

        def ema(data, period):
            k = 2 / (period + 1)
            ema_values = [data[0]]
            for price in data[1:]:
                ema_values.append(price * k + ema_values[-1] * (1 - k))
            return ema_values

        ema_short = ema(prices, self.macd_short)
        ema_long = ema(prices, self.macd_long)
        macd_line = [short - long for short, long in zip(ema_short[-len(ema_long):], ema_long)]
        signal_line = ema(macd_line, self.macd_signal)
        macd_hist = [macd - signal for macd, signal in zip(macd_line[-len(signal_line):], signal_line)]

        return macd_line[-1], signal_line[-1], macd_hist[-1]

    def on_marketdatafeed(self, md, ab):
        if self.trading_frozen:
            self.evt.consoleLog("[交易冻结] 因余额不足，交易已被冻结")
            return

        instrument = md.instrument
        price = md.lastPrice
        volume = md.volume
        current_time = md.timestamp

        if price == 0 or not instrument:
            return

        # 初始化数据结构
        if instrument not in self.price_history:
            self.price_history[instrument] = []
            self.volume_history[instrument] = []
            self.last_trade_time[instrument] = None
            self.positions[instrument] = 0
            self.pending_orders[instrument] = []

        # 更新价格和成交量历史
        self.price_history[instrument].append(price)
        self.volume_history[instrument].append(volume)

        # 保持数据窗口
        if len(self.price_history[instrument]) > 200:
            self.price_history[instrument].pop(0)
            self.volume_history[instrument].pop(0)

        # 检查交易冷却时间（1分钟）
        if (self.last_trade_time.get(instrument) and
                (current_time - self.last_trade_time[instrument]) < datetime.timedelta(minutes=1)):
            return

        # 计算技术指标
        rsi = self.calculate_rsi(self.price_history[instrument], self.rsi_period)
        macd, signal, hist = self.calculate_macd(self.price_history[instrument])

        if rsi is None or macd is None:
            return

        # 成交量过滤 - 使用最近30个数据点
        if len(self.volume_history[instrument]) >= 30:
            avg_volume = sum(self.volume_history[instrument][-30:]) / 30
            if volume < avg_volume:
                return

        # ========= 买入信号处理 =========
        if (rsi < 30) or (macd > signal and hist > 0):
            # 安全计算可买数量（包括多重检查）
            quantity = self.calculate_max_buy_quantity(price, instrument)
            if quantity <= 0:
                self.evt.consoleLog(f"[{instrument}] 可买数量不足: {quantity}")
                return

            # 计算需求资金并冻结
            required_funds = quantity * price * self.buffer_factor
            available = self.update_available_balance()
            if required_funds > available:
                self.evt.consoleLog(f"[{instrument}] 资金不足冻结所需: {required_funds:.2f} > {available:.2f}")
                return

            try:
                order = AlgoAPIUtil.OrderObject(
                    instrument=instrument,
                    openclose='open',
                    buysell=1,
                    ordertype=0,
                    volume=quantity
                )
                tradeID = self.evt.sendOrder(order)

                # 记录订单并冻结资金
                self.pending_orders[instrument].append({
                    "id": tradeID,
                    "type": "buy",
                    "quantity": quantity,
                    "price": price,
                    "frozen_amount": required_funds
                })
                self.freeze_funds(required_funds)

                self.evt.consoleLog(
                    f"[{instrument}] 买入下单 {quantity}单位 @ ~{price:.2f} (订单ID: {tradeID}, 冻结: {required_funds:.2f})")
                self.last_trade_time[instrument] = current_time
            except Exception as e:
                self.evt.consoleLog(f"[{instrument}] 买入下单错误: {str(e)}")

        # ========= 卖出信号处理 =========
        elif (rsi > 70) or (macd < signal and hist < 0):
            # 检查是否有足够持仓
            if self.positions.get(instrument, 0) <= 0:
                self.evt.consoleLog(f"[{instrument}] 持仓不足: 持有{self.positions.get(instrument, 0)}单位")
                return

            # 计算可卖数量（不超过持仓量）
            quantity = min(math.floor(self.invest_amount / price), self.positions[instrument])
            quantity = max(quantity, 1)  # 至少1单位

            try:
                order = AlgoAPIUtil.OrderObject(
                    instrument=instrument,
                    openclose='close',
                    buysell=-1,
                    ordertype=0,
                    volume=quantity
                )
                tradeID = self.evt.sendOrder(order)

                # 记录订单（卖出订单不需要冻结资金）
                self.pending_orders[instrument].append({
                    "id": tradeID,
                    "type": "sell",
                    "quantity": quantity,
                    "price": price
                })

                self.evt.consoleLog(f"[{instrument}] 卖出下单 {quantity}单位 @ ~{price:.2f} (订单ID: {tradeID})")
                self.last_trade_time[instrument] = current_time
            except Exception as e:
                self.evt.consoleLog(f"[{instrument}] 卖出下单错误: {str(e)}")

    def on_orderfeed(self, of):
        """订单状态更新处理（核心资金控制）"""
        # 修复错误：使用正确的属性名
        if not hasattr(of, 'orderID'):
            self.evt.consoleLog("订单反馈对象缺少orderID属性")
            return

        instrument = of.instrument
        order_id = of.orderID

        # 查找匹配的待处理订单
        if instrument in self.pending_orders:
            for i, order in enumerate(self.pending_orders[instrument]):
                if order["id"] != order_id:
                    continue

                # ====== 订单成交处理 ======
                if of.status == 'FILLED':
                    executed_price = of.price
                    executed_volume = of.volume

                    # 买入订单成交
                    if order["type"] == "buy":
                        # 计算实际成本并释放冻结资金
                        actual_cost = executed_volume * executed_price
                        self.unfreeze_funds(order["frozen_amount"])

                        # 更新实际余额和持仓
                        self.actual_balance -= actual_cost
                        self.positions[instrument] = self.positions.get(instrument, 0) + executed_volume

                        # 检查余额安全
                        available_after = self.update_available_balance()
                        if available_after < self.min_buffer_amount:
                            self.trading_frozen = True
                            self.evt.consoleLog(
                                f"[紧急] 余额不足冻结交易! 可用余额: {available_after:.2f} < {self.min_buffer_amount}")

                        self.evt.consoleLog(
                            f"[{instrument}] 买入成交: {executed_volume}单位@{executed_price:.4f}, "
                            f"成本: {actual_cost:.2f}, 新余额: {self.actual_balance:.2f}, "
                            f"可用余额: {available_after:.2f}, 新持仓: {self.positions[instrument]}"
                        )

                    # 卖出订单成交
                    elif order["type"] == "sell":
                        # 更新余额和持仓
                        proceeds = executed_volume * executed_price
                        self.actual_balance += proceeds
                        self.positions[instrument] = self.positions.get(instrument, 0) - executed_volume

                        # 检查是否可以解除交易冻结
                        available_after = self.update_available_balance()
                        if self.trading_frozen and available_after > self.min_buffer_amount * 2:
                            self.trading_frozen = False
                            self.evt.consoleLog(f"[恢复] 余额恢复，解除交易冻结! 可用余额: {available_after:.2f}")

                        self.evt.consoleLog(
                            f"[{instrument}] 卖出成交: {executed_volume}单位@{executed_price:.4f}, "
                            f"收入: {proceeds:.2f}, 新余额: {self.actual_balance:.2f}, "
                            f"可用余额: {available_after:.2f}, 新持仓: {self.positions[instrument]}"
                        )

                    # 从待处理订单中移除
                    del self.pending_orders[instrument][i]
                    if not self.pending_orders[instrument]:
                        del self.pending_orders[instrument]
                    break

                # ====== 订单被拒绝处理 ======
                elif of.status == 'REJECTED':
                    self.evt.consoleLog(f"[{instrument}] 订单被拒绝: {of.reason}")

                    # 买入订单被拒绝：释放冻结资金
                    if order["type"] == "buy":
                        self.unfreeze_funds(order["frozen_amount"])
                        self.evt.consoleLog(f"[{instrument}] 释放冻结资金: {order['frozen_amount']:.2f}")

                    # 余额不足导致的拒绝：冻结交易
                    if "insufficient funds" in of.reason.lower() or "余额不足" in of.reason:
                        self.trading_frozen = True
                        self.evt.consoleLog("[紧急] 余额不足，冻结所有交易!")

                    # 从待处理订单中移除
                    del self.pending_orders[instrument][i]
                    if not self.pending_orders[instrument]:
                        del self.pending_orders[instrument]
                    break

    def on_dailyPLfeed(self, pl):
        """每日资金报告"""
        available_balance = self.update_available_balance()
        self.evt.consoleLog("\n" + "=" * 40)
        self.evt.consoleLog(f"当日余额报告:")
        self.evt.consoleLog(f"实际余额: {self.actual_balance:.2f} (初始: {self.initial_cash:.2f})")
        self.evt.consoleLog(f"冻结资金: {self.frozen_funds:.2f}")
        self.evt.consoleLog(f"可用余额: {available_balance:.2f}")
        self.evt.consoleLog(f"交易状态: {'冻结' if self.trading_frozen else '正常'}")
        self.evt.consoleLog(f"持仓情况: {self.positions}")
        self.evt.consoleLog("=" * 40 + "\n")
