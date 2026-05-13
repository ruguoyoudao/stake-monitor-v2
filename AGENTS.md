# AGENTS.md — Stake Monitor V2 项目指南

## 启动流程（硬依赖）

```powershell
# 1. 关闭旧 Edge，以 CDP 调试模式启动
taskkill /F /IM msedge.exe
start msedge --remote-debugging-port=9222 https://stake.com/sports/live
# 2. 在 Edge 中手动登录 stake.com
# 3. 启动监控
python run_monitor.py
# 或一键: start_monitor.bat
```

**`cdp_port: 9222` 是硬依赖。** 程序通过 CDP 连接已有 Edge 复用登录态。端口未启动报 `ECONNREFUSED 127.0.0.1:9222`。

## 入口文件

| 文件 | 角色 |
|------|------|
| `run_monitor.py` | **主入口** — 轮询监控 + 大额本地存储 + 聚类企业微信通知 |
| `capture_bets.py` | 批量采集 — 一次性打开所有风云榜投注弹窗，保存完整弹窗文本/HTML |
| `tmp_check_detail.py` | 一次性 DOM 探索脚本，非生产代码 |

## 核心架构

### 数据流

```
run_monitor.py 轮询(30s)
  → scraper.fetch_data(types=["bet_feed"])    # 仅提取风云榜，跳过体育赛事DOM扫描
    → 过滤新投注 (seen_bets.json 去重)
      → 过滤大额 (CNY >= threshold, 跳过"复式"/"多项")
        → scraper.extract_details_for_bets()   # 点击弹窗获取 share_link + market + outcome + event_url
          → 保存到 large_bets.json (含 saved_at 时间戳)
            → _check_clusters()               # 24h窗口内按(event+market+outcome)分组
              → >=3条 → notifier.send_cluster_alert() → 企业微信
```

### 弹窗信息提取

**market/outcome 靠相对位置反推，无标签行：**

弹窗文本结构（单关投注）：
```
...状态行 → [market] → [outcome] → [inline_odds] → 赔率 → [赔率值] → 投注额 → [金额]
```
提取逻辑（`scraper.py:_extract_modal_info`）：
- `赔率` 标签行倒找 → `oddsLabelIdx`
- `market = lines[oddsLabelIdx - 3]`
- `outcome = lines[oddsLabelIdx - 2]`
- 验证：market 不能是纯数字/比分/超长(>80字符)/与outcome相同（否则置空）

若 stake.com 更新弹窗布局，首查 `captured_bets.json` 中 `modal_full_text` 验证新结构。

### 币种识别：双通道

- **SVG 通道**（优先）：`_extract_bet_feed()` 从 `<svg data-ds-icon="ETH">` 提取币种代码
- **文本通道**（回退）：`parse_amount()` 6 步正则匹配
- 最终 `currency`：SVG 仅在 `text_currency == "USD"` 时启用

**仅支持 4 种币种**：USDT, USDC, BTC, ETH。其他 → `to_cny()` 返回 0（skip）。

### 行定位必须精确

`_find_bet_row` 的文本提取方式 **必须** 与 `_extract_bet_feed` 完全一致：
```javascript
// 正确: (innerText || textContent || '').trim().filter(t => t.length > 0)
// 错误: (innerText || '').trim().filter()
```
不一致导致大小写差异（`SRL` vs `Srl`），5 字段匹配失败。

### 分享链接提取流程

```
find row (5字段精确匹配) → click → wait modal → verify (odds+amount)
  → clipboard intercept → get share URL → _extract_modal_info(market+outcome)
  → dismiss modal
```
- clipboard 拦截器仅安装一次（`__capture_installed` 标志防重复）
- 弹窗核对失败重试 1 次，仍失败跳过
- 每笔前自动 `_dismiss_detail_panel()` 清除残留弹窗

### 聚类检测

`_check_clusters()` — 每次保存 `large_bets.json` 后触发：
- 加载全量数据，按 `event|market|outcome` 分组（过滤空字段）
- `window_hours: 24` 仅统计最近 N 小时内的投注
- 首次达 `min_count: 3` 条 → 通知；之后每增 `step: 1` 条再通知
- 已通知计数存在 `cluster_alerts.json`，重启不重复

### 通知

- **聚类通知**：使用 `notifier.send_cluster_alert()` — 专用格式含 玩法/结果/累积/玩家/总金额
- **大额单笔**：**不再发企业微信**，仅保存到 `large_bets.json`
- **异常监控**：连续10轮异常写 `logger.warning` + 企业微信通知（数据提取异常/无投注/Feed停滞三种类型）
- 企业微信Markdown：3色 `info`(蓝) / `warning`(橙红) / `comment`(灰)
- 赔率三色：`<1.2` 灰 / `1.2~1.4` 蓝 / `>=1.4` 红

### 汇率架构

两个独立 API，各自缓存：
- **法币**：`exchangerate-api.com/v4/latest/USD` — 缓存 1h，3 次重试
- **加密货币**：CoinGecko `simple/price?ids=bitcoin,ethereum` — 缓存 5min，需 User-Agent header

换算：USDT/USDC → USD 1:1；BTC/ETH → `val × crypto_price × CNY_rate`

## 状态文件

| 文件 | 内容 |
|------|------|
| `seen_bets.json` | 已处理投注 key 集合（去重） |
| `large_bets.json` | 大额投注记录（含 `saved_at` 时间戳） |
| `cluster_alerts.json` | `{cluster_key: last_notified_count}` |
| `captured_bets.json` | 批量采集的弹窗完整数据（含 `modal_full_text`） |

## 过滤规则

| 条件 | 动作 |
|------|------|
| event 含 "复式" 或 "多项" | skip（不进入 large_bets.json） |
| `sport_category` 不在白名单中 | skip（config.yaml filters.sport_categories） |
| `to_cny()` 返回 0 | skip（未知币种） |
| `amount_cny < threshold` | 不记录大额 |

## 环境坑

- **控制台 GBK 编码**：含 `₹`/`ö` 等字符的 `print()` 会 `UnicodeEncodeError`。所有输出用 `logger`，日志 UTF-8
- **PowerShell `$` 转义**：命令行中 `CA$1,445` 的 `$` 会被 PowerShell 吃掉，用单引号包裹或文件测试
- **BTC/ETH 金额保留 2 位小数**，其他币种整数显示

## 调试

```yaml
# config.yaml — 临时降低阈值
notifications.rules.cny_threshold: 10000

# config.yaml — DEBUG 查看 parse_amount 每步路径
logging.level: "DEBUG"

# config.yaml — 关闭时间窗口看全量聚类
clustering.window_hours: 0
```
