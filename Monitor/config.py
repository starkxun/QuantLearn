"""集中配置：币种、数据源、限频、路径、字段白名单。

改标的或调节奏只改这个文件，不要把这些值散落进各个抓取脚本。
所有实测结论的来源见 docs.md 第 3 节。
"""

import os
from pathlib import Path

# ---------- 标的（固定 9 个，加币种见 docs.md 红线第 7 条）----------
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "AVAX", "ADA", "SUI"]

# 各接口的符号写法不同：现货和 Coinglass 用 BTCUSDT，LSR 两种都收
def perp(coin: str) -> str:
    return f"{coin}USDT"


PERPS = [perp(c) for c in COINS]

# ---------- 数据源 ----------
# api.binance.com / fapi.binance.com 在本机 DNS 被污染，完全不通，别再试
BASE_SPOT = "https://data-api.binance.vision"
# 手册认可域名。vip.coinglass.site 只转发 Coinglass、没有 LSR，不要用
BASE_CG = "https://api.coinglass.site"

CG_EXCHANGE = "Binance"          # Coinglass 历史接口的 exchange 参数

# ---------- 限频 ----------
# 中转站两个上游各自限 6 次/分钟，且**独立计数**
#（429 文案分别是"用户在 coinglass 的速率超限"和"用户在 lsr 的速率超限"）
# 理论间隔 10s，垫到 11s 实测 100% 成功，留 1s 给网络抖动
BUCKET_COINGLASS = "coinglass"
BUCKET_LSR = "lsr"
MIN_INTERVAL = {
    BUCKET_COINGLASS: 11.0,
    BUCKET_LSR: 11.0,
}

# 代理有丢包（同一请求时通时不通），重试是硬性要求不是优化项
MAX_RETRIES = 4
TIMEOUT = 30

# ---------- 归档节奏 ----------
# LSR 源数据约 2 分钟一变，日频快照会把信息全丢掉。
# 5 分钟一次 = 288 次/天，远在 6 次/分钟的限额内。
ARCHIVE_INTERVAL = 300


def _load_dotenv() -> None:
    """把 .env 读进环境变量。已存在的环境变量优先，不覆盖。

    自己解析而不是引 python-dotenv：就这十行，少一个依赖。
    仓库根和 Monitor/ 都找一遍，省得 key 放错地方查半天。
    """
    for path in (ROOT.parent / ".env", ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def api_key() -> str:
    """读 key。读不到直接炸，不给默认值——静默跑成无认证最难查。"""
    k = os.environ.get("COINGLASS_KEY")
    if not k:
        raise SystemExit(
            f"缺少 COINGLASS_KEY。\n"
            f"  在 {ROOT.parent / '.env'} 里写一行：COINGLASS_KEY=cg_xxx\n"
            f"别把 key 写进代码。"
        )
    return k


# ---------- 路径 ----------
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW_SPOT = DATA / "raw" / "spot"
RAW_CG = DATA / "raw" / "coinglass"
LSR_ARCHIVE = DATA / "lsr_archive"
SNAPSHOTS = DATA / "snapshots"

for _p in (RAW_SPOT, RAW_CG, LSR_ARCHIVE, SNAPSHOTS):
    _p.mkdir(parents=True, exist_ok=True)

# 导入时就把 .env 装进环境。放在这里（而不是 api_key 里）是因为
# urllib 直接读 http_proxy/https_proxy 环境变量——代理配置也走 .env，
# 无 key 的币安镜像请求同样需要它。
# 实测：本机 profile 不设代理，任务计划调起来时环境是干净的，
# 不在这里补上，定时任务会静默失败。
_load_dotenv()


# ---------- LSR 归档字段白名单 ----------
# 接口返回 75 个字段，全存下来一天 6.7MB 原始 JSON，一年扛不住。
#
# 这里留了 32 个，比 docs.md 里写的"12~15"多。理由是成本不对称：
#   多存一个字段  → parquet 里约 +5KB/天
#   漏存一个字段  → 三个月后想用时，那段历史永远补不回来（卖家不提供历史）
# 32 字段 × 9 币 × 288 次/天 ≈ 2600 行/天，parquet 约 150KB/天、50MB/年，完全可控。
# 所以宁可多存。真正砍掉的是 43 个衍生字段（各种 *_pct 字符串、*_abs），
# 它们都能从保留字段算出来。
LSR_FIELDS = [
    # 基础
    "symbol", "rank", "oi_mcap_ratio", "found",
    # 交易者模式：多空比与多周期变化率
    "ratio", "ratio_30m",
    "trader_delta_2m", "trader_delta_30m", "trader_delta_4h",
    "long_notional", "short_notional",
    # 大户模式
    "whale_ratio", "whale_delta_2m", "whale_delta_30m", "whale_delta_4h",
    "whale_long_qty", "whale_short_qty",
    "whale_long_count", "whale_short_count",
    "whale_long_avg_entry", "whale_short_avg_entry",
    # Binance overview：人数、持仓量、平均成本、盈利人数
    # 平均成本和盈利人数是币安公开 API 里没有的，这批最值得存
    "ov_total_traders", "ov_long_traders", "ov_short_traders",
    "ov_long_whales", "ov_short_whales",
    "ov_long_qty", "ov_short_qty",
    "ov_long_avg_entry", "ov_short_avg_entry",
    "ov_long_profit_traders", "ov_short_profit_traders",
]
