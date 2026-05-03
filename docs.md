# Stake Monitor 模块文档

## 项目依赖

| 库 | 版本 | 用途 |
|----|------|------|
| `playwright` | >=1.40.0 | 浏览器自动化，CDP 连接，DOM 操作 |
| `playwright_stealth` | 最新 | 反检测，隐藏自动化特征 |
| `pyyaml` | >=6.0 | YAML 配置文件解析 |
| `requests` | >=2.28.0 | HTTP 请求（汇率 API、加密货币 API、Webhook 通知） |
| `re` | 内置 | 正则表达式，币种文本解析 |
| `json` | 内置 | JSON 数据解析 |
| `logging` | 内置 | 日志系统 |
| `time` | 内置 | 时间戳、轮询等待、缓存过期 |
| `urllib.request` | 内置 | 汇率 API 请求（无额外依赖） |

---

## 1. run_monitor.py — 主监控入口

### 概述
程序主循环，协调所有模块：连接浏览器、轮询数据、过滤投注、提取分享链接、发送通知。

### 全局变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `seen_bets` | `set` | 已见投注去重集合，key 为 `event\|player\|time\|amount` |
| `ALERT_THRESHOLD_CNY` | `int` | 从 `config.yaml` 读取的大额阈值 |

### 主循环流程

```
while True:
    1. scraper.fetch_data()                    # 获取风云榜数据
    2. 过滤 bet_feed 类型                       # 排除 market 数据
    3. 去重：event|player|time|amount 组合 key  # 仅新投注进入 new_bets
    4. 遍历 new_bets:
       a. parse_amount()  → 币种识别
       b. to_cny()        → 汇率转换
       c. 过滤复式投注     → skip(复式)
       d. 检测大额         → amount_cny >= threshold
       e. 过滤未知币种     → amount_cny == 0 → skip(unknown)
    5. 大额投注:
       a. extract_details_for_bets() → 点击弹窗 → 剪贴板拦截 → 分享链接
       b. notifier.send()            → 企业微信推送
    6. sleep(poll_interval)
```

### 过滤规则

| 条件 | 动作 |
|------|------|
| `event` 含"复式" | `skip(复式)` |
| `to_cny()` 返回 0（非 USDT/USDC/BTC/ETH） | `skip(unknown currency)` |
| `amount_cny < ALERT_THRESHOLD_CNY` | 不通知（仅日志记录） |

---

## 2. scraper.py — 浏览器控制与数据采集

### 依赖库

- **`playwright.sync_api`** — `sync_playwright`, `Page`, `Browser` 同步 API
- **`playwright_stealth`** — `Stealth` 反检测注入

### 类: `StakeScraper`

#### 构造参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `config` | `dict` | 从 `config.yaml` 反序列化 |

#### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `page` | `Page \| None` | Playwright 页面对象 |
| `browser` | `Browser \| None` | Playwright 浏览器对象 |
| `_context` | `BrowserContext` | 浏览器上下文 |
| `target_url` | `str` | 目标监控 URL |

### 公开方法

#### `start()`
启动浏览器并导航到目标页面。支持两种模式：

**CDP 模式** (cdp_port > 0)：连接已有浏览器实例 `chromium.connect_over_cdp(endpoint)`，复用登录态

**内置模式** (cdp_port = 0)：`chromium.launch()` 或 `launch_persistent_context(user_data_dir)` 启动新浏览器

注入 Stealth 反检测：`chrome_runtime`、`navigator_webdriver`、`navigator_languages` 等。

#### `fetch_data() → list[dict]`
轮询一次，从页面提取所有投注数据。根据 URL 特征分发：

| URL 特征 | 提取方法 | 返回 type |
|---------|---------|----------|
| `sports` 页面 | `_extract_sports_events()` + `_extract_bet_feed()` | `market` + `bet_feed` |
| `casino` 页面 | `_extract_casino_events()` | `crash` |
| 其他 | `_extract_generic_data()` | `generic` |

返回格式：
```python
{
    "type": "bet_feed",
    "event": "赛事名",
    "player": "玩家名",
    "time": "时间",
    "odds": "赔率",
    "amount": "投注额原文",
    "currency": "SVG图标币种代码",  # 如 BTC/ETH/USDT
    "amount_num": 1234.56,
    "rawCols": ["列1", "列2", ...]
}
```

### 内部方法

#### `_extract_bet_feed() → list[dict]`
从 `<tr>` 表格行提取风云榜数据。

**列提取逻辑**：
1. 调用 `_click_bets_tab()` 确保"风云榜" tab 激活
2. 遍历所有 `<tr>`
3. 过滤：至少 4 个 `<td>`、含赔率模式 `\d{1,4}\.\d{2,3}`
4. 双通道币种识别：
   - **文本通道**：`td.innerText` 获取金额文本，由 `parse_amount()` 解析
   - **SVG 通道**：`td.querySelector('svg').getAttribute('data-ds-icon')` 获取图标币种

#### `_click_bets_tab()`
确保风云榜 tab 处于激活状态。滚动到目标按钮，用 Playwright `page.click()` 点击 `button:has-text('风云榜')`。

#### `_find_bet_row(bet: dict) → dict | None`
在 DOM 中根据 `event + player` 文本定位匹配的 `<tr>` 行。返回行索引和触发类型（`button`/`anchor`/`td`）。

**匹配逻辑**：遍历所有 `<tr>`，用 `.filter(Boolean)` 对齐列索引后，比对 `texts[0]`（赛事）和 `texts[1]`（玩家）。

#### `_open_bet_detail(bet: dict) → str`
点击风云榜行的赛事名按钮，打开投注详情弹窗，提取分享链接后关闭弹窗。

**流程**：
1. `_find_bet_row()` 定位行
2. Playwright `locator().click()` 点击第 1 列按钮
3. `_get_share_link_from_detail()` 提取链接
4. `_dismiss_detail_panel()` 关闭弹窗
5. 失败重试 1 次

#### `_get_share_link_from_detail(timeout=10) → str`
从投注详情弹窗中获取分享链接。

**三步策略**：
1. 安装 `navigator.clipboard.writeText` 拦截器 → 全局变量 `__captured_share_url`
2. 在 `[class*="fixed"][class*="justify-center"]` 弹窗中依次点击每个"复制"按钮
3. 优先返回含 `modal=bet` 的链接，fallback 根据 Bet ID 构造

#### `_dismiss_detail_panel()`
关闭详情弹窗：优先查找并点击 close 按钮，fallback 按 Escape 键。

#### `extract_details_for_bets(bets: list[dict]) → list[dict]`
公开接口：对一批大额投注逐个提取分享链接（含重试），返回富化数据。

---

## 3. forex.py — 币种解析与汇率转换

### 依赖库

- **`re`** — 正则匹配币种格式
- **`json`** — 解析 API 响应
- **`urllib.request`** — 在线汇率 / 加密货币价格 API 请求
- **`CoinGecko API`** — 加密货币实时价格（BTC、ETH）
- **`exchangerate-api.com`** — 法币汇率（USD 基准）

### 常量

#### `SYMBOL_MAP: dict`
货币符号 → ISO 代码映射，处理带符号前缀的金额：

```python
{"₹": "INR", "CA$": "CAD", "€": "EUR", "₿": "BTC", ...}
```

#### `CACHE_TTL = 3600`
法币汇率缓存时间（秒），1 小时内复用。

#### `CRYPTO_TTL = 300`
加密货币价格缓存时间（秒），5 分钟内复用。

### 公开函数

#### `parse_amount(amount_str: str) → tuple[float, str]`
解析投注额字符串，提取数值和币种代码。**6 步优先级匹配**：

| 步骤 | 正则 / 逻辑 | 示例输入 | 输出 |
|------|-----------|---------|------|
| 1 | 符号前缀 `SYMBOL_MAP` | `₹100,000` | `(100000, "INR")` |
| 2 | 纯数字 `^[\d,.]+$` | `5000.00` | `(5000, "USD")` |
| 2.5 | 代码前缀 `^([A-Za-z]{3,5})\s*([\d,.]+)$` | `INR100000` | `(100000, "INR")` |
| 3 | 代码后缀 `^([\d,.]+)\s*([A-Za-z]{3,4})$` | `110,000 USDC` | `(110000, "USDC")` |
| 4 | 关键词 `USDT/BTC/ETH/USD/EUR` | `BTC 0.01` | `(0.01, "BTC")` |
| 5 | 兜底提取数字 | `任何含数字串` | `(数字, "USD")` |

#### `to_cny(amount_str: str, hint_currency: str = "") → float`
将投注额转换为人民币。**仅支持 USDT/USDC/BTC/ETH**。

**三路逻辑**：

```
parse_amount() → (val, cur)
  │
  ├─ USDT/USDC/BUSD/DAI/TUSD → cur = "USD"
  │
  ├─ cur == "USD"  → return val × CNY/USD 汇率
  │
  ├─ cur == "BTC" or "ETH"
  │   → price = CoinGecko API 实时价格
  │   → return val × price × CNY/USD 汇率
  │
  └─ 其他 → return 0  (skip)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `amount_str` | `str` | 原始投注额字符串 |
| `hint_currency` | `str` | SVG 图标提取的币种代码，优先于文本解析 |
| **返回** | `float` | CNY 金额，0 表示不支持该币种 |

### 内部函数

#### `_fetch_rates() → dict`
获取法币汇率（USD 基准），来源：`exchangerate-api.com/v4/latest/USD`。

- **缓存**：`CACHE_TTL = 3600` 秒
- **重试**：失败 3 次，间隔 2 秒
- **回退**：`_hardcoded_rates()` 提供硬编码汇率

#### `_fetch_crypto_prices() → dict`
获取 BTC/ETH 实时 USD 价格，来源：`api.coingecko.com/api/v3/simple/price`。

- **缓存**：`CRYPTO_TTL = 300` 秒
- **回退**：`{"BTC": 85000, "ETH": 1800}` 硬编码

#### `_hardcoded_rates() → dict`
硬编码汇率表（2026/05 近似值），作为 API 失败时的回退。

---

## 4. notifier.py — 通知模块

### 依赖库

- **`requests`** — HTTP POST 请求
- **企业微信机器人 Webhook API** — `qyapi.weixin.qq.com/cgi-bin/webhook/send`
- **钉钉机器人 Webhook API** — `oapi.dingtalk.com/robot/send`

### 类: `Notifier`

#### 构造参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `config` | `dict` | `config.yaml` 的 `notifications` 节点 |

### 公开方法

#### `send(title: str, data: list[dict])`
发送通知到所有已启用的渠道（企业微信 / 钉钉）。

### 内部方法

#### `_send_wecom(webhook_url: str, title: str, data: list[dict])`
企业微信 Markdown 格式通知，含 **4096 字节分条**逻辑。

**请求格式**：
```json
{
  "msgtype": "markdown",
  "markdown": {"content": "## Title\n\n> **field**: value\n..."}
}
```

#### `_split_wecom_chunks(title, data, max_bytes=4096) → list[str]`
将投注数据按字节数分块。每条分块包含完整标题头。

#### `_format_one_bet(item: dict) → list[str]`
格式化单条投注为 Markdown 行：

```
> **赛事**: LPL 2026 — Weibo Gaming vs Top Esports
> **玩家**: 隐身
> **时间**: 下午6:18
> **赔率**: 1.75x
> **金额**: <font color="warning">USDC 110,000</font>
> **CNY**: <font color="warning">CNY752,400</font>
> **分享**: https://stake.com/sports/home?iid=...
```

#### `_send_dingtalk(webhook_url, title, data)`
钉钉 Markdown 格式通知（格式同企业微信）。

---

## 5. 外部 API 清单

| API | URL | 用途 | 频率限制 |
|-----|-----|------|---------|
| Exchange Rate API | `api.exchangerate-api.com/v4/latest/USD` | 法币汇率（USD 基准，含 CNY） | 免费版 ~1500 req/month |
| CoinGecko | `api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd` | BTC/ETH 实时 USD 价格 | 免费版 10-30 req/min |
| 企业微信 Webhook | `qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` | 推送 Markdown 消息 | 20 条/min |

---

## 6. config.yaml 完整配置项

| 路径 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `target.url` | `str` | `stake.com/sports/live` | 监控目标页面 |
| `browser.headless` | `bool` | `false` | 无头模式 |
| `browser.channel` | `str` | `chrome` | 浏览器类型 |
| `browser.slow_mo` | `int` | `100` | 操作间隔毫秒（防检测） |
| `browser.cdp_port` | `int` | `9222` | CDP 端口，0=内置浏览器 |
| `browser.user_data_dir` | `str` | `./browser_profile` | 持久化用户目录 |
| `browser.timeout` | `int` | `60000` | 页面加载超时(ms) |
| `browser.nav_retries` | `int` | `3` | 导航重试次数 |
| `scraper.poll_interval` | `int` | `30` | 轮询间隔(秒) |
| `scraper.wait_for_selector` | `str` | `.sports-live-events` | 等待元素选择器 |
| `scraper.ready_timeout` | `int` | `120` | 页面就绪超时(秒) |
| `notifications.rules.cny_threshold` | `int` | `500000` | 大额通知阈值(人民币) |
| `notifications.wecom.enabled` | `bool` | `true` | 启用企业微信 |
| `notifications.wecom.webhook_url` | `str` | — | 企业微信 Webhook URL |
| `logging.level` | `str` | `INFO` | 日志级别 |
| `logging.file` | `str` | `monitor.log` | 日志文件路径 |
