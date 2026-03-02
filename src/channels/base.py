from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class BaseChannel(ABC):
    """渠道抽象基类，所有渠道需实现此接口"""

    @property
    @abstractmethod
    def channel_code(self) -> str:
        ...

    @property
    @abstractmethod
    def channel_name(self) -> str:
        ...

    @abstractmethod
    def parse_transactions(self, file_path: Path) -> pd.DataFrame:
        """
        解析渠道导出的交易记录文件，返回统一格式的 DataFrame。

        统一字段:
            fund_code   : str   基金代码
            fund_name   : str   基金名称
            txn_type    : str   交易类型 (buy / sell / dividend / bonus)
            txn_date    : date  交易日期
            amount      : Decimal 交易金额
            shares      : Decimal 确认份额
            nav         : Decimal 确认净值
            fee         : Decimal 手续费
            status      : str   交易状态 (confirmed / pending / failed)
        """
        ...
