"""基金数据服务 — 基金信息查询 & 净值获取

使用 akshare 从东方财富等公开数据源获取基金信息和历史净值。
"""

from datetime import date, datetime
from decimal import Decimal

import akshare as ak
import pandas as pd
from sqlalchemy.orm import Session

from src.models import Fund, NAVRecord, FundType


# akshare 基金类型关键字 → 内部枚举
_FUND_TYPE_KEYWORDS = {
    "股票": FundType.STOCK.value,
    "债券": FundType.BOND.value,
    "混合": FundType.HYBRID.value,
    "货币": FundType.MONEY.value,
    "指数": FundType.INDEX.value,
    "QDII": FundType.QDII.value,
}

_INDUSTRY_RULES: list[tuple[list[str], str]] = [
    (["军工", "国防"], "军工国防"),
    (["医疗", "医药", "健康", "生物", "创新药"], "医疗健康"),
    (["半导体", "芯片", "集成电路"], "半导体"),
    (["人工智能", "AI", "机器人"], "人工智能"),
    (["科技", "信息技术", "信息产业", "电子", "计算机", "软件"], "科技"),
    (["互联网", "数字经济", "数据"], "互联网"),
    (["新能源", "光伏", "电力", "碳中和", "绿色"], "新能源"),
    (["消费", "白酒", "食品", "家电", "品牌"], "消费"),
    (["金融", "银行", "保险", "非银", "券商"], "金融"),
    (["房地产", "地产", "REITs"], "房地产"),
    (["钢铁", "煤炭", "有色", "资源", "材料", "化工", "石油"], "资源材料"),
    (["黄金", "贵金属"], "黄金"),
    (["农业", "养殖", "种业"], "农业"),
    (["汽车", "车", "智能驾驶"], "汽车"),
    (["传媒", "游戏", "影视", "文化"], "传媒"),
    (["环保", "水务", "公用事业"], "环保公用"),
    (["港股", "恒生", "纳斯达克", "标普", "美国", "海外", "全球", "香港"], "海外"),
    (["沪深300", "上证50", "中证500", "中证1000", "中证A", "创业板", "科创50"], "宽基指数"),
    (["债券", "债", "利率", "信用", "短债", "纯债", "定开"], "债券"),
    (["货币", "现金", "活期"], "货币"),
]


def _guess_fund_type(type_str: str) -> str:
    if not type_str:
        return FundType.OTHER.value
    for kw, ft in _FUND_TYPE_KEYWORDS.items():
        if kw in type_str:
            return ft
    return FundType.OTHER.value


def guess_industry(fund_name: str, fund_type: str = "") -> str:
    """从基金名称和类型推断行业分类"""
    text = fund_name + " " + fund_type
    for keywords, industry in _INDUSTRY_RULES:
        for kw in keywords:
            if kw in text:
                return industry
    if "混合" in text:
        return "混合"
    if "指数" in text:
        return "指数其他"
    if "股票" in text:
        return "股票其他"
    return "其他"


def fetch_fund_info(fund_code: str) -> dict | None:
    """从公开数据源获取基金基本信息"""
    try:
        info = ak.fund_individual_basic_info_xq(symbol=fund_code)
        if info is None or info.empty:
            return None
        data = dict(zip(info["item"], info["value"]))
        name = data.get("基金全称", data.get("基金简称", ""))
        type_str = data.get("基金类型", "")
        return {
            "code": fund_code,
            "name": name,
            "fund_type": _guess_fund_type(type_str),
            "industry": guess_industry(name, type_str),
            "manager": data.get("基金经理", ""),
            "company": data.get("基金管理人", ""),
        }
    except Exception:
        return _fetch_fund_info_fallback(fund_code)


def _fetch_fund_info_fallback(fund_code: str) -> dict | None:
    """备用方式：从基金列表中检索"""
    try:
        all_funds = ak.fund_name_em()
        row = all_funds[all_funds["基金代码"] == fund_code]
        if row.empty:
            return None
        r = row.iloc[0]
        name = r.get("基金简称", "")
        type_str = r.get("基金类型", "")
        return {
            "code": fund_code,
            "name": name,
            "fund_type": _guess_fund_type(type_str),
            "industry": guess_industry(name, type_str),
            "manager": "",
            "company": "",
        }
    except Exception:
        return None


def ensure_fund_exists(session: Session, fund_code: str, fund_name: str = "") -> Fund:
    """确保基金记录存在，不存在则尝试从网络获取并创建"""
    fund = session.get(Fund, fund_code)
    if fund:
        return fund

    info = fetch_fund_info(fund_code)
    if info:
        fund = Fund(**info)
    else:
        name = fund_name or fund_code
        fund = Fund(code=fund_code, name=name, industry=guess_industry(name))

    session.add(fund)
    session.flush()
    return fund


def fetch_nav_history(
    fund_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame | None:
    """获取基金历史净值"""
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is None or df.empty:
            return None

        df = df.rename(columns={"净值日期": "nav_date", "单位净值": "nav", "日增长率": "daily_return"})
        df["nav_date"] = pd.to_datetime(df["nav_date"]).dt.date

        if start_date:
            df = df[df["nav_date"] >= datetime.strptime(start_date, "%Y-%m-%d").date()]
        if end_date:
            df = df[df["nav_date"] <= datetime.strptime(end_date, "%Y-%m-%d").date()]

        return df
    except Exception:
        return None


def sync_nav(session: Session, fund_code: str, days: int = 30) -> int:
    """同步最近 N 天的净值数据到数据库，返回新增记录数"""
    from datetime import timedelta
    end = date.today()
    start = end - timedelta(days=days)
    df = fetch_nav_history(fund_code, start.isoformat(), end.isoformat())
    if df is None or df.empty:
        return 0

    ensure_fund_exists(session, fund_code)

    count = 0
    for _, row in df.iterrows():
        exists = (
            session.query(NAVRecord)
            .filter_by(fund_code=fund_code, nav_date=row["nav_date"])
            .first()
        )
        if exists:
            continue
        rec = NAVRecord(
            fund_code=fund_code,
            nav_date=row["nav_date"],
            nav=Decimal(str(row["nav"])),
            daily_return=(
                Decimal(str(row["daily_return"])) if pd.notna(row.get("daily_return")) else None
            ),
        )
        session.add(rec)
        count += 1

    session.commit()
    return count


def sync_fund_industries(session: Session) -> int:
    """为所有缺少行业分类的基金自动推断行业"""
    funds = session.query(Fund).all()
    count = 0
    for fund in funds:
        industry = guess_industry(fund.name or "", fund.fund_type or "")
        if industry != fund.industry:
            fund.industry = industry
            count += 1
    session.commit()
    return count


def get_latest_nav(session: Session, fund_code: str) -> Decimal | None:
    """获取数据库中最新净值"""
    rec = (
        session.query(NAVRecord)
        .filter_by(fund_code=fund_code)
        .order_by(NAVRecord.nav_date.desc())
        .first()
    )
    return rec.nav if rec else None


def get_latest_nav_with_date(session: Session, fund_code: str) -> tuple[Decimal | None, date | None]:
    """获取数据库中最新净值及其日期"""
    rec = (
        session.query(NAVRecord)
        .filter_by(fund_code=fund_code)
        .order_by(NAVRecord.nav_date.desc())
        .first()
    )
    return (rec.nav, rec.nav_date) if rec else (None, None)


def get_nav_on_date(session: Session, fund_code: str, target_date: date) -> Decimal | None:
    """获取指定日期（或之前最近交易日）的净值"""
    rec = (
        session.query(NAVRecord)
        .filter(NAVRecord.fund_code == fund_code, NAVRecord.nav_date <= target_date)
        .order_by(NAVRecord.nav_date.desc())
        .first()
    )
    return rec.nav if rec else None
