# AGENTS.md — Stake Monitor 项目指南

## 启动必须先运行 Edge CDP

```powershell
# 关闭旧 Edge 并以调试端口启动（必须）
taskkill /F /IM msedge.exe
start msedge --remote-debugging-port=9222 https://stake.com/sports/live
# 然后手动登录 stake.com，再启动监控
python run_monitor.py

# 或者一键启动
start_monitor.bat
```

**`cdp_port: 9222` 是硬依赖。**程序通过 CDP 连接已有 Edge 实例复用登录态。端口未启动则报 `ECONNREFUSED 127.0.0.1:9222`。

## 入口文件

| 文件 | 角色 |
|------|------|
| `run_monitor.py` | **主入口**（product code） |
| `main.py` | **已废弃**，不再维护 |
| `tmp_check_detail.py` | 一次性的 DOM 探索脚本，非生产代码 |

## 关键架构约定

### 币种识别：双通道

- **SVG 通道**（优先）：`_extract_bet_feed()` 从 `<svg data-ds-icon="ETH">` 提取币种代码，存入 `bet['currency']`
- **文本通道**（回退）：`parse_amount()` 6 步正则匹配金额字符串中的符号/代码
- 最终的 `currency` 由 `run_monitor.py` 决定：SVG 仅在 text_currency == "USD" 时启用

**仅支持 4 种币种**：USDT, USDC, BTC, ETH。其他货币 `to_cny()` 返回 `0`（视为 skip 信号）。

### 行定位必须精确

`_find_bet_row` 的文本提取方式 **必须** 与 `_extract_bet_feed` 完全一致：
```javascript
// 必须：(innerText || textContent || '').trim().filter(t => t.length > 0)
// 不能：  (innerText || '').trim().filter()
```
不一致会导致 `SRL` vs `Srl` 这种大小写差异，5 字段匹配失败。

### 分享链接提取流程

```
find row (5 字段精确匹配) → click → wait modal → verify (odds+amount 2 字段)
  → clipboard intercept → get share URL → dismiss modal
```
- 弹窗核对失败会重试 1 次，仍失败则跳过
- 每笔投注处理前自动 `_dismiss_detail_panel()` 清除残留弹窗

### 通知格式约束

- 企业微信 Markdown 仅支持 3 种颜色：`info`（蓝）、`warning`（橙红）、`comment`（灰）
- 单条消息上限 **4096 字节**，超限自动分条 `(1/N)`
- 赔率三色：`<1.2` 灰 / `1.2~1.4` 蓝 / `>=1.4` 红
- BTC/ETH 金额保留 2 位小数，其他整数

## 汇率架构

两个独立 API，各自缓存：
- **法币**：`exchangerate-api.com/v4/latest/USD` — 缓存 1h，3 次重试
- **加密货币**：`api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum` — 缓存 5min，需 User-Agent header

换算逻辑：USDT/USDC → USD 1:1；BTC/ETH → `val × crypto_price × 6.85`

## 过滤规则

| 条件 | 动作 |
|------|------|
| event 含 "复式" | skip |
| `to_cny()` 返回 0 | `skip(unknown currency)` |
| `amount_cny < threshold` | 不通知 |

## 异常监控

三种异常类型，各需**连续 10 轮**才触发通知：
- `data == []` → 数据提取异常（页面关闭）
- `bets == 0` → 无投注数据
- `new_bets == 0` → feed 停滞

## 环境坑

- **控制台 GBK 编码**：含 `₹`、`ö` 等字符的 `print()` 会 `UnicodeEncodeError`。所有数据输出改用 `logger.info()`，日志写入 `monitor.log`（UTF-8）
- **Git 路径**：`d:\Program Files\Git\cmd\git.exe`（非标准 PATH）
- **PowerShell `$` 转义**：在命令行中 `CA$1,445` 的 `$` 会被 PowerShell 吃掉，用文件测试或单引号包裹

## 调试

```yaml
# config.yaml — 临时降低阈值测试
notifications.rules.cny_threshold: 10000

# config.yaml — 查看币种解析详情
logging.level: "DEBUG"   # 打印 parse_amount 每步路径
```
