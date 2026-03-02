"""全渠道基金管理系统 — CLI 入口"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from src.database import init_db, get_session
from src.channels.alipay import AlipayChannel
from src.services.fund import ensure_fund_exists, sync_nav, fetch_fund_info
from src.services.portfolio import (
    import_transactions, get_holdings, get_portfolio_summary, ensure_channel,
)
from src.models import Transaction, ChannelType

console = Console()


@click.group()
def cli():
    """全渠道基金管理系统"""
    init_db()


# ===================================================================
# 支付宝相关命令
# ===================================================================

@cli.group("alipay")
def alipay_group():
    """支付宝渠道管理"""
    pass


@alipay_group.command("import")
@click.argument("file_path", type=click.Path(exists=True))
def alipay_import(file_path: str):
    """从支付宝导出文件导入交易记录"""
    channel = AlipayChannel()
    try:
        df = channel.parse_transactions(Path(file_path))
    except Exception as e:
        console.print(f"[red]解析文件失败: {e}[/red]")
        return

    console.print(f"解析到 [cyan]{len(df)}[/cyan] 条交易记录")

    session = get_session()
    try:
        records = df.to_dict("records")
        count = import_transactions(session, records, channel.channel_code, channel.channel_name)
        console.print(f"[green]成功导入 {count} 条交易记录[/green]")
    except Exception as e:
        session.rollback()
        console.print(f"[red]导入失败: {e}[/red]")
    finally:
        session.close()


@alipay_group.command("add")
@click.option("--code", required=True, help="基金代码")
@click.option("--type", "txn_type", required=True, type=click.Choice(["buy", "sell", "dividend", "bonus"]), help="交易类型")
@click.option("--date", "txn_date", required=True, help="交易日期 (YYYY-MM-DD)")
@click.option("--amount", required=True, type=float, help="交易金额")
@click.option("--shares", default=0.0, type=float, help="确认份额")
@click.option("--nav", default=0.0, type=float, help="确认净值")
@click.option("--fee", default=0.0, type=float, help="手续费")
def alipay_add(code, txn_type, txn_date, amount, shares, nav, fee):
    """手动添加支付宝交易记录"""
    channel = AlipayChannel()
    try:
        d = datetime.strptime(txn_date, "%Y-%m-%d").date()
    except ValueError:
        console.print("[red]日期格式错误，请使用 YYYY-MM-DD[/red]")
        return

    record = channel.build_manual_record(code, txn_type, d, amount, shares, nav, fee)
    session = get_session()
    try:
        count = import_transactions(session, [record], channel.channel_code, channel.channel_name)
        console.print(f"[green]成功添加 {count} 条交易记录[/green]")
    except Exception as e:
        session.rollback()
        console.print(f"[red]添加失败: {e}[/red]")
    finally:
        session.close()


# ===================================================================
# 基金信息
# ===================================================================

@cli.group("fund")
def fund_group():
    """基金信息管理"""
    pass


@fund_group.command("info")
@click.argument("fund_code")
def fund_info(fund_code: str):
    """查询基金信息"""
    info = fetch_fund_info(fund_code.zfill(6))
    if not info:
        console.print(f"[yellow]未找到基金 {fund_code} 的信息[/yellow]")
        return

    table = Table(title=f"基金信息 - {fund_code}", box=box.ROUNDED)
    table.add_column("字段", style="cyan")
    table.add_column("值")

    labels = {"code": "基金代码", "name": "基金名称", "fund_type": "基金类型", "manager": "基金经理", "company": "基金公司"}
    for k, label in labels.items():
        table.add_row(label, str(info.get(k, "")))

    console.print(table)


@fund_group.command("sync-nav")
@click.argument("fund_code")
@click.option("--days", default=30, help="同步最近 N 天的净值")
def fund_sync_nav(fund_code: str, days: int):
    """同步基金净值数据"""
    session = get_session()
    try:
        count = sync_nav(session, fund_code.zfill(6), days)
        console.print(f"[green]同步完成，新增 {count} 条净值记录[/green]")
    except Exception as e:
        console.print(f"[red]同步失败: {e}[/red]")
    finally:
        session.close()


# ===================================================================
# 持仓 & 收益
# ===================================================================

@cli.command("holdings")
@click.option("--channel", default=None, help="按渠道过滤 (如 alipay)")
def show_holdings(channel: str | None):
    """查看持仓明细"""
    session = get_session()
    try:
        holdings = get_holdings(session, channel)
        if not holdings:
            console.print("[yellow]暂无持仓记录[/yellow]")
            return

        table = Table(title="持仓明细", box=box.ROUNDED)
        table.add_column("渠道", style="magenta")
        table.add_column("基金代码", style="cyan")
        table.add_column("基金名称")
        table.add_column("持有份额", justify="right")
        table.add_column("持仓成本", justify="right")
        table.add_column("成本净值", justify="right")
        table.add_column("最新净值", justify="right")
        table.add_column("市值", justify="right")
        table.add_column("盈亏", justify="right")
        table.add_column("收益率", justify="right")

        for h in holdings:
            profit_style = ""
            if h.profit is not None:
                profit_style = "green" if h.profit >= 0 else "red"

            table.add_row(
                h.channel_name,
                h.fund_code,
                h.fund_name,
                f"{h.shares:.2f}",
                f"{h.cost_amount:.2f}",
                f"{h.cost_nav:.4f}" if h.cost_nav else "-",
                f"{h.latest_nav:.4f}" if h.latest_nav else "-",
                f"{h.market_value:.2f}" if h.market_value else "-",
                f"[{profit_style}]{h.profit:.2f}[/{profit_style}]" if h.profit is not None else "-",
                f"[{profit_style}]{h.profit_rate:.2f}%[/{profit_style}]" if h.profit_rate is not None else "-",
            )

        console.print(table)
    finally:
        session.close()


@cli.command("summary")
def show_summary():
    """查看投资组合汇总"""
    session = get_session()
    try:
        s = get_portfolio_summary(session)
        if s["fund_count"] == 0:
            console.print("[yellow]暂无持仓记录[/yellow]")
            return

        profit_style = "green" if (s["total_profit"] or 0) >= 0 else "red"

        panel_text = (
            f"持有基金数: [cyan]{s['fund_count']}[/cyan]\n"
            f"总投入成本: [white]{s['total_cost']:.2f}[/white]\n"
            f"当前总市值: [white]{s['total_market_value']:.2f}[/white]\n"
            f"总盈亏金额: [{profit_style}]{s['total_profit']:.2f}[/{profit_style}]\n"
            f"总收益率:   [{profit_style}]{s['total_profit_rate']:.2f}%[/{profit_style}]"
        )
        console.print(Panel(panel_text, title="投资组合汇总", box=box.ROUNDED))

        console.print()
        show_holdings.invoke(click.Context(show_holdings))
    except Exception:
        holdings = s.get("holdings", [])
        if holdings:
            console.print(f"\n共 {len(holdings)} 只基金")
    finally:
        session.close()


@cli.command("transactions")
@click.option("--code", default=None, help="按基金代码过滤")
@click.option("--channel", default=None, help="按渠道过滤")
@click.option("--limit", default=20, help="显示条数")
def show_transactions(code: str | None, channel: str | None, limit: int):
    """查看交易记录"""
    session = get_session()
    try:
        from src.models import Channel as ChannelModel
        query = session.query(Transaction).order_by(Transaction.txn_date.desc())

        if code:
            query = query.filter(Transaction.fund_code == code.zfill(6))
        if channel:
            ch = session.query(ChannelModel).filter_by(code=channel).first()
            if ch:
                query = query.filter(Transaction.channel_id == ch.id)

        txns = query.limit(limit).all()
        if not txns:
            console.print("[yellow]暂无交易记录[/yellow]")
            return

        type_labels = {"buy": "买入", "sell": "卖出", "dividend": "分红", "bonus": "红利再投"}

        table = Table(title=f"交易记录 (最近 {limit} 条)", box=box.ROUNDED)
        table.add_column("日期", style="cyan")
        table.add_column("渠道", style="magenta")
        table.add_column("基金代码")
        table.add_column("类型")
        table.add_column("金额", justify="right")
        table.add_column("份额", justify="right")
        table.add_column("净值", justify="right")
        table.add_column("手续费", justify="right")

        for t in txns:
            ch_name = t.channel.name if t.channel else ""
            table.add_row(
                str(t.txn_date),
                ch_name,
                t.fund_code,
                type_labels.get(t.txn_type, t.txn_type),
                f"{t.amount:.2f}",
                f"{t.shares:.2f}" if t.shares else "-",
                f"{t.nav:.4f}" if t.nav else "-",
                f"{t.fee:.2f}" if t.fee else "0.00",
            )

        console.print(table)
    finally:
        session.close()


if __name__ == "__main__":
    cli()
