"""
通知模块 - 支持企业微信和钉钉 Webhook
"""

import requests
import logging

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("enabled", True)

    def send(self, title: str, data: list[dict]):
        if not self.enabled:
            return

        wecom_cfg = self.config.get("wecom", {})
        dingtalk_cfg = self.config.get("dingtalk", {})

        if wecom_cfg.get("enabled"):
            self._send_wecom(wecom_cfg["webhook_url"], title, data)

        if dingtalk_cfg.get("enabled"):
            self._send_dingtalk(dingtalk_cfg["webhook_url"], title, data)

    def _format_data(self, title: str, data: list[dict]) -> str:
        """将数据格式化为 Markdown 通知内容"""
        lines = [f"## {title}", ""]
        for item in data:
            for key, value in item.items():
                lines.append(f"- **{key}**: {value}")
            lines.append("")
        return "\n".join(lines)

    def _send_wecom(self, webhook_url: str, title: str, data: list[dict]):
        """企业微信通知，内容超过 4096 字节时分条发送"""
        wecom_max = self.config.get("wecom", {}).get("max_bytes", 4096)
        chunks = self._split_wecom_chunks(title, data, wecom_max)
        for i, content in enumerate(chunks):
            suffix = f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": content + suffix},
            }
            try:
                resp = requests.post(webhook_url, json=payload, timeout=10)
                if resp.status_code == 200:
                    logger.info(f"企业微信通知发送成功: {title}{suffix}")
                else:
                    logger.warning(f"企业微信通知失败: {resp.text}")
            except Exception as e:
                logger.error(f"企业微信通知异常: {e}")

    def _split_wecom_chunks(self, title: str, data: list[dict], max_bytes: int) -> list[str]:
        """将投注数据按字节数分块"""
        header = f"## {title}\n\n"
        header_bytes = len(header.encode("utf-8"))

        chunks = []
        current = header
        current_bytes = header_bytes

        for item in data:
            block_lines = self._format_one_bet(item)
            block = "\n".join(block_lines) + "\n"
            block_bytes = len(block.encode("utf-8"))

            if current_bytes + block_bytes > max_bytes and current != header:
                chunks.append(current.rstrip())
                current = header
                current_bytes = header_bytes

            current += block
            current_bytes += block_bytes

        if current != header:
            chunks.append(current.rstrip())

        return chunks

    def _format_one_bet(self, item: dict) -> list[str]:
        """格式化单条投注"""
        odds_raw = item.get('odds', '')
        try:
            odds_val = float(odds_raw or '0')
        except ValueError:
            odds_val = 0
        if odds_val < 1.2:
            odds_color = "comment"
        elif odds_val < 1.4:
            odds_color = "info"
        else:
            odds_color = "warning"

        lines = [
            f"> **赛事**: {item.get('event', '')}",
            f"> **玩家**: {item.get('player', '')}",
            f"> **时间**: {item.get('time', '')}",
            f'> **赔率**: <font color=\"{odds_color}\">{item.get("odds", "")}{"x" if odds_val > 0 else ""}</font>',
            f"> **金额**: <font color=\"warning\">{item.get('amount', '')}</font>",
            f"> **CNY**: <font color=\"warning\">{item.get('cny', '')}</font>",
        ]
        sl = item.get("share_link", "")
        if sl:
            lines.append(f"> **分享**: {sl}")
        lines.append("")
        return lines

    def _format_wecom_md(self, title: str, data: list[dict]) -> str:
        """企业微信 Markdown 格式（完整版，用于小批量）"""
        lines = [f"## {title}", ""]
        for item in data:
            lines.extend(self._format_one_bet(item))
        return "\n".join(lines)

    def _send_dingtalk(self, webhook_url: str, title: str, data: list[dict]):
        content = self._format_data(title, data)
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
        }
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info(f"钉钉通知发送成功: {title}")
            else:
                logger.warning(f"钉钉通知失败: {resp.text}")
        except Exception as e:
            logger.error(f"钉钉通知异常: {e}")
