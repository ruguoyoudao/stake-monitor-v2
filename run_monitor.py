"""Stake.com 风云榜监控 + 大额下注本地存储 + 跟注聚类企业微信通知"""
import sys
import os
import re
import logging, time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("monitor.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("monitor")

# 加载 .env 文件中的环境变量
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import yaml

with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# 解析 ${ENV_VAR} 环境变量
def _resolve_env(obj):
    if isinstance(obj, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _resolve_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env(v) for v in obj]
    return obj
config = _resolve_env(config)

from scraper import StakeScraper
from notifier import Notifier
from forex import to_cny, parse_amount

notifier = Notifier(config.get("notifications", {}))
ALERT_THRESHOLD_CNY = config.get("notifications", {}).get("rules", {}).get("cny_threshold", 50000)
CLUSTER_CFG = config.get("clustering", {})
CLUSTER_ENABLED = CLUSTER_CFG.get("enabled", False)
CLUSTER_MIN_COUNT = CLUSTER_CFG.get("min_count", 3)
CLUSTER_STEP = CLUSTER_CFG.get("step", 1)
CLUSTER_WINDOW_HRS = CLUSTER_CFG.get("window_hours", 24)

scraper = StakeScraper(config)
scraper.start()

logger.info("=" * 60)
logger.info("Stake.com 风云榜监控 - 大额投注本地存储模式 (Ctrl+C 退出)")
logger.info(f"页面: {scraper.page.url}")
logger.info(f"大额阈值: >= CNY{ALERT_THRESHOLD_CNY:,}")
if CLUSTER_ENABLED:
    window_info = f"窗口{CLUSTER_WINDOW_HRS}h" if CLUSTER_WINDOW_HRS > 0 else "不限时"
    logger.info(f"跟注聚类: 同(event+market+outcome)累积{CLUSTER_MIN_COUNT}条通知, 之后每+{CLUSTER_STEP}条再次通知 ({window_info})")
logger.info("=" * 60)

# seen_bets 持久化：从文件恢复，首次静默采集，防止重启重复记录
import json as _json
SEEN_FILE = "seen_bets.json"
LARGE_BETS_FILE = "large_bets.json"
CLUSTER_ALERTS_FILE = "cluster_alerts.json"
seen_bets = set()
cluster_alerts = {}  # {cluster_key: last_notified_count}
if os.path.exists(SEEN_FILE):
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as _f:
            seen_bets = set(_json.load(_f))
        logger.info(f"已恢复 {len(seen_bets)} 条历史投注记录")
    except Exception:
        pass
if os.path.exists(CLUSTER_ALERTS_FILE):
    try:
        with open(CLUSTER_ALERTS_FILE, "r", encoding="utf-8") as _f:
            cluster_alerts = _json.load(_f)
        logger.info(f"已恢复 {len(cluster_alerts)} 条聚类通知记录")
    except Exception:
        pass
first_poll_done = len(seen_bets) > 0  # 有历史记录时首次静默

def _save_seen():
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as _f:
            _json.dump(list(seen_bets), _f)
    except Exception:
        pass

def _save_large_bets(new_entries: list[dict]):
    """将大额投注追加到本地 JSON 文件"""
    existing = []
    if os.path.exists(LARGE_BETS_FILE):
        try:
            with open(LARGE_BETS_FILE, "r", encoding="utf-8") as _f:
                existing = _json.load(_f)
        except Exception:
            pass
    existing.extend(new_entries)
    with open(LARGE_BETS_FILE, "w", encoding="utf-8") as _f:
        _json.dump(existing, _f, ensure_ascii=False, indent=2)
    logger.info(f">>> 已保存 {len(new_entries)} 条大额投注至 {LARGE_BETS_FILE} (共 {len(existing)} 条)")

def _save_cluster_alerts():
    try:
        with open(CLUSTER_ALERTS_FILE, "w", encoding="utf-8") as _f:
            _json.dump(cluster_alerts, _f, ensure_ascii=False)
    except Exception:
        pass

def _check_clusters():
    """加载 large_bets.json，按 (event+market+outcome) 分组检测跟注聚集"""
    if not os.path.exists(LARGE_BETS_FILE):
        return

    try:
        with open(LARGE_BETS_FILE, "r", encoding="utf-8") as _f:
            all_bets = _json.load(_f)
    except Exception:
        return

    # 按 (event, market, outcome) 分组，过滤空字段
    # 时间窗口过滤：仅统计最近 N 小时内的投注
    cutoff = ""
    if CLUSTER_WINDOW_HRS > 0:
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.localtime(time.time() - CLUSTER_WINDOW_HRS * 3600),
        )

    groups: dict[str, list[dict]] = {}
    for b in all_bets:
        if cutoff and b.get("saved_at", "") < cutoff:
            continue
        ev = b.get("event", "").strip()
        mk = b.get("market", "").strip()
        oc = b.get("outcome", "").strip()
        if not ev or not mk or not oc:
            continue
        key = f"{ev}|{mk}|{oc}"
        groups.setdefault(key, []).append(b)

    new_alerts = []
    for key, items in groups.items():
        count = len(items)
        if count < CLUSTER_MIN_COUNT:
            continue
        last_count = cluster_alerts.get(key, 0)
        if count <= last_count:
            continue

        # 每增加 step 条通知一次：min_count, min_count+step, min_count+2*step ...
        # 首次通知: count >= min_count && last_count < min_count
        # 后续: count - last_count >= step
        if last_count == 0:
            # 首次达到阈值
            eligible = True
        else:
            eligible = (count - last_count) >= CLUSTER_STEP

        if not eligible:
            continue

        new_alerts.append((key, items, count))
        cluster_alerts[key] = count

    if new_alerts:
        _save_cluster_alerts()
        for key, items, count in new_alerts:
            parts = key.split("|", 2)
            event_name = parts[0]
            market_name = parts[1] if len(parts) > 1 else ""
            outcome_name = parts[2] if len(parts) > 2 else ""

            # 汇总金额（样本取第一条的货币格式）
            total_cny = sum(b.get("amount_cny", 0) for b in items)
            players = [b.get("player", "?") for b in items]
            links = [b.get("share_link", "") for b in items if b.get("share_link")]
            latest_odds = items[-1].get("odds", "")

            title = f"跟注预警 - {event_name}"
            players = [b.get("player", "?") for b in items]

            notifier.send_cluster_alert(title, {
                "title": title,
                "event": event_name,
                "market": market_name,
                "outcome": outcome_name,
                "count": count,
                "players": players,
                "total_cny": sum(b.get("amount_cny", 0) for b in items),
                "latest_odds": items[-1].get("odds", ""),
            })
            logger.info(
                f">>> 跟注预警已发送: {event_name} | {market_name} | {outcome_name} "
                f"x{count} players"
            )

zero_bets_streak = 0          # 连续 0 投注次数
stale_streak = 0              # 连续无新投注次数
scrape_err_streak = 0         # 连续数据提取失败次数
ANOMALY_THRESHOLD = 10        # 连续 N 次异常触发日志警告

try:
    poll_count = 0
    while True:
        poll_count += 1
        data = scraper.fetch_data(types=["bet_feed"])
        timestamp = time.strftime("%H:%M:%S")

        bets = [d for d in data if d.get('type') == 'bet_feed']

        new_bets = []
        for b in bets:
            r = b.get('rawCols', [])
            # rawCols: [event, player, time, odds, amount] — 用前4列作为唯一 key
            key = '|'.join(r[:4]) if len(r) >= 4 else b.get('event', '') + '|' + b.get('player', '') + '|' + b.get('time', '') + '|' + b.get('amount', '')
            if key not in seen_bets:
                seen_bets.add(key)
                if first_poll_done:  # 首次恢复时静默，不触发记录
                    new_bets.append(b)
        if not first_poll_done:
            first_poll_done = True
            _save_seen()

        logger.info(f"[{timestamp}] #{poll_count} bets={len(bets)}(new={len(new_bets)})")

        if new_bets:
            logger.info("  --- 风云榜 ---")
            large_bets = []
            for b in new_bets:
                amount_raw = b.get('amount', '')
                svg_currency = b.get('currency', '')  # 从 SVG icon 提取的币种
                amount_cny = to_cny(amount_raw, svg_currency)
                amount_val, text_currency = parse_amount(amount_raw)
                currency = svg_currency if (svg_currency and text_currency == "USD") else text_currency
                event = b.get('event', '')

                # 跳过未知币种（to_cny 返回 0）
                if amount_cny == 0 and amount_val > 0:
                    logger.info(f"    skip(unknown currency): {currency} {amount_raw} | {event[:30]}")
                    continue

                flag = " >>>" if amount_cny >= ALERT_THRESHOLD_CNY else ""
                if amount_cny >= ALERT_THRESHOLD_CNY:
                    # 过滤：跳过复式投注
                    if '复式' in event:
                        logger.info(f"    skip(复式): {event[:30]}")
                    else:
                        large_bets.append({**b, "cny": amount_cny})

                # 币种展示：USD 省略前缀，非 USD 显示币种；原料含字母时附加
                amount_disp = f"{currency} {amount_val:,.0f}" if currency != "USD" else f"{amount_val:,.0f}"
                raw_has_alpha = bool(re.search(r"[A-Za-z]", amount_raw))
                raw_hint = f" [raw: {amount_raw}]" if currency != "USD" or raw_has_alpha else ""
                logger.info(f"  [{b.get('time','')}] {b.get('player','')} | {b.get('odds','')}x | {amount_disp} (CNY{amount_cny:,.0f}){flag}{raw_hint} | {event[:35]}")

            if large_bets:
                # 点击弹窗获取分享链接 + 玩法 + 下注结果
                enriched = scraper.extract_details_for_bets(large_bets)

                entries = []
                for b in enriched:
                    amount_raw = b.get("amount", "")
                    val, text_currency = parse_amount(amount_raw)
                    svg_cur = b.get("currency", "")
                    currency = svg_cur if (svg_cur and text_currency == "USD") else text_currency
                    fmt = ".2f" if currency.upper() in ("BTC", "ETH") else ",.0f"
                    entry = {
                        "event": b.get("event", ""),
                        "player": b.get("player", ""),
                        "time": b.get("time", ""),
                        "odds": b.get("odds", ""),
                        "amount": f"{currency} {val:{fmt}}",
                        "amount_cny": round(b.get("cny", 0)),
                        "market": b.get("market", ""),
                        "outcome": b.get("outcome", ""),
                        "share_link": b.get("share_link", ""),
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    logger.info(
                        f"    market={b.get('market','')} outcome={b.get('outcome','')}"
                    )
                    entries.append(entry)

                _save_large_bets(entries)

                if CLUSTER_ENABLED:
                    _check_clusters()

        # 数据提取异常检测（data 完全为空 = scrape 失败）
        if len(data) == 0:
            scrape_err_streak += 1
            zero_bets_streak = 0
            stale_streak = 0
            if scrape_err_streak >= ANOMALY_THRESHOLD and scrape_err_streak % ANOMALY_THRESHOLD == 0:
                logger.warning(f"!!! 数据提取异常: 连续 {scrape_err_streak} 轮提取失败")
        elif len(bets) == 0:
            zero_bets_streak += 1
            stale_streak = 0
            if zero_bets_streak >= ANOMALY_THRESHOLD and zero_bets_streak % ANOMALY_THRESHOLD == 0:
                logger.warning(f"!!! 数据异常: 连续 {zero_bets_streak} 轮无投注数据")
        elif len(new_bets) == 0:
            zero_bets_streak = 0
            stale_streak += 1
            if stale_streak >= ANOMALY_THRESHOLD and stale_streak % ANOMALY_THRESHOLD == 0:
                logger.warning(f"!!! 数据异常: 连续 {stale_streak} 轮无新投注，feed 可能停滞")
        else:
            zero_bets_streak = 0
            stale_streak = 0
            scrape_err_streak = 0

        _save_seen()
        time.sleep(config["scraper"]["poll_interval"])

except KeyboardInterrupt:
    logger.info("用户中断，正在退出...")
finally:
    scraper.stop()
    logger.info("监控已停止")
