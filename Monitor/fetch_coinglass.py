"""Coinglass 历史指标抓取：funding / OI / 大户多空比 / 主动买卖量。

这四个都有 2019~2020 至今的日线历史，**可回测**——这是它们和 LSR 信号
（只有当前快照）的本质区别，也是它们值得进面板的唯一理由。见 docs.md 第 1 节判据。

设计要点：

1. **单次请求返回全历史**（BTC funding 2332 条回溯到 2020-03-30），所以不需要
   增量逻辑，每次全量覆盖。省掉最容易写错的一块代码。

2. **原始值和归一化值都存。** funding 源头是「百分数 / 每个计息周期」
   （0.008579 表示 0.0086%），Gate.io 那种源返回的却是小数——单位不统一是这类
   项目最经典的暗坑。这里统一加一列年化小数，但**不删原始列**，
   以后发现换算假设错了还能重算。

3. **降级**：某个币某个指标拉失败时，沿用该币已存的历史，不让整个任务崩，
   也不把已有数据抹掉。

用法：
    python fetch_coinglass.py               # 全部 4 个指标 × 9 币，约 7 分钟
    python fetch_coinglass.py funding_rate  # 只跑指定指标
"""

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from config import BASE_CG, BUCKET_COINGLASS, CG_EXCHANGE, COINS, RAW_CG, perp
from http_util import ApiError, check_coinglass, get_json

LIMIT = 4500          # 够覆盖最长历史（BTC OI 2364 条）

# ---- funding 年化假设 ----
# 币安主流永续是 8 小时一次计息，即每天 3 次。
# 注意这是**假设**：极端行情下币安会把部分交易对临时改成 4h/1h，
# 那些时段按 3 次/天折算会低估。所以原始百分数列一定要留着。
FUNDING_PER_DAY = 3
DAYS = 365

METRICS = {
    "funding_rate": {
        "path": "/api/futures/funding-rate/history",
        "desc": "资金费率（百分数/计息周期）",
    },
    "open_interest": {
        "path": "/api/futures/open-interest/history",
        "desc": "未平仓合约（USD）",
    },
    "top_ls_ratio": {
        "path": "/api/futures/top-long-short-position-ratio/history",
        "desc": "大户持仓多空比",
    },
    "taker_volume": {
        "path": "/api/futures/taker-buy-sell-volume/history",
        "desc": "主动买卖量（USD）",
    },
}


def fetch_one(metric: str, coin: str) -> pd.DataFrame:
    """拉一个币一个指标的全历史。"""
    payload = check_coinglass(
        get_json(BASE_CG, METRICS[metric]["path"], BUCKET_COINGLASS,
                 params={"exchange": CG_EXCHANGE, "symbol": perp(coin),
                         "interval": "1d", "limit": LIMIT}),
        f"{metric}/{coin}",
    )
    data = payload.get("data") or []
    if not data:
        raise ApiError(f"{metric}/{coin} 返回空 data")

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["coin"] = coin
    df = df.drop(columns="time")

    # 数值列一律转 float；接口偶尔混用 str 和 number（实测 OI 的 close 出现过裸 float）
    for c in df.columns:
        if c not in ("date", "coin"):
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if metric == "funding_rate":
        # 归一化：百分数/周期 → 年化小数。原始 OHLC 列保留不动。
        #
        # 两个口径，因为 1d 这根 K 线是**当天 3 次结算的 OHLC 聚合**：
        #   _close = 用当天最后一次结算的费率。是"日末瞬时水平"，
        #            对"当前拥挤程度"这类状态判断合适，但会漏掉当天前两次结算。
        #   _mid   = 用 (high+low)/2 估计当天的中枢水平。对"这一天平均有多贵"
        #            更有代表性，抗日末归零的噪音。
        # 实测过 BNB：日末常年归零（币安把该对利率设为 0，溢价在 ±0.05% 带内时
        # funding 恰好为 0），只看 _close 会把 49% 的天数误判成"零成本"。
        # 要精确的"当日实际支付总额"，得拉 8h 明细逐笔求和 —— 见 docs.md 6.2c。
        k = FUNDING_PER_DAY * DAYS / 100
        df["funding_annualized"] = df["close"] * k
        df["funding_annualized_mid"] = (df["high"] + df["low"]) / 2 * k

    return df.sort_values("date").reset_index(drop=True)


def update_metric(metric: str) -> tuple[int, list[str]]:
    """抓一个指标的全部 9 个币，写成一个 parquet。返回 (行数, 失败的币)。"""
    path = RAW_CG / f"{metric}.parquet"
    old = pd.read_parquet(path) if path.exists() else None

    frames, failed = [], []
    for coin in COINS:
        try:
            frames.append(fetch_one(metric, coin))
            print(f"    {coin:<5} ok", flush=True)
        except ApiError as e:
            failed.append(coin)
            print(f"    {coin:<5} 失败：{str(e)[:90]}", flush=True)
            # 降级：沿用已存的历史，别把旧数据也弄丢
            if old is not None and coin in set(old["coin"]):
                frames.append(old[old["coin"] == coin])
                print(f"    {coin:<5} → 沿用已存历史", flush=True)

    if not frames:
        return 0, failed

    out = (pd.concat(frames, ignore_index=True)
             .drop_duplicates(subset=["coin", "date"])
             .sort_values(["coin", "date"])
             .reset_index(drop=True))

    tmp = path.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(path)
    return len(out), failed


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取 Coinglass 历史指标")
    ap.add_argument("metrics", nargs="*", help=f"指标名，默认全部：{list(METRICS)}")
    args = ap.parse_args()

    targets = args.metrics or list(METRICS)
    unknown = set(targets) - set(METRICS)
    if unknown:
        sys.exit(f"未知指标 {unknown}，可选：{list(METRICS)}")

    n_req = len(targets) * len(COINS)
    print(f"{len(targets)} 个指标 × {len(COINS)} 个币 = {n_req} 次请求")
    print(f"限频 6 次/分钟，串行节奏 11s，预计约 {n_req * 11 / 60:.1f} 分钟\n")

    started = datetime.now(timezone.utc)
    all_failed = {}
    for metric in targets:
        print(f"[{metric}] {METRICS[metric]['desc']}")
        rows, failed = update_metric(metric)
        if failed:
            all_failed[metric] = failed
        print(f"  → {rows} 行写入 {metric}.parquet\n")

    mins = (datetime.now(timezone.utc) - started).total_seconds() / 60
    print(f"耗时 {mins:.1f} 分钟")

    if all_failed:
        # 降级不是成功，但也不该让整个流程崩 —— 明确报出来让人看见
        print(f"\n⚠ 有失败（已沿用旧数据降级）：{all_failed}")
        print("  面板可以继续跑，但这些币的该指标不是最新的。")
    else:
        print("✔ 全部成功")


if __name__ == "__main__":
    main()
