"""支付宝（蚂蚁财富）渠道接入

支持两种导入方式：
1. 解析支付宝导出的基金交易 CSV/Excel 文件
2. 手动添加交易记录
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.channels.base import BaseChannel

# 支付宝导出文件的列名映射（适配常见导出格式）
_COLUMN_MAP = {
    "基金代码": "fund_code",
    "基金名称": "fund_name",
    "交易类型": "txn_type",
    "交易时间": "txn_date",
    "交易日期": "txn_date",
    "交易金额": "amount",
    "金额": "amount",
    "确认份额": "shares",
    "份额": "shares",
    "确认净值": "nav",
    "净值": "nav",
    "手续费": "fee",
    "交易状态": "status",
    "状态": "status",
}

_TXN_TYPE_MAP = {
    "买入": "buy",
    "申购": "buy",
    "定投买入": "buy",
    "卖出": "sell",
    "赎回": "sell",
    "分红": "dividend",
    "红利再投": "bonus",
    "红利再投资": "bonus",
}

_STATUS_MAP = {
    "交易成功": "confirmed",
    "确认成功": "confirmed",
    "已确认": "confirmed",
    "交易中": "pending",
    "待确认": "pending",
    "交易失败": "failed",
    "已撤单": "failed",
}


class AlipayChannel(BaseChannel):

    @property
    def channel_code(self) -> str:
        return "alipay"

    @property
    def channel_name(self) -> str:
        return "支付宝"

    def parse_transactions(self, file_path: Path) -> pd.DataFrame:
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()

        if suffix == ".csv":
            df = pd.read_csv(file_path, dtype=str)
        elif suffix in (".xls", ".xlsx"):
            df = pd.read_excel(file_path, dtype=str)
        else:
            raise ValueError(f"不支持的文件格式: {suffix}，请使用 CSV 或 Excel")

        df.columns = df.columns.str.strip()
        df = df.rename(columns=_COLUMN_MAP)

        required = {"fund_code", "txn_type", "txn_date", "amount"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"文件缺少必要列: {missing}")

        df["fund_code"] = df["fund_code"].str.strip().str.zfill(6)

        if "fund_name" not in df.columns:
            df["fund_name"] = ""
        df["fund_name"] = df["fund_name"].fillna("").str.strip()

        df["txn_type"] = df["txn_type"].str.strip().map(
            lambda v: _TXN_TYPE_MAP.get(v, v)
        )
        df["txn_date"] = pd.to_datetime(df["txn_date"]).dt.date

        for col in ("amount", "shares", "nav", "fee"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            else:
                df[col] = 0

        if "status" in df.columns:
            df["status"] = df["status"].str.strip().map(
                lambda v: _STATUS_MAP.get(v, "confirmed")
            )
        else:
            df["status"] = "confirmed"

        return df[
            [
                "fund_code", "fund_name", "txn_type", "txn_date",
                "amount", "shares", "nav", "fee", "status",
            ]
        ]

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
        """构造一条手动录入的交易记录"""
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
