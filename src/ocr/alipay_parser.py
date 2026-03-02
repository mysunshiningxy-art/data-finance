"""基金持仓截图 OCR 解析 (通用)

支持支付宝（蚂蚁财富）和京东金融等类似三列两行布局的基金持仓截图。

截图布局 (每只基金2行, 3列):
  名称/基金名称       金额/昨日收益       持有收益/率 | 持仓收益/率
  ─────────────────────────────────────────────────
  易方达中证军工指数    128.16            +28.16
  (LOF)A             +2.17             +28.16%
  定投 / 屡创新高No.6                                ← 标签行(可选, 需过滤)

解析策略:
  - 中列出现无符号数字(如 128.16) → 新基金第1行, 该数字=金额
  - 中列出现有符号数字(如 +2.17) → 当前基金第2行, 该数字=昨日收益
  - 中列出现 0.00 + 右列为百分比 → 当前基金第2行(昨日收益=0)
  - 右列第1行=持有收益(金额), 第2行=持有收益率(带%)
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
    market_value: float | None = None     # 金额
    daily_return: float | None = None     # 昨日收益 (金额)
    profit: float | None = None           # 持有收益 (金额)
    profit_rate: float | None = None      # 持有收益率 (%)


_RE_PLAIN_NUMBER = re.compile(r"^(\d[\d,]*\.\d+)$")
_RE_SIGNED_NUMBER = re.compile(r"^([+-]\d[\d,]*\.?\d*)$")
_RE_PERCENTAGE = re.compile(r"^([+-]?\d[\d,]*\.?\d*)%$")


def _block_center_x(bbox):
    return (bbox[0][0] + bbox[1][0]) / 2


def _block_center_y(bbox):
    return (bbox[0][1] + bbox[2][1]) / 2


def _group_into_rows(blocks: list, y_threshold: float = 30.0) -> list[list]:
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: (_block_center_y(b[0]), _block_center_x(b[0])))
    rows: list[list] = []
    current_row = [sorted_blocks[0]]
    current_y = _block_center_y(sorted_blocks[0][0])

    for block in sorted_blocks[1:]:
        by = _block_center_y(block[0])
        if abs(by - current_y) < y_threshold:
            current_row.append(block)
        else:
            current_row.sort(key=lambda b: _block_center_x(b[0]))
            rows.append(current_row)
            current_row = [block]
            current_y = by

    if current_row:
        current_row.sort(key=lambda b: _block_center_x(b[0]))
        rows.append(current_row)

    return rows


_NOISE_PATTERNS = re.compile(
    r"(定投|屡创新高|热门定投榜|金选|指数基金[🔥💰]?|No\.\d|基金经理说)"
)


def _is_noise_text(text: str) -> bool:
    """判断左列文本是否为标签/噪音而非基金名称后缀"""
    t = text.strip()
    if not t:
        return True
    return bool(_NOISE_PATTERNS.search(t))


def _detect_columns(blocks: list, img_width: int) -> tuple[float, float]:
    """从表头行自适应检测列分界线, 返回 (左-中分界, 中-右分界)"""
    header_positions = {}
    for bbox, text, _ in blocks:
        t = text.strip()
        if t in ("名称", "基金名称"):
            header_positions["left"] = _block_center_x(bbox)
        elif "金额" in t and "收益" in t:
            header_positions["center"] = _block_center_x(bbox)
        elif ("持有收益" in t or "持仓收益" in t) and ("率" in t or "%" in t):
            header_positions["right"] = _block_center_x(bbox)

    if "left" in header_positions and "center" in header_positions:
        col_lc = (header_positions["left"] + header_positions["center"]) / 2
    else:
        col_lc = img_width * 0.40

    if "center" in header_positions and "right" in header_positions:
        col_cr = (header_positions["center"] + header_positions["right"]) / 2
    else:
        col_cr = img_width * 0.75

    return col_lc, col_cr


def _detect_content_area(blocks: list, img_height: int) -> tuple[float, float]:
    """检测数据内容区域的上下边界"""
    content_top = 0.0
    content_bottom = float(img_height)

    for bbox, text, _ in blocks:
        t = text.strip()
        if t in ("名称", "基金名称") or ("金额" in t and "收益" in t):
            content_top = max(content_top, bbox[2][1])
        bottom_nav_keywords = ("基金市场", "机会", "全球投资", "基金圈")
        if t in bottom_nav_keywords and _block_center_y(bbox) > img_height * 0.85:
            content_bottom = min(content_bottom, bbox[0][1])

    return content_top, content_bottom


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
    img_array = np.array(img)
    result, _ = ocr(img_array)

    if not result:
        return []

    all_blocks = [(bbox, text.strip(), conf) for bbox, text, conf in result if text.strip()]
    col_lc, col_cr = _detect_columns(all_blocks, img.width)
    content_top, content_bottom = _detect_content_area(all_blocks, img.height)

    content_blocks = [
        b for b in all_blocks
        if content_top < _block_center_y(b[0]) < content_bottom
    ]
    if not content_blocks:
        content_blocks = all_blocks

    rows = _group_into_rows(content_blocks, y_threshold=30.0)

    parsed_rows = []
    for row in rows:
        left_parts, center_parts, right_parts = [], [], []
        for bbox, text, _ in row:
            cx = _block_center_x(bbox)
            if cx < col_lc:
                left_parts.append(text)
            elif cx > col_cr:
                right_parts.append(text)
            else:
                center_parts.append(text)

        parsed_rows.append({
            "left": " ".join(left_parts),
            "center": " ".join(center_parts),
            "right": " ".join(right_parts),
        })

    funds: list[ParsedFundHolding] = []
    current: ParsedFundHolding | None = None

    for row in parsed_rows:
        left = row["left"].strip()
        center = row["center"].strip().replace(",", "").replace(" ", "")
        right = row["right"].strip().replace(",", "").replace(" ", "")

        if not center and not right:
            continue

        if "基金经理说" in center or "基金经理说" in left:
            continue

        m_plain = _RE_PLAIN_NUMBER.match(center)
        m_right_pct = _RE_PERCENTAGE.match(right) if right else None

        if m_plain and m_right_pct and current is not None:
            # Row 2: right column is percentage (profit rate) → continuation
            # center = daily return (e.g. "0.00"), right = profit rate (e.g. "+30.17%")
            current.daily_return = float(m_plain.group(1))
            current.profit_rate = float(m_right_pct.group(1))
            if left and not _is_noise_text(left):
                current.fund_name += left
            continue

        if m_plain:
            if current:
                funds.append(current)

            market_value = float(m_plain.group(1))
            profit = None

            m_signed = _RE_SIGNED_NUMBER.match(right)
            if m_signed:
                profit = float(m_signed.group(1))
            elif right:
                m_num = re.search(r"([+-]?\d[\d,]*\.?\d*)", right)
                if m_num:
                    profit = float(m_num.group(1))

            current = ParsedFundHolding(
                fund_name=left,
                market_value=market_value,
                profit=profit,
            )
            continue

        if current is None:
            continue

        m_signed = _RE_SIGNED_NUMBER.match(center)
        if m_signed:
            current.daily_return = float(m_signed.group(1))
            if left and not _is_noise_text(left):
                current.fund_name += left

            if m_right_pct:
                current.profit_rate = float(m_right_pct.group(1))
            continue

    if current:
        funds.append(current)

    return [asdict(f) for f in funds if f.fund_name]


def _normalize_fund_name(name: str) -> str:
    """去除基金名称中的类型后缀，便于模糊搜索"""
    name = name.replace("（", "(").replace("）", ")")
    name = re.sub(r"\(LOF\)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\(QDII[-‐]?LOF\)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\(QDII\)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[A-E]$", "", name)
    return name.strip()


def _detect_share_class(name: str) -> str:
    m = re.search(r"([A-E])$", name.strip())
    return m.group(1) if m else ""


def search_fund_code(fund_name: str) -> list[dict]:
    """根据基金名称搜索基金代码，支持渐进式模糊匹配"""
    try:
        import akshare as ak
        all_funds = ak.fund_name_em()
        share_class = _detect_share_class(fund_name)

        normalized = _normalize_fund_name(fund_name)
        candidates = [fund_name, normalized]
        if len(normalized) > 6:
            candidates.append(normalized[:len(normalized) - 2])

        for query in candidates:
            if not query:
                continue
            escaped = re.escape(query)
            matches = all_funds[
                all_funds["基金简称"].str.contains(escaped, case=False, na=False)
            ]
            if not matches.empty:
                results = [
                    {"code": r["基金代码"], "name": r["基金简称"], "type": r.get("基金类型", "")}
                    for _, r in matches.head(20).iterrows()
                ]
                if share_class:
                    results.sort(
                        key=lambda r: (0 if r["name"].rstrip().endswith(share_class) else 1)
                    )
                return results[:10]
        return []
    except Exception:
        return []
