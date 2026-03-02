"""京东金融基金持仓截图 OCR 解析

京东金融的持仓页面布局与支付宝几乎一致（三列两行），
复用通用解析逻辑。
"""

from src.ocr.alipay_parser import (
    OCR_AVAILABLE,
    parse_screenshot,
    search_fund_code,
)

__all__ = ["OCR_AVAILABLE", "parse_screenshot", "search_fund_code"]
