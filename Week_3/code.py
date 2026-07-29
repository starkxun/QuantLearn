# ============================================================
# 第 3 周：识别"看起来赚钱"的陷阱
# 用法：粘进 QC Research notebook（research.ipynb），改 CONFIG 就能跑
#
# 为什么用 Research 而不是跑 12 次回测：
#   3 组参数 × 训练/测试 × 有费/无费 = 12 次回测，手点太慢，而且没法画参数曲面。
#   这里用向量化的方式在 pandas 里复现同一套双均线逻辑，几秒钟跑完 80+ 组参数。
#   代价是必须自己保证「不偷看未来」和「成本算对」——CELL 2 就是干这个的。
#   最后用 Week_3/main.py 在真引擎里跑 1 组做交叉验证，确认两边数字对得上。
# ============================================================

# ---------- CELL 1: 配置 + 取数 ----------
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize

qb = QuantBook()

# ---- CONFIG：你只需要改这里 ----
MARKET = Market.COINBASE
TICKER = "BTCUSD"

START = datetime(2019, 1, 1)      # 全样本起点
SPLIT = datetime(2023, 1, 1)      # 训练/测试分界线（之前=训练，之后=测试）
END   = datetime(2026, 1, 1)      # 全样本终点

# 第 1 周验证过的单边成本：手续费 0.80% + 滑点 0.05%
FEE      = 0.0080
SLIPPAGE = 0.0005
COST     = FEE + SLIPPAGE         # 单边 0.85%，一个来回 1.70%

# 作业指定必测的三组参数
CORE_PARAMS = [(5, 20), (10, 60), (20, 120)]

# 参数曲面扫描范围（用来看"好参数"是一片高原还是一根孤峰）
FAST_GRID = [3, 5, 8, 10, 15, 20, 30, 40, 50]
SLOW_GRID = [20, 30, 40, 60, 80, 100, 120, 150, 200]
# --------------------------------

symbol = qb.add_crypto(TICKER, market=MARKET).symbol
hist = qb.history(symbol, START, END, Resolution.DAILY)
close = hist.loc[symbol]["close"]
close.index = pd.to_datetime(close.index)

print(f"{TICKER} 日线 {len(close)} 根 | {close.index.min().date()} → {close.index.max().date()}")
print(f"训练区间: {START.date()} → {SPLIT.date()}   "
      f"({(close.index < SPLIT).sum()} 根)")
print(f"测试区间: {SPLIT.date()} → {END.date()}   "
      f"({(close.index >= SPLIT).sum()} 根)")
print(f"单边成本 {COST*100:.2f}%，一个来回 {COST*200:.2f}%")


# ---------- CELL 2: 向量化双均线回测引擎 ----------
# 这一格是全周的地基，三条铁律：
#   1) 均线只用过去的数据（rolling 天然满足）
#   2) 今天收盘算出的信号，最早只能赚到「明天」的收益 → held = signal.shift(1)
#   3) 每次换仓（0→1 或 1→0）扣一次单边成本

def backtest(close, fast_n, slow_n, cost=COST):
    """在全样本上跑一次双均线，返回逐日明细。切训练/测试在这之后做。

    注意：均线在全样本上滚动计算，再切片。这不算偷看未来（SMA 只看过去），
    好处是测试区间开头不会因为均线没预热而空一段。
    """
    ret  = close.pct_change().fillna(0.0)
    fast = close.rolling(fast_n).mean()
    slow = close.rolling(slow_n).mean()

    signal = (fast > slow).astype(float)
    signal[fast.isna() | slow.isna()] = np.nan   # 均线没就绪前不给信号

    held     = signal.shift(1)                   # 今天实际持有的仓位
    turnover = held.diff().abs().fillna(0.0)     # 1 = 今天换了仓
    gross    = held.fillna(0.0) * ret            # 不含成本的策略日收益
    # 成本按成交金额比例扣，所以是乘法不是减法：
    # 写成 gross - turnover*cost 会漏掉 cost×ret 的交叉项，和逐日模拟对不上
    net      = (1 + gross) * (1 - turnover * cost) - 1

    return pd.DataFrame({"close": close, "ret": ret, "held": held,
                         "turnover": turnover, "gross": gross, "net": net})


def metrics(df, cost=COST, ret_col="net"):
    """对一段区间算验收指标。df 是 backtest() 输出的切片。"""
    d = df.dropna(subset=["held"])
    if len(d) < 30:
        return None

    r  = d[ret_col]
    eq = (1 + r).cumprod()

    years = len(d) / 365.0
    total = eq.iloc[-1] - 1
    cagr  = eq.iloc[-1] ** (1 / years) - 1
    mdd   = ((eq - eq.cummax()) / eq.cummax()).min()
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan

    # 逐笔交易：held 连续为 1 的一段 = 一笔完整交易
    blk = (d["held"] != d["held"].shift()).cumsum()
    trades = []
    for _, g in d.groupby(blk):
        if g["held"].iloc[0] == 1:
            gross_r = (1 + g["gross"]).prod() - 1
            trades.append((1 + gross_r) * (1 - cost) ** 2 - 1   # 进出各扣一次
                          if ret_col == "net" else gross_r)

    wins   = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]

    # buy & hold 同区间基准
    bh_eq = (1 + d["ret"]).cumprod()

    return {
        "总收益":    total,
        "年化":      cagr,
        "最大回撤":  mdd,
        "Sharpe":    sharpe,
        "交易笔数":  len(trades),
        "胜率":      len(wins) / len(trades) if trades else np.nan,
        "盈亏比":    (np.mean(wins) / abs(np.mean(losses))
                      if wins and losses else np.nan),
        "持仓占比":  d["held"].mean(),
        "成本拖累":  d["turnover"].sum() * cost,   # 累计交易成本（占本金比例）
        "BH总收益":  bh_eq.iloc[-1] - 1,
        "_eq":       eq,
    }


def slice_period(df, start, end):
    return df.loc[(df.index >= start) & (df.index < end)]


def evaluate(fast_n, slow_n, cost=COST):
    """一组参数 → 训练/测试两段、含费/不含费两种，共 4 组指标。"""
    df = backtest(close, fast_n, slow_n, cost)
    train, test = slice_period(df, START, SPLIT), slice_period(df, SPLIT, END)
    return {
        "train_net":   metrics(train, cost, "net"),
        "train_gross": metrics(train, cost, "gross"),
        "test_net":    metrics(test,  cost, "net"),
        "test_gross":  metrics(test,  cost, "gross"),
    }


# 冒烟测试：跑一组看看数字合不合理
_r = evaluate(20, 60)
print("20/60 测试区间（含费）:", {k: round(v, 4) for k, v in _r["test_net"].items()
                                  if not k.startswith("_")})


# ---------- CELL 3: 任务①+③ 三组参数 × 训练/测试 × 有费/无费 ----------
rows = []
for f, s in CORE_PARAMS:
    r = evaluate(f, s)
    for period in ("train", "test"):
        for fee_tag, col in (("含费", "net"), ("不含费", "gross")):
            m = r[f"{period}_{col}"]
            rows.append({
                "参数":   f"{f}/{s}",
                "区间":   "训练" if period == "train" else "测试",
                "成本":   fee_tag,
                "总收益": m["总收益"],
                "年化":   m["年化"],
                "最大回撤": m["最大回撤"],
                "Sharpe": m["Sharpe"],
                "笔数":   m["交易笔数"],
                "胜率":   m["胜率"],
                "成本拖累": m["成本拖累"],
                "BH总收益": m["BH总收益"],
            })

core = pd.DataFrame(rows)
# 让参数按 CORE_PARAMS 的顺序排，不然 pivot 会按字符串排成 10/60, 20/120, 5/20
core["参数"] = pd.Categorical(core["参数"],
                              categories=[f"{f}/{s}" for f, s in CORE_PARAMS],
                              ordered=True)
pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
print("\n========== 三组参数总表 ==========")
print(core.to_string(index=False))

# 手续费吃掉多少：同参数同区间，含费 vs 不含费
print("\n========== 手续费影响 ==========")
piv = core.pivot_table(index=["参数", "区间"], columns="成本",
                       values="总收益").reset_index()
piv["被吃掉"] = piv["不含费"] - piv["含费"]
piv["吃掉占比"] = piv["被吃掉"] / piv["不含费"].abs()
print(piv.to_string(index=False))
print("\n提示：'被吃掉' ≈ 笔数 × 1.70%。如果某组参数 '含费' 由正转负，"
      "它就是本周要找的「手续费吞噬收益」案例。")


# ---------- CELL 4: 任务② 参数曲面（训练 vs 测试）----------
# 目的：看训练集上最好的参数，到了测试集还排第几名。
grid_rows = []
for f in FAST_GRID:
    for s in SLOW_GRID:
        if f >= s:
            continue
        r = evaluate(f, s)
        if r["train_net"] is None or r["test_net"] is None:
            continue
        grid_rows.append({
            "fast": f, "slow": s,
            "train_cagr": r["train_net"]["年化"],
            "test_cagr":  r["test_net"]["年化"],
            "train_sharpe": r["train_net"]["Sharpe"],
            "test_sharpe":  r["test_net"]["Sharpe"],
            "test_trades":  r["test_net"]["交易笔数"],
        })

grid = pd.DataFrame(grid_rows)
grid["train_rank"] = grid["train_cagr"].rank(ascending=False).astype(int)
grid["test_rank"]  = grid["test_cagr"].rank(ascending=False).astype(int)

best = grid.loc[grid["train_cagr"].idxmax()]
# Spearman = 先把两列各自换成排名，再算普通相关。
# 直接写 .corr(method="spearman") 要装 scipy，这么写不用依赖。
rho  = grid["train_cagr"].rank().corr(grid["test_cagr"].rank())

print(f"\n扫描 {len(grid)} 组参数")
print(f"训练集最优: {int(best.fast)}/{int(best.slow)}  "
      f"年化 {best.train_cagr*100:+.1f}%（训练第 1 名）")
print(f"  → 它在测试集: 年化 {best.test_cagr*100:+.1f}%，"
      f"排第 {int(best.test_rank)} / {len(grid)} 名")
print(f"训练/测试年化的 Spearman 秩相关: {rho:.3f}")
print("  接近 0 或为负 = 训练集的排名对测试集几乎没有预测力 = 挑参数就是在拟合噪音")

print("\n训练集前 5 名在测试集的表现：")
print(grid.nsmallest(5, "train_rank")[
    ["fast", "slow", "train_cagr", "train_rank", "test_cagr", "test_rank"]
].to_string(index=False))


# ---------- CELL 5: 画图 ----------
# 图上一律用英文标签：matplotlib 默认字体没有中文字形，中文会渲染成方块（豆腐块）。
# 解读文字放在 print 里，终端输出不受字体影响。
#
# 配色约定：
#   曲面图 = 有正负的量 → 双色发散（橙-中性灰-蓝），灰色恒定落在 0，一眼看出赚赔
#   曲线图 = 类别识别 → 固定顺序取色，绝不循环
DIVERGING = LinearSegmentedColormap.from_list(
    "quant_div", ["#eb6834", "#e8e8e6", "#2a78d6"])
DIVERGING.set_bad("#f5f5f4")                       # fast >= slow 的无效格
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]         # 类别槽位 1/2/3
INK, MUTED, GRID_C = "#0b0b0b", "#52514e", "#dcdcd8"


def style(ax, title, xlabel="", ylabel=""):
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_C)


# --- 图 1：两张参数曲面热力图，共用同一色标 ---
def surface(col):
    m = grid.pivot(index="fast", columns="slow", values=col)
    return m.reindex(index=FAST_GRID,
                     columns=SLOW_GRID)          # 保持网格顺序

t_train = surface("train_cagr") * 100            # 统一换成百分数，色标和格内数字才一致
t_test  = surface("test_cagr") * 100
vmax = np.nanmax(np.abs([t_train.values, t_test.values]))
norm = Normalize(-vmax, vmax)                    # 对称 → 0 永远是中性灰

fig, axes = plt.subplots(1, 2, figsize=(15, 6))
for ax, mat, name in zip(axes, [t_train, t_test], ["Train", "Test"]):
    im = ax.imshow(np.ma.masked_invalid(mat.values), cmap=DIVERGING,
                   norm=norm, aspect="auto")
    ax.set_xticks(range(len(SLOW_GRID)), SLOW_GRID)
    ax.set_yticks(range(len(FAST_GRID)), FAST_GRID)
    # 每格标数值 —— 热力图的数值标注同时充当"表格视图"，不靠颜色单独传达信息
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center",
                        fontsize=7, color=INK)
    style(ax, f"{name} period — annualised return % (after costs)",
          "slow MA", "fast MA")
fig.colorbar(im, ax=axes, shrink=0.8, label="annualised %")
plt.show()
print("看点：训练图里的深蓝区块，在测试图里还在不在原地？"
      "换位置 = 参数没有稳定性；只有孤零零一格深蓝 = 典型过拟合。")

# --- 图 2：训练年化 vs 测试年化散点 ---
fig, ax = plt.subplots(figsize=(7, 7))
ax.axhline(0, color=GRID_C, lw=1)
ax.axvline(0, color=GRID_C, lw=1)
ax.scatter(grid["train_cagr"] * 100, grid["test_cagr"] * 100,
           s=42, color=SERIES[0], alpha=0.75,
           edgecolor="#ffffff", linewidth=1.2)       # 2px 白环，重叠点不糊
ax.scatter([best.train_cagr * 100], [best.test_cagr * 100],
           s=150, color=SERIES[1], edgecolor="#ffffff", linewidth=2,
           zorder=5, label=f"best in train: {int(best.fast)}/{int(best.slow)}")
ax.annotate(f"  train #1 -> test #{int(best.test_rank)}",
            (best.train_cagr * 100, best.test_cagr * 100),
            color=INK, fontsize=9, va="center")
ax.legend(frameon=False, fontsize=9, labelcolor=MUTED)
style(ax, f"Every parameter pair: train vs test (Spearman rho = {rho:.2f})",
      "train annualised %", "test annualised %")
plt.show()
print("看点：点云若毫无向右上倾斜的趋势，说明「训练集好」不代表「测试集好」。")

# --- 图 3：测试区间净值曲线（含费）+ buy & hold ---
fig, ax = plt.subplots(figsize=(12, 6))
for i, (f, s) in enumerate(CORE_PARAMS):
    m = metrics(slice_period(backtest(close, f, s), SPLIT, END))
    eq = m["_eq"]
    ax.plot(eq.index, eq.values, lw=2, color=SERIES[i], label=f"{f}/{s}")
    ax.annotate(f" {f}/{s}", (eq.index[-1], eq.iloc[-1]),
                color=SERIES[i], fontsize=9, va="center")   # 端点直标

d = slice_period(backtest(close, 20, 60), SPLIT, END).dropna(subset=["held"])
bh = (1 + d["ret"]).cumprod()
ax.plot(bh.index, bh.values, lw=2, color=MUTED, ls="--", label="Buy & Hold BTC")
ax.annotate(" B&H", (bh.index[-1], bh.iloc[-1]), color=MUTED, fontsize=9, va="center")

ax.axhline(1, color=GRID_C, lw=1)
ax.legend(frameon=False, fontsize=9, labelcolor=MUTED, loc="upper left")
ax.grid(axis="y", color=GRID_C, lw=0.6)
ax.set_axisbelow(True)
style(ax, f"Test period equity (after {COST*100:.2f}% per-side cost, start = 1.0)",
      "", "equity")
plt.show()

# --- 图 4：手续费吃掉多少 ---
fig, ax = plt.subplots(figsize=(10, 5))
sub = piv[piv["区间"] == "测试"].reset_index(drop=True)
x = np.arange(len(sub))
ax.bar(x - 0.21, sub["不含费"] * 100, width=0.40, color=SERIES[0], label="before costs")
ax.bar(x + 0.21, sub["含费"] * 100, width=0.40, color=SERIES[1], label="after costs")
for xi, (g, nt) in enumerate(zip(sub["不含费"], sub["含费"])):
    ax.text(xi - 0.21, g * 100, f"{g*100:.0f}", ha="center",
            va="bottom" if g >= 0 else "top", fontsize=9, color=INK)
    ax.text(xi + 0.21, nt * 100, f"{nt*100:.0f}", ha="center",
            va="bottom" if nt >= 0 else "top", fontsize=9, color=INK)
ax.axhline(0, color=INK, lw=1)
ax.set_xticks(x, sub["参数"])
ax.legend(frameon=False, fontsize=9, labelcolor=MUTED)
style(ax, "Test period total return: before vs after trading costs",
      "parameters (fast/slow)", "total return %")
plt.show()


# ---------- CELL 6: 结论自检 ----------
print("\n========== 过关自检 ==========")
overfit = int(best.test_rank) > len(grid) * 0.3 or rho < 0.3
print(f"[{'✔' if overfit else '✘'}] 过拟合证据："
      f"训练第 1 的 {int(best.fast)}/{int(best.slow)} 在测试集排 {int(best.test_rank)}/{len(grid)}，"
      f"秩相关 ρ={rho:.2f}")

killed = piv[(piv["不含费"] > 0) & (piv["含费"] <= 0)]
print(f"[{'✔' if len(killed) else '✘'}] 手续费吞噬证据：")
if len(killed):
    for _, r in killed.iterrows():
        print(f"      {r['参数']} 在{r['区间']}区间：不含费 {r['不含费']*100:+.1f}% "
              f"→ 含费 {r['含费']*100:+.1f}%，被成本翻面")
else:
    worst = piv.loc[piv["吃掉占比"].idxmax()]
    print(f"      没有直接翻面的，但 {worst['参数']}（{worst['区间']}）被吃掉 "
          f"{worst['被吃掉']*100:.1f} 个百分点，占毛收益 {worst['吃掉占比']*100:.0f}%")
    print("      想造出翻面案例：把 FAST_GRID 往小调（如 2/5/8），高频参数换手多，最容易被成本打死")
