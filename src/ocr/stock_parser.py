"""股票持仓截图 OCR 解析器 — 平安证券布局

平安证券持仓截图布局（4 列 × 2 行为一组）：
Row 1:  名称       现价        总盈亏金额     当日盈亏金额
Row 2:  市值       成本价      总盈亏率       当日盈亏率
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


@dataclass
class ParsedStockHolding:
    stock_name: str = ""
    market_value: float = 0.0
    current_price: float = 0.0
    cost_price: float = 0.0
    total_profit: float = 0.0
    total_profit_rate: float = 0.0
    daily_profit: float = 0.0
    daily_profit_rate: float = 0.0
    stock_code: str = ""


_RE_NUMBER = re.compile(r'^[+\-]?[\d,]+\.?\d*$')
_RE_PERCENTAGE = re.compile(r'^[+\-]?[\d,]+\.?\d*%$')
_HEADER_KEYWORDS = {"市值", "成本价", "总盈亏", "当日盈亏", "持仓"}
_BOTTOM_KEYWORDS = {"首页", "行情", "自选", "交易", "理财", "我的"}


def _cx(bbox):
    return (bbox[0][0] + bbox[2][0]) / 2

def _cy(bbox):
    return (bbox[0][1] + bbox[2][1]) / 2


def _parse_number(text: str) -> float:
    s = text.replace(',', '').replace('%', '').replace('+', '').strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _is_stock_name(text: str) -> bool:
    """判断是否为股票名称（非数字、非标签）"""
    text = text.strip()
    if not text or len(text) < 2:
        return False
    if _RE_NUMBER.match(text) or _RE_PERCENTAGE.match(text):
        return False
    if text in _HEADER_KEYWORDS or text in _BOTTOM_KEYWORDS:
        return False
    for kw in ['普通', '信用', '期权', '模拟', '快捷服务', '撤单', '查询', '成本价']:
        if kw in text:
            return False
    return True


def parse_screenshot(image_input) -> list[ParsedStockHolding]:
    """解析平安证券持仓截图"""
    if not OCR_AVAILABLE:
        raise RuntimeError("OCR 依赖未安装")

    if isinstance(image_input, (str, bytes)):
        from pathlib import Path
        img = Image.open(image_input if isinstance(image_input, str) else Path(image_input))
    else:
        img = image_input

    ocr = _get_ocr()
    result, _ = ocr(img)
    if not result:
        return []

    blocks = [(bbox, text.strip(), score) for bbox, text, score in result if text.strip()]
    img_h = img.size[1]

    # Detect bottom nav area
    bottom_y = img_h
    for bbox, text, _ in blocks:
        if text in _BOTTOM_KEYWORDS:
            y = _cy(bbox)
            if y > img_h * 0.8:
                bottom_y = min(bottom_y, bbox[0][1])

    # Detect header area — find "市值" or "成本价" header row
    header_y = 0
    for bbox, text, _ in blocks:
        if text.startswith('市值') or text.startswith('成本价'):
            y = _cy(bbox)
            if y < img_h * 0.3:
                header_y = max(header_y, bbox[2][1])

    # Filter to content blocks only
    content_blocks = [
        (bbox, text, score) for bbox, text, score in blocks
        if _cy(bbox) > header_y and _cy(bbox) < bottom_y
    ]

    if not content_blocks:
        return []

    # Group into rows by Y coordinate
    rows: list[list[tuple]] = []
    content_blocks.sort(key=lambda b: _cy(b[0]))
    current_row = [content_blocks[0]]
    for b in content_blocks[1:]:
        if abs(_cy(b[0]) - _cy(current_row[0][0])) < 30:
            current_row.append(b)
        else:
            rows.append(sorted(current_row, key=lambda b: _cx(b[0])))
            current_row = [b]
    if current_row:
        rows.append(sorted(current_row, key=lambda b: _cx(b[0])))

    # Parse row pairs
    results: list[ParsedStockHolding] = []
    i = 0
    while i < len(rows):
        row = rows[i]

        # Check if first element of this row is a stock name
        first_text = row[0][1] if row else ""
        if not _is_stock_name(first_text):
            i += 1
            continue

        # Row 1: name | current_price | total_pnl | daily_pnl
        holding = ParsedStockHolding()
        holding.stock_name = first_text

        nums_r1 = [_parse_number(b[1]) for b in row[1:] if _RE_NUMBER.match(b[1])]
        if len(nums_r1) >= 1:
            holding.current_price = nums_r1[0]
        if len(nums_r1) >= 2:
            holding.total_profit = nums_r1[1]
        if len(nums_r1) >= 3:
            holding.daily_profit = nums_r1[2]

        # Row 2: market_value | cost_price | total_pnl_rate | daily_pnl_rate
        if i + 1 < len(rows):
            row2 = rows[i + 1]
            row2_first = row2[0][1] if row2 else ""
            if _RE_NUMBER.match(row2_first) or _RE_PERCENTAGE.match(row2_first):
                nums_r2 = []
                pcts_r2 = []
                for b in row2:
                    t = b[1]
                    if _RE_PERCENTAGE.match(t):
                        pcts_r2.append(_parse_number(t))
                    elif _RE_NUMBER.match(t):
                        nums_r2.append(_parse_number(t))

                if len(nums_r2) >= 1:
                    holding.market_value = nums_r2[0]
                if len(nums_r2) >= 2:
                    holding.cost_price = nums_r2[1]
                if len(pcts_r2) >= 1:
                    holding.total_profit_rate = pcts_r2[0]
                if len(pcts_r2) >= 2:
                    holding.daily_profit_rate = pcts_r2[1]

                i += 2
            else:
                i += 1
        else:
            i += 1

        if holding.market_value > 0:
            results.append(holding)

    return results


_stock_cache = None
_fund_cache = None


def _get_stock_list():
    global _stock_cache
    if _stock_cache is None:
        import akshare as ak
        _stock_cache = ak.stock_info_a_code_name()
    return _stock_cache


def _get_fund_list():
    global _fund_cache
    if _fund_cache is None:
        import akshare as ak
        _fund_cache = ak.fund_name_em()
    return _fund_cache


def _is_exchange_traded(code: str) -> bool:
    """场内 ETF/股票代码通常以 5/1 开头（SH）或 0/3 开头（SZ）"""
    return code.startswith(('5', '1', '0', '3', '6'))


def search_stock_code(name: str) -> dict | None:
    """通过名称搜索股票代码（A 股 + 场内 ETF）"""
    name = name.strip()
    if not name:
        return None

    has_etf = 'ETF' in name.upper()

    # 1. Search A-share stocks
    try:
        df = _get_stock_list()
        exact = df[df['name'] == name]
        if not exact.empty:
            r = exact.iloc[0]
            return {"code": r['code'], "name": r['name']}
        partial = df[df['name'].str.contains(name, na=False)]
        if not partial.empty:
            r = partial.iloc[0]
            return {"code": r['code'], "name": r['name']}
    except Exception:
        pass

    # 2. Search via fund list (for ETFs)
    try:
        df = _get_fund_list()

        def _filter_etf(candidates):
            """For brokerage ETFs, exclude 联接 funds and prefer 场内 codes"""
            if has_etf and '联接' not in name:
                candidates = candidates[~candidates['基金简称'].str.contains('联接', na=False)]
                candidates = candidates[candidates['基金简称'].str.contains('ETF', na=False)]
            return candidates

        exact = df[df['基金简称'] == name]
        if not exact.empty:
            r = exact.iloc[0]
            return {"code": r['基金代码'], "name": r['基金简称']}

        partial = _filter_etf(df[df['基金简称'].str.contains(name, na=False)])
        if not partial.empty:
            r = partial.iloc[0]
            return {"code": r['基金代码'], "name": r['基金简称']}

        # Fuzzy: match funds containing all Chinese keywords
        cn_parts = re.findall(r'[\u4e00-\u9fff]+', name)
        if cn_parts:
            mask = df['基金简称'].str.contains(cn_parts[0], na=False)
            for kw in cn_parts[1:]:
                mask = mask & df['基金简称'].str.contains(kw, na=False)
            matched = _filter_etf(df[mask])
            if not matched.empty:
                r = matched.iloc[0]
                return {"code": r['基金代码'], "name": r['基金简称']}

            # Fallback: try each keyword separately and sub-keywords
            cn_parts.sort(key=len, reverse=True)
            for kw in cn_parts:
                if len(kw) >= 2:
                    matched = _filter_etf(df[df['基金简称'].str.contains(kw, na=False)])
                    if not matched.empty:
                        r = matched.iloc[0]
                        return {"code": r['基金代码'], "name": r['基金简称']}
                    # Try sub-keywords (sliding window)
                    for sub_len in range(len(kw) - 1, 1, -1):
                        for start in range(len(kw) - sub_len + 1):
                            sub = kw[start:start + sub_len]
                            matched = _filter_etf(df[df['基金简称'].str.contains(sub, na=False)])
                            if not matched.empty and len(matched) <= 20:
                                r = matched.iloc[0]
                                return {"code": r['基金代码'], "name": r['基金简称']}
    except Exception:
        pass

    return None
