"""测试 notifier.py 格式化与分条"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from notifier import Notifier

def test_format_one_bet():
    config = {"enabled": True, "wecom": {"enabled": False}}
    n = Notifier(config)
    lines = n._format_one_bet({
        "event": "Test Event", "player": "Player1",
        "time": "12:00", "odds": "1.85",
        "amount": "USDT 1,000", "cny": "6,840",
        "share_link": "https://stake.com/bet/123"
    })
    content = "\n".join(lines)
    assert "Test Event" in content
    assert "Player1" in content
    assert "1.85" in content
    assert "USDT 1,000" in content
    assert "6,840" in content
    assert "https://stake.com" in content

def test_format_one_bet_no_link():
    config = {"enabled": True, "wecom": {"enabled": False}}
    n = Notifier(config)
    lines = n._format_one_bet({
        "event": "Test", "player": "P", "time": "12:00",
        "odds": "1.20", "amount": "1,000", "cny": "6,840"
    })
    content = "\n".join(lines)
    assert "share" not in content.lower()

def test_split_chunks():
    config = {"enabled": True, "wecom": {"enabled": False}}
    n = Notifier(config)
    data = [{"event": "E" * 30, "player": "P", "time": "T",
             "odds": "1.5", "amount": "A", "cny": "C"} for _ in range(20)]
    chunks = n._split_wecom_chunks("Test", data, 4096)
    assert len(chunks) > 1  # should split into multiple chunks
    for c in chunks:
        assert len(c.encode("utf-8")) <= 4096

def test_odds_color():
    config = {"enabled": True, "wecom": {"enabled": False}}
    n = Notifier(config)
    # odds < 1.2 → comment (gray)
    lines = n._format_one_bet({"event":"E","player":"P","time":"T","odds":"1.19","amount":"A","cny":"C"})
    assert 'color="comment"' in "".join(lines)
    # odds 1.2~1.4 → info (blue)
    lines = n._format_one_bet({"event":"E","player":"P","time":"T","odds":"1.30","amount":"A","cny":"C"})
    assert 'color="info"' in "".join(lines)
    # odds >= 1.4 → warning (red)
    lines = n._format_one_bet({"event":"E","player":"P","time":"T","odds":"1.50","amount":"A","cny":"C"})
    assert 'color="warning"' in "".join(lines)

print("All notifier tests passed")
