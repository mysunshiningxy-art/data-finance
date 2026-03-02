"""京东金融渠道接入

支持两种导入方式：
1. 截图 OCR 识别持仓
2. 手动添加交易记录
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.channels.base import BaseChannel


class JDChannel(BaseChannel):

    @property
    def channel_code(self) -> str:
        return "jd"

    @property
    def channel_name(self) -> str:
        return "京东金融"

    def parse_transactions(self, file_path: Path) -> pd.DataFrame:
        raise NotImplementedError("京东金融暂不支持文件导入，请使用截图 OCR 或手动录入")

    @staticmethod
    def build_manual_record(
        fund_code: str,
        txn_type: str,
        txn_date: date,
        amount: float,
        shares: float = 0,
        nav: float = 0,
        fee: float = 0,
    ) -> dict:
        return {
            "fund_code": fund_code.strip().zfill(6),
            "fund_name": "",
            "txn_type": txn_type,
            "txn_date": txn_date,
            "amount": Decimal(str(amount)),
            "shares": Decimal(str(shares)),
            "nav": Decimal(str(nav)),
            "fee": Decimal(str(fee)),
            "status": "confirmed",
        }
