"""
风云榜投注详情批量采集 - 依次打开每个投注弹窗，保存弹窗信息到本地
不修改 StakeScraper 核心代码，仅通过公开/内部方法调用
"""
import sys
import os
import re
import time
import json
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("capture_bets.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("capture")

import yaml

with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

from scraper import StakeScraper

# ---------------------------------------------------------------------------
# 输出文件
# ---------------------------------------------------------------------------
OUTPUT_FILE = "captured_bets.json"
PROGRESS_FILE = "capture_progress.json"


def load_progress() -> set:
    """加载已采集的投注 key，支持断点续采"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("completed_keys", []))
        except Exception:
            pass
    return set()


def save_progress(completed_keys: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"completed_keys": list(completed_keys), "updated": datetime.now().isoformat()}, f, ensure_ascii=False)


def load_existing_results() -> list[dict]:
    """加载已有采集结果"""
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_results(results: list[dict]):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {OUTPUT_FILE} ({len(results)} 条)")


def make_bet_key(bet: dict) -> str:
    """生成投注唯一标识"""
    r = bet.get("rawCols", [])
    if len(r) >= 4:
        return "|".join(r[:4])
    return "|".join([
        bet.get("event", ""),
        bet.get("player", ""),
        bet.get("time", ""),
        bet.get("amount", ""),
    ])


def extract_bets_from_feed(scraper: StakeScraper) -> list[dict]:
    """从风云榜提取所有投注（复用 _extract_bet_feed 逻辑）"""
    data = scraper.fetch_data()
    return [d for d in data if d.get("type") == "bet_feed"]


def open_bet_and_capture(scraper: StakeScraper, bet: dict) -> dict | None:
    """
    点击一条投注，打开弹窗，采集弹窗全部信息
    复用 scraper._open_bet_detail 做点击+校验+分享链接，
    额外补充弹窗完整文本/HTML 等字段。
    """
    detail = scraper._open_bet_detail(bet)
    if not detail:
        return None

    # 弹窗仍打开中，补充采集完整文本和 HTML
    modal_full_text = scraper.page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const m of modals) {
            const t = (m.innerText || '').trim();
            if (t.includes('ID')) return t;
        }
        return '';
    }""")

    modal_html = scraper.page.evaluate("""() => {
        const modals = document.querySelectorAll(
            '[class*="fixed"][class*="justify-center"]'
        );
        for (const m of modals) {
            const t = (m.innerText || '').trim();
            if (t.includes('ID')) return m.outerHTML.substring(0, 5000);
        }
        return '';
    }""")

    # Bet ID
    bet_id = ""
    id_match = re.search(r"ID\s*([\d,]+)", modal_full_text or "")
    if id_match:
        bet_id = id_match.group(1).replace(",", "")

    # 结构字段（已在 _open_bet_detail 中提取并与 feed 比对过）
    modal_info = scraper._extract_modal_info()

    result = {
        "bet_id": bet_id,
        "feed_event": bet.get("event", ""),
        "feed_player": bet.get("player", ""),
        "feed_time": bet.get("time", ""),
        "feed_odds": bet.get("odds", ""),
        "feed_amount": bet.get("amount", ""),
        "feed_currency": bet.get("currency", ""),
        "modal_event": modal_info.get("event", ""),
        "modal_player": modal_info.get("player", ""),
        "modal_odds": modal_info.get("odds", ""),
        "modal_amount": modal_info.get("amount", ""),
        "modal_market": detail.get("market", ""),
        "modal_outcome": detail.get("outcome", ""),
        "modal_full_text": modal_full_text,
        "modal_html_snippet": modal_html,
        "share_link": detail.get("share_link", ""),
        "captured_at": datetime.now().isoformat(),
    }

    scraper._dismiss_detail_panel()
    return result


def main():
    logger.info("=" * 60)
    logger.info("风云榜投注详情批量采集")
    logger.info(f"目标: {config['target']['url']}")
    logger.info(f"输出: {OUTPUT_FILE}")
    logger.info("=" * 60)

    # 断点续采
    completed_keys = load_progress()
    results = load_existing_results()
    if completed_keys:
        logger.info(f"断点续采: 已完成 {len(completed_keys)} 条，已保存 {len(results)} 条")

    scraper = StakeScraper(config)
    scraper.start()
    logger.info(f"页面已加载: {scraper.page.url}")

    try:
        # 提取风云榜投注
        bets = extract_bets_from_feed(scraper)
        if not bets:
            logger.warning("未提取到任何风云榜投注数据，请确认页面已加载完成且风云榜 tab 已激活")
            logger.info("尝试手动点击风云榜 tab...")
            scraper._click_bets_tab()
            time.sleep(3)
            bets = extract_bets_from_feed(scraper)

        logger.info(f"风云榜投注总数: {len(bets)}")

        pending_bets = []
        for bet in bets:
            key = make_bet_key(bet)
            if key not in completed_keys:
                pending_bets.append((key, bet))

        logger.info(f"待采集: {len(pending_bets)} / 已采集: {len(completed_keys)}")

        for idx, (key, bet) in enumerate(pending_bets):
            logger.info(f"[{idx + 1}/{len(pending_bets)}] event={bet.get('event','')[:40]} player={bet.get('player','')[:15]}")

            # 确保没有残留弹窗
            scraper._dismiss_detail_panel()
            time.sleep(0.5)

            captured = open_bet_and_capture(scraper, bet)
            if captured:
                captured["bet_key"] = key
                results.append(captured)
                completed_keys.add(key)
                logger.info(f"  ✓ bet_id={captured['bet_id']} share_link={'yes' if captured['share_link'] else 'no'}")
            else:
                # 即使采集失败也标记为完成（避免死循环）
                completed_keys.add(key)
                logger.info("  ✗ 采集失败")

            # 每 5 条保存一次进度
            if (idx + 1) % 5 == 0 or idx == len(pending_bets) - 1:
                save_results(results)
                save_progress(completed_keys)
                logger.info(f"  进度: {len(completed_keys)}/{len(bets)}")

            # 间隔，避免触发反爬
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        save_results(results)
        save_progress(completed_keys)
        scraper.stop()
        logger.info(f"采集结束，共获取 {len(results)} 条弹窗信息，保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
