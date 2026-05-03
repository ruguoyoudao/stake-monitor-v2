"""Stake.com 风云榜监控 + 大额下注通知（CNY 汇率转换）"""
import sys
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

import yaml

with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

config["browser"]["headless"] = False
config["scraper"]["poll_interval"] = 30

from scraper import StakeScraper
from notifier import Notifier
from forex import to_cny, parse_amount

notifier = Notifier(config.get("notifications", {}))
ALERT_THRESHOLD_CNY = config.get("notifications", {}).get("rules", {}).get("cny_threshold", 50000)

scraper = StakeScraper(config)
scraper.start()

logger.info("=" * 60)
logger.info("Stake.com 风云榜监控 (Ctrl+C 退出)")
logger.info(f"页面: {scraper.page.url}")
logger.info(f"大额通知阈值: >= CNY{ALERT_THRESHOLD_CNY:,}")
logger.info("=" * 60)

seen_bets = set()

try:
    poll_count = 0
    while True:
        poll_count += 1
        data = scraper.fetch_data()
        timestamp = time.strftime("%H:%M:%S")

        bets = [d for d in data if d.get('type') == 'bet_feed']

        new_bets = []
        for b in bets:
            r = b.get('rawCols', [])
            # 用 event|player|time|amount 组合作为唯一 key，避免同赛事不同投注被误判重复
            key = '|'.join(r[:4]) if len(r) >= 4 else b.get('event', '') + '|' + b.get('player', '') + '|' + b.get('time', '') + '|' + b.get('amount', '')
            if key not in seen_bets:
                seen_bets.add(key)
                new_bets.append(b)

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
                enriched = scraper.extract_details_for_bets(large_bets)

                for b in enriched:
                    sl = b.get("share_link", "")
                    if sl:
                        logger.info(f"    link: {sl}")

                title = f"大额下注通知 - {timestamp}"
                formatted = []
                for b in enriched:
                    amount_raw = b.get("amount", "")
                    val, text_currency = parse_amount(amount_raw)
                    svg_cur = b.get("currency", "")
                    currency = svg_cur if (svg_cur and text_currency == "USD") else text_currency
                    # BTC/ETH 保留 2 位小数，其他币种整数显示
                    fmt = ".2f" if currency.upper() in ("BTC", "ETH") else ",.0f"
                    item = {
                        "event": b.get("event", ""),
                        "player": b.get("player", ""),
                        "time": b.get("time", ""),
                        "odds": b.get("odds", ""),
                        "amount": f"{currency} {val:{fmt}}",
                        "cny": f"{b.get('cny', 0):,.0f}",
                    }
                    sl = b.get("share_link", "")
                    if sl:
                        item["share_link"] = sl
                    formatted.append(item)

                notifier.send(title, formatted)
                logger.info(f">>> 已发送 {len(large_bets)} 条大额通知")

        time.sleep(config["scraper"]["poll_interval"])

except KeyboardInterrupt:
    logger.info("用户中断，正在退出...")
finally:
    scraper.stop()
    logger.info("监控已停止")
