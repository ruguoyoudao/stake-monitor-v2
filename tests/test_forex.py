"""测试 forex.py 币种解析与汇率转换"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from forex import parse_amount, to_cny, _hardcoded_rates

def test_parse_amount_pure_num():
    assert parse_amount("1000") == (1000.0, "USD")
    assert parse_amount("1,234.56") == (1234.56, "USD")

def test_parse_amount_symbol_prefix():
    assert parse_amount("CA$1,445.00") == (1445.0, "CAD")
    assert parse_amount("₹100,000") == (100000.0, "INR")

def test_parse_amount_code_suffix():
    assert parse_amount("100 USDT") == (100.0, "USDT")
    assert parse_amount("0.0015 btc") == (0.0015, "BTC")
    assert parse_amount("2.86eth") == (2.86, "ETH")

def test_parse_amount_code_prefix():
    assert parse_amount("INR100000") == (100000.0, "INR")

def test_parse_amount_keyword():
    assert parse_amount("USDC 10,000") == (10000.0, "USDC")

def test_parse_amount_fallback():
    v, c = parse_amount("ABC 500")
    assert c == "USD"

def test_to_cny_usd():
    r = to_cny("1000")
    assert r > 0
    assert abs(r - 1000 * 6.84) < 1  # approximate

def test_to_cny_btc():
    r = to_cny("0.01 BTC", "BTC")
    assert r > 100  # should be significant

def test_to_cny_unknown():
    r = to_cny("500 SOL", "SOL")
    assert r == 0

def test_hardcoded_rates():
    rates = _hardcoded_rates()
    assert "CNY" in rates
    assert "EUR" in rates

print("All forex tests passed")
