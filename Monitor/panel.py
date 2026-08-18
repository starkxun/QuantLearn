"""每日面板：把第 1~3 层拼成一张表，落快照 + 出一份人看的报告。

**这一层只做汇总和呈现，不做任何判断。** 不输出"该买/该卖"，也不输出
"当前是趋势/震荡"——状态标签属于 Day 8 的 regime filter，而且要等 Day 7
验证过的因子才能用。见 docs.md 红线第 1、3 条。

快照纪律：`data/snapshots/YYYY-MM-DD.parquet` **一天一个，永不覆盖**。
重复跑同一天默认跳过（`--force` 才覆盖）。这份归档三个月后才显出价值，
现在每一次静默覆盖都是在毁掉未来的样本。

用法：
    python panel.py                 # 算最新一天，落快照 + 打印报告
    python panel.py --date 2026-08-10
    python panel.py --force         # 覆盖已存在的快照
    python panel.py --no-save       # 只看不写
"""

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from config import COINS, DATA, RAW_CG, SNAPSHOTS

IND = DATA / "indicators.parquet"
CS_MARKET = DATA / "cross_section_market.parquet"
CS_COIN = DATA / "cross_section_coin.parquet"

# 面板里每个币要展示的列，顺序即报告里的列顺序
COIN_COLS = [
    "close", "ret",
    "er20", "dist_ma20", "dist_ma60", "ma20_vs_ma60",
    "donchian20", "pos_1y", "mom_1m", "mom_3m",
    "atr_pct", "rv30", "rv30_pct", "dd_ath",
    "rel_btc_chg20", "mom3m_rank", "mom3m_rank_chg20",
    "funding_ann", "funding_ann_mid", "oi_usd", "oi_chg20",
    "ls_ratio", "taker_imbalance",
]
MARKET_COLS = ["avg_corr60", "dispersion", "mkt_ret",
               "breadth_ma60", "breadth_ma60_n", "breadth_ma60_pct",
               "breadth_ma200", "breadth_ma200_n", "breadth_ma200_pct"]


def _need(path):
    if not path.exists():
        sys.exit(f"缺少 {path.name}，先跑对应的脚本（见 README 第 2 节）")
    return pd.read_parquet(path)


def build(date: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.Series, pd.Timestamp]:
    ind = _need(IND)
    mkt = _need(CS_MARKET)
    csc = _need(CS_COIN)

    # 面板日期锚定在**现货最后一根已收盘 K 线**上。
    # Coinglass 那几个指标当天就有值，但那是没走完的一天，混进来会让快照不可复现。
    last = ind["date"].max()
    date = pd.Timestamp(date, tz="UTC") if date is not None else last
    if date > last:
        sys.exit(f"{date:%Y-%m-%d} 超出现货数据范围（最新 {last:%Y-%m-%d}）")

    df = ind[ind["date"] == date].set_index("coin")
    if df.empty:
        sys.exit(f"{date:%Y-%m-%d} 没有指标数据（可能是数据起始日之前）")

    c = csc[csc["date"] == date].set_index("coin")
    for col in ("rel_btc_chg20", "mom3m_rank", "mom3m_rank_chg20"):
        df[col] = c[col] if col in c else pd.NA

    # ---- Coinglass 四件套 ----
    f = _need(RAW_CG / "funding_rate.parquet")
    f = f[f["date"] == date].set_index("coin")
    df["funding_ann"] = f.get("funding_annualized")
    df["funding_ann_mid"] = f.get("funding_annualized_mid")

    oi = _need(RAW_CG / "open_interest.parquet")
    oi_now = oi[oi["date"] == date].set_index("coin")["close"]
    # 20 日前的 OI，用来看杠杆是在堆积还是在出清
    prev_d = date - pd.Timedelta(days=20)
    oi_prev = oi[oi["date"] == prev_d].set_index("coin")["close"]
    df["oi_usd"] = oi_now
    df["oi_chg20"] = (oi_now / oi_prev - 1) if len(oi_prev) else pd.NA

    ls = _need(RAW_CG / "top_ls_ratio.parquet")
    ls = ls[ls["date"] == date].set_index("coin")
    df["ls_ratio"] = ls.get("top_position_long_short_ratio")

    tv = _need(RAW_CG / "taker_volume.parquet")
    tv = tv[tv["date"] == date].set_index("coin")
    if len(tv):
        b, s = tv["taker_buy_volume_usd"], tv["taker_sell_volume_usd"]
        df["taker_imbalance"] = (b - s) / (b + s)   # >0 = 主动买占优
    else:
        df["taker_imbalance"] = pd.NA

    panel = df.reindex(COINS)[COIN_COLS]

    m = mkt[mkt["date"] == date]
    market = m.iloc[0][MARKET_COLS] if len(m) else pd.Series(dtype=float, index=MARKET_COLS)

    return panel, market, date


# ---------------- 呈现 ----------------
def _pct(x, d=1):
    return "—" if pd.isna(x) else f"{x*100:+.{d}f}%"


def render(panel: pd.DataFrame, market: pd.Series, date: pd.Timestamp) -> str:
    L = [f"# 市场状态面板 · {date:%Y-%m-%d}（UTC 收盘）", ""]

    L += ["## 市场级", "",
          "| 指标 | 值 | 说明 |", "|---|---|---|"]
    ac = market.get("avg_corr60")
    n_eff = len(COINS) / (1 + (len(COINS) - 1) * ac) if pd.notna(ac) else float("nan")
    L += [f"| 平均两两相关(60d) | {ac:.3f} | 有效标的数 N_eff = **{n_eff:.2f}** |",
          f"| 横截面离散度 | {_pct(market.get('dispersion'), 2)} | 低=纯 beta，选币无意义 |",
          f"| 等权市场当日收益 | {_pct(market.get('mkt_ret'), 2)} | |",
          f"| 站上 MA60 | {market.get('breadth_ma60'):.0f}/{market.get('breadth_ma60_n'):.0f}"
          f" ({_pct(market.get('breadth_ma60_pct'), 0)}) | |",
          f"| 站上 MA200 | {market.get('breadth_ma200'):.0f}/{market.get('breadth_ma200_n'):.0f}"
          f" ({_pct(market.get('breadth_ma200_pct'), 0)}) | |", ""]

    L += ["## 趋势与波动", "",
          "| 币 | 收盘 | 当日 | ER20 | vs MA20 | vs MA60 | 20日区间 | 1年区间 | 3M动量 | ATR% | 波动分位 | 距ATH |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in COINS:
        r = panel.loc[c]
        L.append(
            f"| {c} | {r['close']:,.4f} | {_pct(r['ret'], 2)} | {r['er20']:.2f} | "
            f"{_pct(r['dist_ma20'])} | {_pct(r['dist_ma60'])} | {r['donchian20']:.2f} | "
            f"{r['pos_1y']:.2f} | {_pct(r['mom_3m'])} | {r['atr_pct']*100:.2f} | "
            f"{r['rv30_pct']:.2f} | {_pct(r['dd_ath'])} |")

    L += ["", "## 横截面与衍生品", "",
          "| 币 | 3M排名 | 排名变动 | 近20日vsBTC | funding年化(close/mid) | OI | OI 20日变动 | 大户多空比 | 主买占优 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for c in COINS:
        r = panel.loc[c]
        rk = "—" if pd.isna(r["mom3m_rank"]) else f"{r['mom3m_rank']:.0f}"
        rc = "—" if pd.isna(r["mom3m_rank_chg20"]) else f"{r['mom3m_rank_chg20']:+.0f}"
        oi = "—" if pd.isna(r["oi_usd"]) else f"{r['oi_usd']/1e9:.2f}B"
        L.append(
            f"| {c} | {rk} | {rc} | {_pct(r['rel_btc_chg20'])} | "
            f"{_pct(r['funding_ann'])} / {_pct(r['funding_ann_mid'])} | {oi} | "
            f"{_pct(r['oi_chg20'])} | {r['ls_ratio'] if pd.notna(r['ls_ratio']) else '—'} | "
            f"{_pct(r['taker_imbalance'], 2)} |")

    L += ["", "---", "",
          "> 本面板**只呈现数据，不做判断**。状态标签和告警属于 Day 8，",
          "> 且只允许接入 Day 7 验证通过的因子。见 docs.md 红线第 1、3 条。",
          "",
          "> funding 两个口径经常不一致甚至符号相反（见 docs.md 6.2c），",
          "> 两个都列出来就是提醒：别只看一个。", ""]
    return "\n".join(L)


def save(panel: pd.DataFrame, market: pd.Series, date: pd.Timestamp,
         report: str, force: bool) -> bool:
    pq = SNAPSHOTS / f"{date:%Y-%m-%d}.parquet"
    md = SNAPSHOTS / f"{date:%Y-%m-%d}.md"

    if pq.exists() and not force:
        print(f"⏭  {pq.name} 已存在，跳过（--force 可覆盖）")
        return False

    out = panel.reset_index().rename(columns={"index": "coin"})
    out.insert(0, "date", date)
    for k in MARKET_COLS:                       # 市场级字段随行复制，一个文件读全
        out[k] = market.get(k)
    out["generated_at"] = datetime.now(timezone.utc)

    tmp = pq.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(pq)
    md.write_text(report, encoding="utf-8")
    print(f"✔ 快照 → {pq.name} / {md.name}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="每日面板")
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD，默认最新")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的快照")
    ap.add_argument("--no-save", action="store_true", help="只打印不落盘")
    args = ap.parse_args()

    panel, market, date = build(args.date)
    report = render(panel, market, date)
    print(report)
    if not args.no_save:
        save(panel, market, date, report, args.force)


if __name__ == "__main__":
    main()
