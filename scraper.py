"""
数据采集模块 - 基于 Playwright 采集 Stake.com 投注数据
"""

import time
import logging
from playwright.sync_api import sync_playwright, Page, Browser
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)


class StakeScraper:
    def __init__(self, config: dict):
        self.config = config
        self.target_url = config["target"]["url"]
        self.browser_cfg = config["browser"]
        self.scraper_cfg = config["scraper"]
        self._pw_context = None
        self._managed_pw = None
        self._playwright = None
        self._stealth = None
        self.browser: Browser | None = None
        self._context = None
        self.page: Page | None = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def start(self):
        """启动浏览器并导航到目标页面（支持持久化登录 / CDP 连接已有浏览器）"""
        self._stealth = Stealth(
            chrome_runtime=True,
            navigator_webdriver=True,
            navigator_languages=True,
            navigator_permissions=True,
            navigator_plugins=True,
            navigator_user_agent=True,
            webgl_vendor=True,
        )
        self._pw_context = sync_playwright()
        self._managed_pw = self._stealth.use_sync(self._pw_context)
        self._playwright = self._managed_pw.__enter__()

        cdp_port = self.browser_cfg.get("cdp_port", 0)

        if cdp_port > 0:
            self._connect_via_cdp(cdp_port)
        else:
            self._launch_browser()

        # CDP 模式优先复用已有页面，否则创建新页面
        if cdp_port > 0 and self._context.pages:
            self.page = self._context.pages[-1]
            logger.info(f"复用已有页面: {self.page.url}")
        else:
            self.page = self._context.new_page()
        logger.info(f"正在打开: {self.target_url}")

        nav_retries = self.browser_cfg.get("nav_retries", 3)
        for attempt in range(1, nav_retries + 1):
            try:
                self.page.goto(
                    self.target_url,
                    timeout=self.browser_cfg.get("timeout", 60000),
                    wait_until="domcontentloaded",
                )
                logger.info(f"导航完成 (attempt {attempt})")
                break
            except Exception as e:
                logger.warning(f"导航尝试 {attempt}/{nav_retries} 失败: {e}")
                if attempt < nav_retries:
                    time.sleep(5)

        self._wait_for_page_ready()
        logger.info("浏览器启动完成，开始监控...")

    def refresh_page(self):
        """刷新当前页面"""
        if not self.page:
            return
        try:
            self.page.reload(timeout=30000, wait_until='domcontentloaded')
            logger.info("页面已刷新")
        except Exception as e:
            logger.warning(f"页面刷新失败: {e}")


    def _connect_via_cdp(self, port: int):
        """通过 CDP 连接已有的浏览器实例"""
        endpoint = f"http://127.0.0.1:{port}"
        try:
            self.browser = self._playwright.chromium.connect_over_cdp(endpoint)
            logger.info(f"CDP 连接成功: {endpoint}, 浏览器版本: {self.browser.version}")
            contexts = self.browser.contexts
            if contexts:
                self._context = contexts[0]
                logger.info(f"复用已有 context, 已打开 {len(self._context.pages)} 个页面")
            else:
                self._context = self.browser.new_context()
                logger.info("创建新 context")
        except Exception as e:
            raise RuntimeError(
                f"CDP 连接失败 (端口 {port})。请先以调试模式启动浏览器:\n"
                f"  1. 关闭所有 Edge 窗口\n"
                f'  2. 运行: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port={port}\n'
                f"  3. 在新开的 Edge 中打开 stake.com 并登录\n"
                f"  4. 重新运行本程序"
            ) from e

    def _launch_browser(self):
        """启动新浏览器实例"""
        user_data_dir = self.browser_cfg.get("user_data_dir", "")

        context_opts = {
            "viewport": {
                "width": self.browser_cfg.get("viewport_width", 1920),
                "height": self.browser_cfg.get("viewport_height", 1080),
            },
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        }
        if self.browser_cfg.get("locale"):
            context_opts["locale"] = self.browser_cfg["locale"]
        if self.browser_cfg.get("timezone_id"):
            context_opts["timezone_id"] = self.browser_cfg["timezone_id"]

        if user_data_dir:
            from pathlib import Path
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=self.browser_cfg.get("headless", False),
                channel="chrome",
                slow_mo=self.browser_cfg.get("slow_mo", 100),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-dev-shm-usage",
                ],
                **context_opts,
            )
            self.browser = None
        else:
            launch_opts = {
                "headless": self.browser_cfg.get("headless", False),
                "channel": "chrome",
                "slow_mo": self.browser_cfg.get("slow_mo", 100),
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-dev-shm-usage",
                ],
            }
            proxy_url = self.browser_cfg.get("proxy", "")
            if proxy_url:
                launch_opts["proxy"] = {"server": proxy_url}
            self.browser = self._playwright.chromium.launch(**launch_opts)
            self._context = self.browser.new_context(**context_opts)

    def _wait_for_page_ready(self):
        """等待页面就绪，处理 Cloudflare 等安全验证"""
        max_wait = self.scraper_cfg.get("ready_timeout", 120)
        wait_selector = self.scraper_cfg.get("wait_for_selector", "")
        start = time.time()

        while time.time() - start < max_wait:
            try:
                title = (self.page.title() or "").strip()
            except Exception:
                time.sleep(3)
                continue

            if title and "just a moment" in title.lower():
                logger.info("Cloudflare 验证中，等待完成...")
                time.sleep(5)
                continue

            if wait_selector:
                try:
                    count = self.page.query_selector_all(wait_selector)
                    if count:
                        logger.info(f"检测到 {len(count)} 个目标元素: {wait_selector}")
                        return
                except Exception:
                    pass

            try:
                body = self.page.query_selector("body")
                if body:
                    body_text = body.inner_text() or ""
                    if len(body_text) > 100:
                        logger.info(f"页面加载完成，title={title[:50]}, body_length={len(body_text)}")
                        return
            except Exception:
                pass

            time.sleep(3)

        title = ""
        try:
            title = self.page.title()
        except Exception:
            pass
        logger.warning(f"等待页面就绪超时 ({max_wait}s)，当前 title={title[:80] if title else 'unknown'}")

    def fetch_data(self, types: list[str] | None = None) -> list[dict]:
        """从页面提取投注数据（赔率 + 玩家投注流水）
        types: 可选过滤，如 ['bet_feed'] 仅提取风云榜
        """
        if not self.page:
            return []

        results = []
        try:
            url = self.page.url
        except Exception:
            return results

        try:
            include_sports = types is None or "sports_events" in types
            include_feed = types is None or "bet_feed" in types

            if "sports" in url:
                if include_sports:
                    results.extend(self._extract_sports_events())
                if include_feed:
                    results.extend(self._extract_bet_feed())
            elif "casino" in url:
                results.extend(self._extract_casino_events())

            if not results:
                results.extend(self._extract_generic_data())

        except Exception as e:
            logger.error(f"数据提取异常: {e}")

        return results

    def _extract_bet_feed(self) -> list[dict]:
        """提取底部'风云榜'投注数据
        列顺序: 联赛 | 玩家 | 时间 | 赔率 | 投注额
        """
        try:
            self._click_bets_tab()

            feed = self.page.evaluate("""() => {
                const results = [];
                const seen = new Set();

                const rows = document.querySelectorAll('tr');
                rows.forEach(tr => {
                    const cells = tr.querySelectorAll('td');
                    if (cells.length < 4) return;

                    const texts = Array.from(cells).map(td => (td.innerText || td.textContent || '').trim()).filter(t => t.length > 0);
                    if (texts.length < 4) return;

                    const rowKey = texts.join('|');
                    if (seen.has(rowKey)) return;

                    const hasOdds = texts.some(t => /^\\d{1,4}\\.\\d{2,3}$/.test(t));
                    if (!hasOdds) return;

                    seen.add(rowKey);

                    // 列: event, player, time, odds, amount
                    const event = texts[0] || '';
                    const player = texts.length > 1 ? texts[1] : '';
                    const time = texts.length > 2 ? texts[2] : '';
                    const odds = texts.slice(-2)[0] || '';
                    const amount = texts.slice(-1)[0] || '';

                    // 从最后一列 SVG icon 提取币种代码 (data-ds-icon="ETH")
                    const lastTd = cells[cells.length - 1];
                    const svg = lastTd.querySelector('svg');
                    const currency = svg ? (svg.getAttribute('data-ds-icon') || '').toUpperCase() : '';

                    // 解析投注额数值（去除货币符号和逗号）
                    const amountNum = parseFloat(amount.replace(/[^\\d.]/g, '')) || 0;

                    results.push({
                        event: event,
                        player: player,
                        time: time,
                        odds: /^\\d/.test(odds) ? odds : '',
                        amount: amount,
                        currency: currency,
                        amount_num: amountNum,
                        rawCols: texts
                    });
                });
                return results.slice(0, 100);
            }""")

            return [{"type": "bet_feed", **item} for item in feed]
        except Exception as e:
            logger.debug(f"风云榜提取失败: {e}")
            return []

    def _click_bets_tab(self):
        """确保'风云榜'tab 处于激活状态"""
        try:
            self.page.evaluate("""() => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if ((btn.textContent || '').trim() === '风云榜') {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        break;
                    }
                }
            }""")
            self.page.click("button:has-text('风云榜')", timeout=3000)
        except Exception:
            try:
                self.page.click("span:has-text('风云榜')", timeout=3000)
            except Exception:
                pass

    def _find_bet_row(self, bet: dict) -> dict | None:
        """在风云榜表格中定位匹配的 tr 行（event/player/time/odds/amount 五字段精确匹配，单次 evaluate 避免 DOM 变化）"""
        raw = bet.get("rawCols", [])
        if len(raw) < 5:
            event = bet.get("event", "")
            player = bet.get("player", "")
            search_fields = [event, player, "", "", ""]
            use_exact = False
        else:
            search_fields = raw[:5]
            use_exact = True
        try:
            return self.page.evaluate("""([cols, exact]) => {
                const rows = document.querySelectorAll('tr');
                for (let i = 0; i < rows.length; i++) {
                    const tds = rows[i].querySelectorAll('td');
                    if (tds.length < 5) continue;
                    const texts = Array.from(tds).map(td => (td.innerText || td.textContent || '').trim()).filter(t => t.length > 0);
                    if (texts.length < 5) continue;
                    const matched = exact
                        ? (texts[0] === cols[0] && texts[1] === cols[1] &&
                           texts[2] === cols[2] && texts[3] === cols[3] &&
                           texts[4] === cols[4])
                        : (texts[0].includes(cols[0]) && texts[1].includes(cols[1]));
                    if (!matched) continue;
                    // 同时检测触发器，避免二次 evaluate
                    const firstTd = tds[0];
                    const btn = firstTd.querySelector('button');
                    if (btn) {
                        return {rowIndex: i, trigger: 'button',
                            btnText: (btn.textContent || '').trim().substring(0, 40)};
                    }
                    const anchor = firstTd.querySelector('a');
                    if (anchor) {
                        return {rowIndex: i, trigger: 'anchor',
                            anchorHref: anchor.getAttribute('href') || ''};
                    }
                    return {rowIndex: i, trigger: 'td'};
                }
                return null;
            }""", [search_fields, use_exact])
        except Exception:
            return None

    def _get_event_url_from_modal(self) -> str:
        """从当前弹窗 DOM 的 <a href="/sports/..."> 提取事件 URL"""
        return self.page.evaluate("""() => {
            const modals = document.querySelectorAll(
                '[class*="fixed"][class*="justify-center"]'
            );
            for (const m of modals) {
                if (!(m.innerText || '').includes('ID')) continue;
                const a = m.querySelector('a[href*="/sports/"]');
                if (a) {
                    const href = a.getAttribute('href') || '';
                    if (href.includes('/sports/') && !href.includes('iid='))
                        return href.startsWith('http') ? href : 'https://stake.com' + href;
                }
            }
            return '';
        }""")

    def _get_event_url_via_tab(self, share_link: str, event_name: str, timeout: float = 15) -> str:
        """Open share link in a new tab, click event name to get full event page URL.

        Falls back to DOM scanning first, then clicks event name to navigate.
        Returns the path-style event URL (with /sports/{category}/...) or empty string.
        """
        if not share_link or not event_name:
            return ''

        if not self._context:
            return ''
        new_page = None
        try:
            new_page = self._context.new_page()
            new_page.goto(share_link, timeout=20000, wait_until='domcontentloaded')

            # Wait for bet modal to appear
            for _ in range(20):
                has_modal = new_page.evaluate("""() => {
                    const modals = document.querySelectorAll(
                        '[class*="fixed"][class*="justify-center"]'
                    );
                    for (const m of modals) {
                        if ((m.innerText || '').includes('ID')) return true;
                    }
                    return false;
                }""")
                if has_modal:
                    break
                time.sleep(0.5)

            # Try DOM href first
            event_url = new_page.evaluate("""() => {
                const all = document.querySelectorAll('a[href*="/sports/"]');
                for (const a of all) {
                    const href = a.getAttribute('href') || '';
                    if (href.includes('/sports/') && !href.includes('iid=') && !href.includes('modal=bet')) {
                        return href;
                    }
                }
                return '';
            }""")

            if event_url:
                if not event_url.startswith('http'):
                    event_url = 'https://stake.com' + event_url
                return event_url

            # Fallback: click event name to navigate to event page
            new_page.evaluate("""(name) => {
                const modals = document.querySelectorAll(
                    '[class*="fixed"][class*="justify-center"]'
                );
                for (const modal of modals) {
                    const text = (modal.innerText || '').trim();
                    if (!text.includes('ID')) continue;
                    const all = modal.querySelectorAll('*');
                    for (const el of all) {
                        const t = (el.textContent || '').trim();
                        if (t === name || (t.length > 10 && name.length > 10 && t.includes(name) && el.tagName !== 'BODY')) {
                            el.click();
                            return;
                        }
                    }
                }
            }""", event_name)

            # Wait for navigation (URL change to sports path)
            start = time.time()
            while time.time() - start < timeout:
                url = new_page.url
                if '/sports/' in url and 'iid=' not in url and 'modal=bet' not in url and 'home?' not in url:
                    return url
                time.sleep(0.5)

            return ''
        except Exception as e:
            logger.info(f"获取事件URL异常: {e}")
            return ''
        finally:
            if new_page:
                try:
                    new_page.close()
                except Exception:
                    pass


    def _dismiss_detail_panel(self):
        """关闭详情面板，并等待确认已消失"""
        try:
            self.page.evaluate("""() => {
                const selectors = [
                    '[class*="drawer"]', '[class*="sheet"]', '[class*="panel"]',
                    '[class*="detail"]', '[class*="bet-slip"]', '[class*="modal"]',
                    '[class*="overlay"]'
                ];
                for (const sel of selectors) {
                    const panels = document.querySelectorAll(sel);
                    for (const p of panels) {
                        const text = (p.innerText || '').trim();
                        if (text.length < 30) continue;
                        const closeBtn = p.querySelector(
                            'button[aria-label*="close" i], button[class*="close" i], ' +
                            'svg[class*="close" i], [class*="close"], ' +
                            'button[class*="dismiss" i], [aria-label*="Close" i]'
                        );
                        if (closeBtn) { closeBtn.click(); return true; }
                    }
                }
                return false;
            }""")
            time.sleep(0.3)
        except Exception:
            pass
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            pass
        # 等待弹窗实际消失
        for _ in range(10):
            exists = self.page.evaluate("""() => {
                const modals = document.querySelectorAll(
                    '[class*="fixed"][class*="justify-center"]'
                );
                for (const m of modals) {
                    if ((m.innerText || '').includes('ID')) return true;
                }
                return false;
            }""")
            if not exists:
                break
            time.sleep(0.3)

    def _get_share_link_from_detail(self, timeout: float = 10) -> str:
        """从详情弹窗中通过复制按钮获取分享链接（拦截 clipboard）"""
        # 安装 clipboard 拦截器（仅一次，不在循环内重复安装）
        self.page.evaluate("""() => {
            if (window.__capture_installed) return;
            window.__captured_share_url = null;
            window.__capture_installed = true;
            const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
            navigator.clipboard.writeText = (text) => {
                window.__captured_share_url = text;
                return orig(text);
            };
        }""")

        start = time.time()
        while time.time() - start < timeout:
            try:
                # 查找 bet detail 弹窗中的"复制"按钮（通常有两个：第2个是分享链接）
                btn_info = self.page.evaluate("""() => {
                    const modals = document.querySelectorAll(
                        '[class*="fixed"][class*="justify-center"]'
                    );
                    for (const modal of modals) {
                        const text = (modal.innerText || '').trim();
                        if (!text.includes('ID')) continue;
                        const all = modal.querySelectorAll('*');
                        const shareBtns = [];
                        for (const btn of all) {
                            if ((btn.textContent || '').trim() === '复制') {
                                shareBtns.push(true);
                            }
                        }
                        return {found: true, count: shareBtns.length};
                    }
                    return {found: false, count: 0};
                }""")

                if not btn_info.get("found"):
                    time.sleep(1)
                    continue

                # 依次点击每个"复制"按钮，检查捕获的 URL 哪个是分享链接
                #    分享链接格式: /sports/home?iid=...&modal=bet
                captured_url = ''
                total_btns = btn_info.get("count", 0)

                for btn_idx in range(total_btns):
                    self.page.evaluate("""(idx) => {
                        const modals = document.querySelectorAll(
                            '[class*="fixed"][class*="justify-center"]'
                        );
                        for (const modal of modals) {
                            const text = (modal.innerText || '').trim();
                            if (!text.includes('ID')) continue;
                            let count = 0;
                            const all = modal.querySelectorAll('*');
                            for (const el of all) {
                                if ((el.textContent || '').trim() === '复制') {
                                    if (count === idx) {
                                        el.click();
                                        return;
                                    }
                                    count++;
                                }
                            }
                        }
                    }""", btn_idx)
                    time.sleep(0.3)

                    captured = self.page.evaluate(
                        "() => window.__captured_share_url"
                    )
                    if captured and 'modal=bet' in captured:
                        return captured
                    if captured and captured.startswith('http') and not captured_url:
                        captured_url = captured

                if captured_url:
                    return captured_url

                # 如果 clipboard 没拦截到，从弹窗提取 Bet ID 构造分享链接
                fallback = self.page.evaluate("""() => {
                    const modals = document.querySelectorAll(
                        '[class*="fixed"][class*="justify-center"]'
                    );
                    for (const modal of modals) {
                        const text = (modal.innerText || '').trim();
                        if (!text.includes('ID')) continue;
                        const idMatch = text.match(/ID\\s*([\\d,]+)/);
                        if (idMatch) {
                            const betId = idMatch[1].replace(/,/g, '');
                            return 'https://stake.com/sports/home?iid=sport%3A' + betId + '&source=link_shared&modal=bet';
                        }
                    }
                    return '';
                }""")
                if fallback:
                    return fallback

            except Exception as e:
                logger.info(f"获取分享链接异常: {e}")

            time.sleep(1)

        return ''

    def _extract_modal_info(self) -> dict:
        """从当前 bet detail 弹窗中提取赛事/玩家/赔率/投注额/玩法/下注结果

        弹窗文本结构（单关投注）:
            状态行... → [market] → [outcome] → [inline_odds] → 赔率 → [赔率值] → 投注额 → [金额]
        market/outcome 无标签行，靠"赔率"标签的相对位置反推:
            market = lines[赔率_idx - 3], outcome = lines[赔率_idx - 2]
        """
        return self.page.evaluate("""() => {
            const modals = document.querySelectorAll(
                '[class*="fixed"][class*="justify-center"]'
            );
            for (const modal of modals) {
                const text = (modal.innerText || '').trim();
                if (!text.includes('ID')) continue;
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                let player = '', event = '', odds = '', amount = '';
                let market = '', outcome = '';

                // 找"赔率"/"Odds"标签行（反推 market/outcome 的锚点）
                let oddsLabelIdx = -1;
                for (let i = lines.length - 1; i >= 0; i--) {
                    const ln = lines[i];
                    if (ln === '赔率' || ln === 'Odds' || ln === 'odds') {
                        oddsLabelIdx = i;
                        break;
                    }
                }

                for (let i = 0; i < lines.length; i++) {
                    if (lines[i].includes('放置在') || lines[i].includes('Placed by')) {
                        player = (lines[i + 1] || '');
                    }
                    // 时间行（如 "下午6:13 2026/5/2"）的下一行是赛事名
                    if (/\\d{1,2}[:.]\\d{2}\\s+\\d{4}/.test(lines[i]) && i + 1 < lines.length) {
                        event = (lines[i + 1] || '');
                    }
                    // 赔率标签 → 下一行是赔率值
                    if ((lines[i] === '赔率' || lines[i] === 'Odds' || lines[i] === 'odds') && i + 1 < lines.length) {
                        odds = lines[i + 1];
                    }
                    // 投注额标签 → 下一行是金额
                    if ((lines[i] === '投注额' || lines[i] === 'Stake' || lines[i] === 'Amount') && i + 1 < lines.length) {
                        amount = lines[i + 1];
                    }
                }

                // 基于赔率标签反推 market/outcome（单关结构: market → outcome → inline_odds → 赔率）
                if (oddsLabelIdx >= 3) {
                    const rawMkt = lines[oddsLabelIdx - 3] || '';
                    const rawOut = lines[oddsLabelIdx - 2] || '';
                    // 验证：market 不应该是纯数字、比分、过长文本
                    const isNum = /^[\\d.,]+$/.test(rawMkt);
                    const isScore = /^\\d{1,2}[-:]\\d{1,2}$/.test(rawMkt);
                    const tooLong = rawMkt.length > 80;
                    if (!isNum && !isScore && !tooLong && rawMkt !== rawOut) {
                        market = rawMkt;
                        outcome = rawOut;
                    }
                }

                return {event: event, player: player, odds: odds, amount: amount, market: market, outcome: outcome};
            }
            return {event: '', player: '', odds: '', amount: '', market: '', outcome: ''};
        }""")

    def _open_bet_detail(self, bet: dict) -> dict:
        """点击风云榜某行的赛事链接，打开详情面板并提取分享链接+玩法+结果

        首次调用通过 _find_bet_row 定位行并缓存到 bet['_cached_row']。
        重试（extract_details_for_bets 二次调用）时复用缓存，避免 DOM 变化导致定位漂移。
        """
        # 优先使用缓存的行定位（来自上一次失败调用）
        row_info = bet.pop('_cached_row', None)
        if row_info is None:
            row_info = self._find_bet_row(bet)

        if not row_info:
            logger.info(
                f"未找到匹配行: {bet.get('event','')[:40]} | "
                f"{bet.get('player','')[:15]}"
            )
            return {}

        # 缓存行信息，供 extract_details_for_bets 重试时复用
        bet['_cached_row'] = row_info

        try:
            trigger = row_info.get("trigger", "td")
            row_idx = row_info.get("rowIndex", 0)

            # 用 Playwright 原生点击（处理滚动、可见性等）
            if trigger == "button":
                btn = self.page.locator("tr").nth(row_idx) \
                    .locator("td").first.locator("button").first
                btn.scroll_into_view_if_needed()
                btn.click(timeout=5000)
            else:
                anchor = self.page.locator("tr").nth(row_idx) \
                    .locator("td").first.locator("a").first
                if anchor.count() > 0:
                    anchor.scroll_into_view_if_needed()
                    anchor.click(timeout=5000)
                else:
                    td = self.page.locator("tr").nth(row_idx) \
                        .locator("td").first
                    td.scroll_into_view_if_needed()
                    td.click(timeout=5000)

            logger.info(f"点击完成: {bet.get('event','')[:40]}")
        except Exception as e:
            logger.info(f"点击赛事链接失败: {e}")
            return {}

        # 等待新弹窗出现（含 Bet ID），最多 40×0.5s=20s
        for _ in range(40):
            has_modal = self.page.evaluate("""() => {
                const modals = document.querySelectorAll(
                    '[class*="fixed"][class*="justify-center"]'
                );
                for (const m of modals) {
                    if ((m.innerText || '').includes('ID')) return true;
                }
                return false;
            }""")
            if has_modal:
                break
            time.sleep(0.5)

        # 核对弹窗内容是否与当前投注匹配（赔率 + 金额 2 项必须匹配）
        expected_odds = bet.get('odds', '')[:6]
        expected_amount = bet.get('amount', '')[:10]
        for verify_attempt in range(2):
            modal_info = self._extract_modal_info()
            odds_ok = expected_odds and expected_odds in modal_info.get('odds', '')
            amount_ok = expected_amount and expected_amount in modal_info.get('amount', '')
            all_ok = odds_ok and amount_ok
            if all_ok:
                break
            if verify_attempt == 0:
                logger.info(
                    f"弹窗不匹配, 重试: expect odds='{expected_odds}' amount='{expected_amount}' "
                    f"got odds='{modal_info.get('odds','')}' amount='{modal_info.get('amount','')}' "
                    f"match={odds_ok}/{amount_ok}"
                )
                self._dismiss_detail_panel()
                time.sleep(0.5)
                # 重新点击
                try:
                    btn = self.page.locator("tr").nth(row_idx) \
                        .locator("td").first.locator("button").first
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=5000)
                except Exception:
                    pass
                time.sleep(2)
            else:
                # 记录完整弹窗内容便于排查
                modal_full = self.page.evaluate("""() => {
                    const modals = document.querySelectorAll(
                        '[class*="fixed"][class*="justify-center"]'
                    );
                    for (const m of modals) {
                        const t = (m.innerText || '').trim();
                        if (t.includes('ID')) return t.substring(0, 500);
                    }
                    return '';
                }""")
                logger.warning(
                    f"弹窗不匹配, 跳过: expect odds='{expected_odds}' amount='{expected_amount}' "
                    f"got odds='{modal_info.get('odds','')}' amount='{modal_info.get('amount','')}' "
                    f"match={odds_ok}/{amount_ok} "
                    f"rawCols={bet.get('rawCols', [])} modalText={modal_full}"
                )
                self._dismiss_detail_panel()
                return {}

        share_link = self._get_share_link_from_detail()
        if share_link:
            logger.info(f"获取分享链接: {share_link}")

        # 提取玩法和下注结果（弹窗仍打开中）
        final_info = self._extract_modal_info()
        market = final_info.get('market', '')
        outcome = final_info.get('outcome', '')

        event_url = self._get_event_url_from_modal()

        self._dismiss_detail_panel()
        bet.pop('_cached_row', None)
        return {"share_link": share_link, "market": market, "outcome": outcome, "event_url": event_url}

    def extract_details_for_bets(self, bets: list[dict]) -> list[dict]:
        """对一批投注获取分享链接+玩法+结果（失败重试1次）"""
        results = []
        for bet in bets:
            # 先清除可能残留的弹窗
            self._dismiss_detail_panel()
            detail = self._open_bet_detail(bet)
            if not detail:
                logger.info(
                    f"首次获取投注详情失败，1秒后重试: "
                    f"{bet.get('event','')[:30]}"
                )
                time.sleep(1)
                self._dismiss_detail_panel()
                detail = self._open_bet_detail(bet)
            linkshare = detail.get("share_link", "") if detail else ""
            merged = {
                **bet,
                "share_link": linkshare,
                "market": detail.get("market", "") if detail else "",
                "outcome": detail.get("outcome", "") if detail else "",
            "event_url": detail.get("event_url", "") if detail else "",
            }
            if not linkshare:
                logger.info(
                    f"未获取到分享链接: event={bet.get('event','')[:40]} "
                    f"player={bet.get('player','')} amount={bet.get('amount','')}"
                )
            if detail.get("market") or detail.get("outcome"):
                logger.info(
                    f"玩法/结果: market={detail.get('market','')} outcome={detail.get('outcome','')}"
                )
            results.append(merged)
            bet.pop('_cached_row', None)  # 确保缓存不污染后续逻辑
        return results

    def _extract_sports_events(self) -> list[dict]:
        """提取体育赛事数据（精准定位投注市场）"""
        return self.page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            // 精准提取：每个 .outcomes 容器是一个投注市场（含队伍名+赔率）
            const markets = document.querySelectorAll('.outcomes');
            markets.forEach(market => {
                if (market.closest('nav, header, footer, [class*="sidebar"], [class*="menu"]'))
                    return;

                const items = [];
                market.querySelectorAll('.outcome-content').forEach(el => {
                    const text = el.innerText.trim();
                    // 匹配队伍名 + 赔率（如 "TeamA\\n1.40"）
                    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                    if (lines.length >= 1) {
                        const name = lines[0];
                        const odds = lines.length > 1 ? parseFloat(lines[lines.length - 1]) : null;
                        if (name && odds && odds > 0) {
                            items.push({name: name, odds: odds});
                        }
                    }
                });

                if (items.length >= 2) {
                    const key = items.map(i => i.name + i.odds).join('|');
                    if (!seen.has(key)) {
                        seen.add(key);
                        results.push({
                            type: 'market',
                            team1: items[0].name,
                            odds1: items[0].odds,
                            team2: items[1] ? items[1].name : '',
                            odds2: items[1] ? items[1].odds : 0,
                            total: items.length
                        });
                    }
                }
            });

            // 如果没找到，回退到文本提取
            if (results.length === 0) {
                document.querySelectorAll('[class*="outcome"]').forEach(el => {
                    if (el.closest('nav, header, footer, [class*="sidebar"], [class*="menu"]'))
                        return;
                    const text = el.innerText.trim();
                    if (text && !seen.has(text) && /\\d+\\.\\d{2,3}/.test(text)) {
                        seen.add(text);
                        results.push({type: 'odds_text', text: text.substring(0, 200)});
                    }
                });
            }

            return results.slice(0, 50);
        }""")

    def _extract_casino_events(self) -> list[dict]:
        """提取赌场游戏数据"""
        results = []
        crash_selectors = [".crash-history-item", ".previous-crash", '[class*="crash" i]']
        for selector in crash_selectors:
            try:
                items = self.page.query_selector_all(selector)
                for item in items:
                    text = item.inner_text().strip()
                    if text:
                        results.append({"event": text, "type": "crash"})
                if results:
                    break
            except Exception:
                continue
        return results

    def _extract_generic_data(self) -> list[dict]:
        """通用数据提取，通过 JS 获取页面关键文本"""
        results = []
        try:
            snippets = self.page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('[class*="event"], [class*="match"], [class*="score"], [class*="odd"]').forEach(el => {
                    const text = el.innerText.trim();
                    if (text && text.length > 3) results.push(text);
                });
                return results.slice(0, 50);
            }""")
            for text in snippets:
                results.append({"event": text[:200], "type": "generic"})
        except Exception as e:
            logger.debug(f"通用提取失败: {e}")
        return results

    def extract_text_content(self, selector: str) -> str:
        """提取指定选择器的文本"""
        if not self.page:
            return ""
        try:
            el = self.page.query_selector(selector)
            return el.inner_text().strip() if el else ""
        except Exception:
            return ""

    def extract_all_text(self) -> str:
        """提取页面全部可见文本（用于调试）"""
        if not self.page:
            return ""
        try:
            body = self.page.query_selector("body")
            return body.inner_text() if body else ""
        except Exception:
            return ""

    def screenshot(self, path: str = "screenshot.png"):
        """截图保存（用于调试）"""
        if self.page:
            self.page.screenshot(path=path, full_page=True)
            logger.info(f"截图已保存: {path}")

    def stop(self):
        """关闭浏览器并释放所有资源"""
        cdp_mode = self.browser_cfg.get("cdp_port", 0) > 0

        if self.page and cdp_mode:
            try:
                self.page.close()
            except Exception:
                pass

        if not cdp_mode:
            try:
                if self._context:
                    self._context.close()
            except Exception as e:
                logger.warning(f"关闭 context 异常: {e}")
            try:
                if self.browser:
                    self.browser.close()
            except Exception as e:
                logger.warning(f"关闭 browser 异常: {e}")

        try:
            if self._managed_pw:
                self._managed_pw.__exit__(None, None, None)
        except Exception as e:
            logger.warning(f"停止 playwright 异常: {e}")
        self._context = None
        self.browser = None
        self._playwright = None
        self._managed_pw = None
        self._pw_context = None
        self.page = None
        logger.info("所有浏览器资源已释放")
