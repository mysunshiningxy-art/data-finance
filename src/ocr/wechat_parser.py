"""微信（腾讯理财通）基金持仓截图 OCR 解析

布局为卡片式，每只基金包含:
  基金名称
  持有金额          持仓收益          昨日收益
  72,613.15        +2,613.15        0.00

解析策略:
  - 以 "持有金额" 文本块为锚点定位每只基金
  - 向上查找最近的非标签文本 → 基金名称
  - 向下查找同一行的数值 → 持有金额 / 持仓收益 / 昨日收益
"""

import re
from dataclasses import dataclass, asdict
from pathlib import Path

from PIL import Image

try:
    from rapidocr_onnxruntime import RapidOCR
    _ocr_engine = None

    def _get_ocr():
        global _ocr_engine
        if _ocr_engine is None:
            _ocr_engine = RapidOCR()
        return _ocr_engine

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

    def _get_ocr():
        raise RuntimeError("请先安装 rapidocr_onnxruntime: pip install rapidocr_onnxruntime")


@dataclass
class ParsedFundHolding:
    fund_name: str
    fund_code: str = ""
    market_value: float | None = None
    daily_return: float | None = None
    profit: float | None = None
    profit_rate: float | None = None


_RE_NUMBER = re.compile(r"^[+-]?\d[\d,]*\.?\d*$")
_LABEL_TEXTS = {"持有金额", "持仓收益", "昨日收益", "累计收益"}
_NOISE_TEXTS = re.compile(
    r"(资产明细|筛选|排序|腾讯理财通|活期|定期|基金市场|我的|^5G|^\d{2}:\d{2})"
)


def _cx(bbox):
    return (bbox[0][0] + bbox[1][0]) / 2


def _cy(bbox):
    return (bbox[0][1] + bbox[2][1]) / 2


def _parse_number(text: str) -> float | None:
    t = text.strip().replace(",", "").replace(" ", "")
    if _RE_NUMBER.match(t):
        return float(t)
    return None


def parse_screenshot(image_input) -> list[dict]:
    ocr = _get_ocr()

    if isinstance(image_input, (str, Path)):
        img = Image.open(image_input)
    elif isinstance(image_input, bytes):
        import io as _io
        img = Image.open(_io.BytesIO(image_input))
    elif isinstance(image_input, Image.Image):
        img = image_input
    else:
        raise ValueError("不支持的图片输入类型")

    import numpy as np
    result, _ = ocr(np.array(img))
    if not result:
        return []

    blocks = [(bbox, text.strip(), conf) for bbox, text, conf in result if text.strip()]

    anchors = []
    for bbox, text, _ in blocks:
        if text == "持有金额":
            anchors.append((_cx(bbox), _cy(bbox), bbox))

    if not anchors:
        return []

    funds: list[ParsedFundHolding] = []

    for anchor_x, anchor_y, anchor_bbox in anchors:
        label_row_blocks = [
            (bbox, text) for bbox, text, _ in blocks
            if abs(_cy(bbox) - anchor_y) < 30
        ]
        col_positions = {}
        for bbox, text in label_row_blocks:
            if text == "持有金额":
                col_positions["amount"] = _cx(bbox)
            elif text == "持仓收益":
                col_positions["profit"] = _cx(bbox)
            elif text == "昨日收益":
                col_positions["daily"] = _cx(bbox)

        fund_name = None
        best_dist = float("inf")
        for bbox, text, _ in blocks:
            by = _cy(bbox)
            dist = anchor_y - by
            if 30 < dist < 200:
                if text in _LABEL_TEXTS or _NOISE_TEXTS.search(text):
                    continue
                if _RE_NUMBER.match(text.replace(",", "")):
                    continue
                if dist < best_dist:
                    best_dist = dist
                    fund_name = text

        if not fund_name:
            continue

        value_blocks = [
            (bbox, text) for bbox, text, _ in blocks
            if abs(_cy(bbox) - (anchor_y + 65)) < 40
        ]

        market_value = None
        profit = None
        daily_return = None

        if col_positions:
            for bbox, text in value_blocks:
                bx = _cx(bbox)
                val = _parse_number(text)
                if val is None:
                    continue

                best_col = None
                best_col_dist = float("inf")
                for col_name, col_x in col_positions.items():
                    d = abs(bx - col_x)
                    if d < best_col_dist:
                        best_col_dist = d
                        best_col = col_name

                if best_col == "amount":
                    market_value = val
                elif best_col == "profit":
                    profit = val
                elif best_col == "daily":
                    daily_return = val
        else:
            nums = []
            for bbox, text in sorted(value_blocks, key=lambda b: _cx(b[0])):
                val = _parse_number(text)
                if val is not None:
                    nums.append(val)
            if len(nums) >= 1:
                market_value = nums[0]
            if len(nums) >= 2:
                profit = nums[1]
            if len(nums) >= 3:
                daily_return = nums[2]

        funds.append(ParsedFundHolding(
            fund_name=fund_name,
            market_value=market_value,
            profit=profit,
            daily_return=daily_return,
        ))

    return [asdict(f) for f in funds if f.fund_name]


def search_fund_code(fund_name: str) -> list[dict]:
    from src.ocr.alipay_parser import search_fund_code as _search
    return _search(fund_name)
