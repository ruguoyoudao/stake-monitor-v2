"""
汇率转换模块 - 将各币种投注额转换为 CNY
"""
import re
import time
import logging
import json

logger = logging.getLogger(__name__)

# 常用货币符号 -> 货币代码
SYMBOL_MAP = {
    "₹": "INR", "CA$": "CAD", "MX$": "MXN", "ARS": "ARS",
    "A$": "AUD", "C$": "CAD", "NZ$": "NZD", "HK$": "HKD",
    "S$": "SGD", "R$": "BRL", "₽": "RUB", "¥": "JPY",
    "₩": "KRW", "CHF": "CHF", "€": "EUR", "£": "GBP",
    "₿": "BTC", "฿": "THB", "₺": "TRY", "zł": "PLN",
    "Kč": "CZK", "Ft": "HUF", "kr": "SEK", "RM": "MYR",
    "Rp": "IDR", "₱": "PHP", "฿": "THB",
}

# 缓存汇率
_rates_cache: dict = {}
_rates_time: float = 0
CACHE_TTL = 3600  # 缓存 1 小时

# 缓存加密货币价格 (USD)
_crypto_cache: dict = {}
_crypto_time: float = 0
CRYPTO_TTL = 300  # 缓存 5 分钟


def _fetch_crypto_prices():
    """获取 BTC/ETH 的 USD 价格（CoinGecko）"""
    global _crypto_cache, _crypto_time

    if _crypto_cache and time.time() - _crypto_time < CRYPTO_TTL:
        return _crypto_cache

    try:
        import urllib.request
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": "StakeMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            btc = data.get("bitcoin", {}).get("usd", 0)
            eth = data.get("ethereum", {}).get("usd", 0)
            _crypto_cache = {"BTC": btc, "ETH": eth}
            _crypto_time = time.time()
            logger.info(f"Crypto prices: BTC=${btc}, ETH=${eth}")
            return _crypto_cache
    except Exception as e:
        logger.warning(f"获取加密货币价格失败: {e}")
        if not _crypto_cache:
            _crypto_cache = {"BTC": 85000, "ETH": 1800}
            _crypto_time = time.time()
            logger.warning("使用硬编码加密货币价格作为回退")
        return _crypto_cache


def _fetch_rates():
    """获取最新汇率（USD 基准），失败重试 3 次"""
    global _rates_cache, _rates_time

    if _rates_cache and time.time() - _rates_time < CACHE_TTL:
        return _rates_cache

    import urllib.request

    url = "https://api.exchangerate-api.com/v4/latest/USD"
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                _rates_cache = data.get("rates", {})
                _rates_time = time.time()
                logger.info(f"汇率已更新, CNY/USD={_rates_cache.get('CNY', 'N/A')}")
                return _rates_cache
        except Exception as e:
            logger.warning(f"获取汇率失败 (attempt {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(2)

    # 回退：使用硬编码汇率
    if not _rates_cache:
        _rates_cache = _hardcoded_rates()
        _rates_time = time.time()
        logger.warning("使用硬编码汇率作为回退")
    return _rates_cache


def _hardcoded_rates() -> dict:
    """硬编码汇率（2026/05 近似值）"""
    return {
        "USD": 1.0, "CNY": 6.84, "EUR": 0.92, "GBP": 0.79,
        "INR": 86.5, "CAD": 1.38, "MXN": 19.4, "ARS": 1381,
        "AUD": 1.53, "NZD": 1.67, "HKD": 7.78, "SGD": 1.34,
        "BRL": 5.7, "RUB": 98, "JPY": 152, "KRW": 1380,
        "CHF": 0.88, "THB": 35.5, "TRY": 36, "PLN": 3.9,
        "CZK": 22.8, "HUF": 370, "SEK": 10.5, "MYR": 4.45,
        "IDR": 16300, "PHP": 57.5,
    }


def parse_amount(amount_str: str) -> tuple[float, str]:
    """
    解析投注额字符串
    返回: (数值, 币种代码)
    """
    if not amount_str:
        return 0, "USD"

    s = amount_str.strip()

    # 1. 匹配符号前缀
    for symbol, code in SYMBOL_MAP.items():
        if s.startswith(symbol):
            val = re.sub(r"[^\d.]", "", s[len(symbol):])
            try:
                result = (float(val), code)
                logger.debug(f"parse_amount [{amount_str}] -> step1(symbol) {result}")
                return result
            except ValueError:
                return 0, code

    # 2. 纯数字（可能是 USDT/USD 等值）
    if re.match(r"^[\d,.]+$", s):
        val = float(s.replace(",", ""))
        logger.debug(f"parse_amount [{amount_str}] -> step2(pure_num) USD {val}")
        return val, "USD"

    # 2.5. 代码前缀 (如 "ARS 1,709,000", "INR100000", "BTC 0.01")
    m = re.match(r"^([A-Za-z]{3,5})\s*([\d,.]+)$", s)
    if m:
        try:
            code = m.group(1).upper()
            val = float(m.group(2).replace(",", ""))
            result = (val, code)
            logger.debug(f"parse_amount [{amount_str}] -> step2.5(prefix) {result}")
            return result
        except ValueError:
            pass

    # 3. 代码后缀 (如 "100.00 USDT", "0.01 eth")
    m = re.match(r"^([\d,.]+)\s*([A-Za-z]{3,4})$", s)
    if m:
        try:
            code = m.group(2).upper()
            result = (float(m.group(1).replace(",", "")), code)
            logger.debug(f"parse_amount [{amount_str}] -> step3(suffix) {result}")
            return result
        except ValueError:
            pass

    # 4. 嵌套关键词匹配 (大小写不敏感)
    s_upper = s.upper()
    for code in ["USDT", "BTC", "ETH", "USD", "EUR"]:
        if code in s_upper:
            val = re.findall(r"[\d,.]+", s)
            if val:
                try:
                    clean = val[0].replace(",", "")
                    result = (float(clean), code)
                    logger.debug(f"parse_amount [{amount_str}] -> step4(keyword) {result}")
                    return result
                except ValueError:
                    pass

    # 5. 尝试提取任何数字
    nums = re.findall(r"[\d,.]+", s)
    if nums:
        try:
            val = float(nums[-1].replace(",", ""))
            logger.debug(f"parse_amount [{amount_str}] -> step5(fallback) USD {val}")
            return val, "USD"
        except ValueError:
            pass

    return 0, "USD"


def to_cny(amount_str: str, hint_currency: str = "") -> float:
    """
    将投注额转换为人民币，仅支持 USD/USDT/USDC/BTC/ETH
    hint_currency: SVG icon 提取的币种，优先使用
    返回 0 表示不支持该币种（应跳过）
    """
    val, currency = parse_amount(amount_str)
    if hint_currency:
        currency = hint_currency
    if val <= 0:
        return 0

    cur = currency.upper()

    # 稳定币 → 视为 USD
    if cur in ("USDT", "USDC", "BUSD", "DAI", "TUSD"):
        cur = "USD"

    # 只支持 USD 和 BTC/ETH
    if cur != "USD" and cur not in ("BTC", "ETH"):
        return 0

    rates = _fetch_rates()
    cny = rates.get("CNY", 6.84)

    # USD 路径
    if cur == "USD":
        return round(val * cny, 2)

    # BTC/ETH 路径
    crypto = _fetch_crypto_prices()
    price = crypto.get(cur, 0)
    if price > 0:
        return round(val * price * cny, 2)

    return 0
