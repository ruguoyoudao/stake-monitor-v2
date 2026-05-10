# Stake Monitor V2 模块文档

## 项目依赖

| 库 | 版本 | 用途 |
|----|------|------|
| `playwright` | >=1.40.0 | 浏览器自动化，CDP 连接，DOM 操作 |
| `playwright_stealth` | 最新 | 反检测，隐藏自动化特征 |
| `pyyaml` | >=6.0 | YAML 配置文件解析 |
| `requests` | >=2.28.0 | HTTP 请求（汇率 API、加密货币 API、Webhook 通知） |
| `streamlit` | >=1.30.0 | Web 仪表板框架 |
| `plotly` | >=5.0.0 | 交互式图表 |
| `pandas` | >=2.0.0 | 数据分析 |

---

## 1. run_monitor.py — 主监控入口

### 概述
程序主循环，协调所有模块：连接浏览器、轮询数据、过滤投注、弹窗采集分享链接+玩法+结果、本地存储、跟注聚类检测。

### 全局变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `seen_bets` | `set` | 已见投注去重集合，key 为 `event\|player\|time\|odds` |
| `cluster_alerts` | `dict` | 聚类通知计数，`{cluster_key: last_notified_count}` |
| `ALERT_THRESHOLD_CNY` | `int` | 从 `config.yaml` 读取的大额阈值 |
| `CLUSTER_MIN_COUNT` | `int` | 聚类触发最小条数 |
| `CLUSTER_STEP` | `int` | 聚类递增步长 |
| `CLUSTER_WINDOW_HRS` | `int` | 聚类时间窗口（小时），0=不限 |

### 主循环流程

```
while True:
    1. scraper.fetch_data(types=["bet_feed"])     # 仅提取风云榜，跳过体育赛事
    2. 去重：event|player|time|odds 组合 key       # 仅新投注进入 new_bets
    3. 遍历 new_bets:
       a. parse_amount() → 币种识别
       b. to_cny()       → 汇率转换
       c. 过滤复式投注    → skip(复式)
       d. 检测大额        → amount_cny >= threshold
       e. 过滤未知币种    → amount_cny == 0 → skip
    4. 大额投注:
       a. extract_details_for_bets() → 点击弹窗 → share_link + market + outcome
       b. _save_large_bets()         → 追加到 large_bets.json
       c. _check_clusters()          → 聚类检测 → 企业微信通知
    5. 异常检测（连续 10 次写日志警告）:
       a. data == []       → 数据提取异常
       b. bets == 0        → 无投注数据
       c. new_bets == 0    → feed 停滞
    6. sleep(poll_interval)
```

### 过滤规则

| 条件 | 动作 |
|------|------|
| `event` 含"复式" | `skip(复式)` |
| `to_cny()` 返回 0 | `skip(unknown currency)` |
| `amount_cny < threshold` | 不记录（仅日志） |

### 聚类检测

`_check_clusters()` — 每次保存 `large_bets.json` 后自动触发：
- 加载全量数据，按 `event|market|outcome` 分组
- 按 `saved_at` 时间戳过滤时间窗口（`window_hours`）
- 首次达 `min_count` 条 → `notifier.send_cluster_alert()` 推送
- 之后每增加 `step` 条再推送
- 已通知计数持久化到 `cluster_alerts.json`

### 异常检测

| 条件 | 连续次数 | 日志级别 | 典型原因 |
|------|---------|---------|---------|
| `len(data) == 0` | ≥ 10 | WARNING | 浏览器页面关闭/崩溃 |
| `len(bets) == 0` | ≥ 10 | WARNING | 投注数据未加载 |
| `len(new_bets) == 0` | ≥ 10 | WARNING | feed 停滞 |

### 状态文件

| 文件 | 内容 |
|------|------|
| `seen_bets.json` | 已处理投注 key 集合（event\|player\|time\|odds） |
| `large_bets.json` | 大额投注记录（含 `saved_at` 时间戳） |
| `cluster_alerts.json` | 聚类通知计数 `{cluster_key: last_notified_count}` |

---

## 2. scraper.py — 浏览器控制与数据采集

### 类: `StakeScraper`

#### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `page` | `Page \| None` | Playwright 页面对象 |
| `browser` | `Browser \| None` | Playwright 浏览器对象 |
| `_context` | `BrowserContext` | 浏览器上下文 |
| `target_url` | `str` | 目标监控 URL |

### 公开方法

#### `start()`
启动浏览器并导航到目标页面。CDP 模式连接已有浏览器复用登录态，内置模式启动新浏览器。注入 Stealth 反检测。

#### `fetch_data(types: list[str] | None = None) → list[dict]`
轮询一次提取投注数据。`types` 参数可选过滤提取类型：

| 参数 | 提取内容 |
|------|---------|
| `None` (默认) | sports_events + bet_feed |
| `["bet_feed"]` | 仅风云榜（run_monitor 使用） |

返回格式：
```python
{
    "type": "bet_feed",
    "event": "赛事名",
    "player": "玩家名",
    "time": "时间",
    "odds": "赔率",
    "amount": "投注额原文",
    "currency": "SVG图标币种代码",
    "amount_num": 1234.56,
    "rawCols": ["event", "player", "time", "odds", "amount"]
}
```

### 内部方法

#### `_extract_bet_feed() → list[dict]`
从 `<tr>` 表格行提取风云榜数据。列序：event | player | time | odds | amount。

#### `_find_bet_row(bet: dict) → dict | None`
根据 rawCols 五字段精确匹配 DOM 行。文本提取（`innerText || textContent`）须与 `_extract_bet_feed` 一致。

#### `_open_bet_detail(bet: dict) → dict`
点击风云榜行打开投注详情弹窗，核对后提取分享链接+玩法+结果。

**流程**：
1. 首次 `_find_bet_row()` 精确定位 → 缓存 `bet['_cached_row']`（避免重试时 DOM 变化导致漂移）
2. `locator().click()` 点击
2. 等待弹窗出现（含 Bet ID）
3. `_extract_modal_info()` 提取 odds+amount 双字段核对（不匹配重试 1 次）
4. `_get_share_link_from_detail()` clipboard 拦截获取分享链接
5. `_extract_modal_info()` 再次提取 market/outcome
6. `_dismiss_detail_panel()` 关闭弹窗
7. 返回 `{"share_link": "...", "market": "...", "outcome": "..."}`

#### `_extract_modal_info() → dict`
从弹窗文本提取 `{event, player, odds, amount, market, outcome}`。

**market/outcome 提取逻辑**（弹窗无标签行，靠相对位置）：
```
弹窗文本结构: ...状态行 → [market] → [outcome] → [inline_odds] → 赔率 → [赔率值] → 投注额 → [金额]
```
- 倒找最后一行的 `赔率`/`Odds` → `oddsLabelIdx`
- `market = lines[oddsLabelIdx - 3]`
- `outcome = lines[oddsLabelIdx - 2]`
- 验证：market 非纯数字/非比分/长度≤80/与outcome不同（否则置空）

#### `_get_share_link_from_detail(timeout=10) → str`
clipboard 拦截获取分享链接。拦截器仅安装一次（`__capture_installed` 标志）。依次点击弹窗中"复制"按钮，优先返回含 `modal=bet` 的链接，fallback 根据 Bet ID 构造。

#### `_dismiss_detail_panel()`
关闭弹窗：优先点 close 按钮，fallback Escape 键，轮询确认弹窗消失。

#### `extract_details_for_bets(bets: list[dict]) → list[dict]`
公开接口：对一批投注逐个提取 `share_link` + `market` + `outcome`（失败重试 1 次），返回富化数据。

---

## 3. forex.py — 币种解析与汇率转换

### 常量

`SYMBOL_MAP`：货币符号 → ISO 代码映射
`CACHE_TTL = 3600`：法币汇率缓存 1h
`CRYPTO_TTL = 300`：加密货币价格缓存 5min

### 公开函数

#### `parse_amount(amount_str: str) → tuple[float, str]`
6 步优先级匹配解析投注额：

| 步骤 | 逻辑 | 示例 | 输出 |
|------|------|------|------|
| 1 | 符号前缀 `SYMBOL_MAP` | `₹100,000` | `(100000, "INR")` |
| 2 | 纯数字 | `5000.00` | `(5000, "USD")` |
| 2.5 | 代码前缀 | `INR100000` | `(100000, "INR")` |
| 3 | 代码后缀 | `110,000 USDC` | `(110000, "USDC")` |
| 4 | 关键词 | `BTC 0.01` | `(0.01, "BTC")` |
| 5 | 兜底数字 | 任意 | `(数字, "USD")` |

#### `to_cny(amount_str: str, hint_currency: str = "") → float`
转换为人民币。仅支持 USDT/USDC/BTC/ETH，其他返回 0。

换算：USDT/USDC → USD 1:1 × CNY汇率；BTC/ETH → 实时价格 × CNY汇率；其他法币 → 通用汇率换算。

---

## 4. notifier.py — 通知模块

### 类: `Notifier`

#### `send(title, data: list[dict])`
单笔投注通知（当前 `run_monitor.py` 不调用此方法）。

#### `send_cluster_alert(title: str, cluster_data: dict)`
**聚类跟注预警专用方法**。

`cluster_data` 字段：
```python
{
    "title": "跟注预警 - 埃弗顿 - 曼城",
    "event": "埃弗顿 - 曼城",
    "market": "胜平负",
    "outcome": "曼城",
    "count": 3,
    "players": ["Jesusitoln", "DonnetteKok0", "caulvi247"],
    "total_cny": 183055,
    "latest_odds": "1.49",
}
```

企业微信 Markdown 输出格式：
```
## 跟注预警 - 埃弗顿 - 曼城

> 赛事: 埃弗顿 - 曼城
> 玩法: 胜平负
> 结果: 曼城
> 累积: 3 条大额下注
> 玩家: Jesusitoln, DonnetteKok0, caulvi247
> 赔率: 1.49x
> 总金额 CNY: 183,055
```

赔率三色：`<1.2` 灰 / `1.2~1.4` 蓝 / `>=1.4` 红。

---

## 5. capture_bets.py — 弹窗批量采集

### 概述
一次性打开风云榜所有投注的弹窗，采集完整信息保存到 `captured_bets.json`。支持断点续采。

### 流程

```
1. scraper.fetch_data() → 获取风云榜所有投注
2. 去重（capture_progress.json）
3. 对每条待采集投注:
   a. scraper._open_bet_detail(bet) → 点击+校验+share_link+market+outcome
   b. 补充采集弹窗完整文本(modal_full_text)和 HTML(modal_html_snippet)
   c. 提取 Bet ID
   d. 关闭弹窗
4. 每 5 条保存一次进度
```

### 输出 (`captured_bets.json`)

```python
{
    "bet_id": "575689753",
    "feed_event": "赛事名",
    "feed_player": "玩家名",
    "feed_time": "时间",
    "feed_odds": "赔率",
    "feed_amount": "投注额",
    "feed_currency": "BTC",
    "modal_event": "弹窗赛事",
    "modal_player": "弹窗玩家",
    "modal_odds": "弹窗赔率",
    "modal_amount": "弹窗金额",
    "modal_market": "玩法",
    "modal_outcome": "下注结果",
    "modal_full_text": "弹窗全部文本内容",
    "modal_html_snippet": "弹窗HTML(前5000字符)",
    "share_link": "分享链接",
    "captured_at": "2026-05-04T23:50:16"
}
```

---

## 6. visualize.py — Streamlit 数据可视化

### 概述
交互式 Web 看板，读取 `large_bets.json` 和 `cluster_alerts.json`，提供大额投注数据的多维度分析和可视化。

### 启动

```powershell
streamlit run visualize.py
```

### 功能

| 模块 | 说明 |
|------|------|
| 概览指标 | 投注笔数、总金额、平均金额、最大单笔 |
| 侧边栏筛选 | 最小CNY金额、币种、赔率范围、赛事关键词搜索、时间范围（默认当天） |
| 金额分布 | 直方图，展示投注金额频率分布 |
| 赔率 vs 金额 | 散点图，按币种着色，hover 赛事/玩家/结果 |
| 赛事热度 | TOP 20 赛事按总金额排序的水平条形图 |
| 趋势时间线 | 10分钟聚合的投注金额面积图 |
| 赛事玩法分析 | 选中赛事后 groupby(market, outcome) 明细表 + 分组条形图 |
| 聚类检测结果 | 读取 `cluster_alerts.json` 展示告警记录 |
| 投注明细 | 完整数据表，支持排序和筛选 |

### 数据源

| 文件 | 用途 |
|------|------|
| `large_bets.json` | 大额投注记录（自动每30秒刷新） |
| `cluster_alerts.json` | 聚类告警记录 |


## 7. 外部 API 清单

| API | URL | 用途 |
|-----|-----|------|
| Exchange Rate | `api.exchangerate-api.com/v4/latest/USD` | 法币汇率 |
| CoinGecko | `api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd` | BTC/ETH 实时 USD 价格 |
| 企业微信 | `qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` | 聚类预警推送 |

---

## 8. config.yaml 完整配置项

| 路径 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `target.url` | `str` | `stake.com/sports/live` | 监控目标页面 |
| `browser.cdp_port` | `int` | `9222` | CDP 端口，0=内置浏览器 |
| `browser.user_data_dir` | `str` | `./browser_profile` | 持久化用户目录 |
| `scraper.poll_interval` | `int` | `30` | 轮询间隔(秒) |
| `scraper.wait_for_selector` | `str` | `.sports-live-events` | 等待元素选择器 |
| `scraper.ready_timeout` | `int` | `120` | 页面就绪超时(秒) |
| `notifications.rules.cny_threshold` | `int` | `20000` | 大额阈值(人民币) |
| `notifications.wecom.enabled` | `bool` | `true` | 启用企业微信 |
| `notifications.wecom.webhook_url` | `str` | `${WECOM_WEBHOOK_URL}` | 企业微信 Webhook URL |
| `clustering.enabled` | `bool` | `true` | 启用聚类检测 |
| `clustering.min_count` | `int` | `3` | 聚类触发最小条数 |
| `clustering.step` | `int` | `1` | 递增通知步长 |
| `clustering.window_hours` | `int` | `24` | 时间窗口(小时)，0=不限 |
| `logging.level` | `str` | `INFO` | 日志级别 |
| `logging.file` | `str` | `monitor.log` | 日志文件路径 |
