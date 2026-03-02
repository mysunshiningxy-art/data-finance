"""全渠道基金管理系统 — Web 应用"""

import io
import traceback
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.database import init_db, get_session
from src.models import Channel, Holding, Transaction, TransactionStatus, Fund, NAVRecord
from src.channels.alipay import AlipayChannel
from src.channels.jd import JDChannel
from src.channels.wechat import WeChatChannel
from src.services.fund import ensure_fund_exists, sync_nav, fetch_fund_info, get_latest_nav, get_nav_on_date, sync_fund_industries
from src.services.portfolio import (
    import_transactions, get_holdings, get_portfolio_summary, ensure_channel,
    HoldingSummary,
)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(application):
    init_db()
    yield

app = FastAPI(title="全渠道基金管理系统", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------------------------------------------------------------------
# API — Portfolio
# ---------------------------------------------------------------------------

def _holding_to_dict(h: HoldingSummary) -> dict:
    return {
        "holding_id": h.holding_id,
        "fund_code": h.fund_code,
        "fund_name": h.fund_name,
        "channel_name": h.channel_name,
        "industry": h.industry,
        "shares": float(h.shares),
        "cost_amount": float(h.cost_amount),
        "cost_nav": float(h.cost_nav) if h.cost_nav else None,
        "latest_nav": float(h.latest_nav) if h.latest_nav else None,
        "latest_nav_date": h.latest_nav_date,
        "market_value": float(h.market_value) if h.market_value else None,
        "profit": float(h.profit) if h.profit is not None else None,
        "profit_rate": float(h.profit_rate) if h.profit_rate is not None else None,
    }


@app.get("/api/summary")
def api_summary():
    session = get_session()
    try:
        s = get_portfolio_summary(session)
        return {
            "fund_count": s["fund_count"],
            "total_cost": float(s["total_cost"]),
            "total_market_value": float(s["total_market_value"]),
            "total_profit": float(s["total_profit"]) if s["total_profit"] else 0,
            "total_profit_rate": float(s["total_profit_rate"]) if s["total_profit_rate"] else 0,
            "holdings": [_holding_to_dict(h) for h in s["holdings"]],
        }
    finally:
        session.close()


@app.post("/api/ai/analyze")
def api_ai_analyze():
    """AI 智能持仓分析"""
    from src.services.ai_analysis import analyze_portfolio
    session = get_session()
    try:
        s = get_portfolio_summary(session)
        holdings = [_holding_to_dict(h) for h in s["holdings"]]
        if not holdings:
            return JSONResponse(status_code=400, content={"error": "暂无持仓数据"})
        result = analyze_portfolio(holdings)
        return {"success": True, "content": result}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"AI 分析失败: {str(e)}"})
    finally:
        session.close()


@app.get("/api/holdings")
def api_holdings(channel: str | None = None):
    session = get_session()
    try:
        holdings = get_holdings(session, channel)
        return [_holding_to_dict(h) for h in holdings]
    finally:
        session.close()


@app.get("/api/transactions")
def api_transactions(code: str | None = None, channel: str | None = None, limit: int = 50):
    session = get_session()
    try:
        query = session.query(Transaction).order_by(Transaction.txn_date.desc())
        if code:
            query = query.filter(Transaction.fund_code == code.zfill(6))
        if channel:
            ch = session.query(Channel).filter_by(code=channel).first()
            if ch:
                query = query.filter(Transaction.channel_id == ch.id)

        txns = query.limit(limit).all()
        type_labels = {"buy": "买入", "sell": "卖出", "dividend": "分红", "bonus": "红利再投", "adjust": "调整"}

        return [
            {
                "id": t.id,
                "fund_code": t.fund_code,
                "fund_name": t.fund.name if t.fund else "",
                "channel_name": t.channel.name if t.channel else "",
                "txn_type": t.txn_type,
                "txn_type_label": type_labels.get(t.txn_type, t.txn_type),
                "txn_date": t.txn_date.isoformat(),
                "amount": float(t.amount),
                "shares": float(t.shares) if t.shares else 0,
                "nav": float(t.nav) if t.nav else 0,
                "fee": float(t.fee) if t.fee else 0,
                "status": t.status,
            }
            for t in txns
        ]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# API — Transactions
# ---------------------------------------------------------------------------

@app.post("/api/transactions")
def api_add_transaction(
    fund_code: str = Form(...),
    txn_type: str = Form(...),
    txn_date: str = Form(...),
    amount: float = Form(...),
    shares: float = Form(0),
    nav: float = Form(0),
    fee: float = Form(0),
    channel_code: str = Form("alipay"),
):
    channel_map = {"alipay": ("alipay", "支付宝"), "tiantian": ("tiantian", "天天基金"), "jd": ("jd", "京东金融"), "wechat": ("wechat", "微信理财通")}
    ch_code, ch_name = channel_map.get(channel_code, (channel_code, channel_code))

    record = AlipayChannel.build_manual_record(
        fund_code, txn_type,
        datetime.strptime(txn_date, "%Y-%m-%d").date(),
        amount, shares, nav, fee,
    )

    session = get_session()
    try:
        count = import_transactions(session, [record], ch_code, ch_name)
        return {"success": True, "count": count}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# API — Fund Info & NAV
# ---------------------------------------------------------------------------

@app.get("/api/fund/{fund_code}/info")
def api_fund_info(fund_code: str):
    info = fetch_fund_info(fund_code.zfill(6))
    if not info:
        return JSONResponse(status_code=404, content={"error": "未找到该基金"})
    return info


@app.post("/api/fund/{fund_code}/sync")
def api_sync_nav(fund_code: str, days: int = 30):
    session = get_session()
    try:
        count = sync_nav(session, fund_code.zfill(6), days)
        return {"success": True, "count": count}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


@app.get("/api/fund/search")
def api_search_fund(q: str):
    from src.ocr.alipay_parser import search_fund_code
    return search_fund_code(q)


@app.post("/api/fund/sync-industries")
def api_sync_industries():
    """自动推断所有基金的行业分类"""
    session = get_session()
    try:
        count = sync_fund_industries(session)
        return {"success": True, "count": count}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# API — OCR Screenshot Import
# ---------------------------------------------------------------------------

def _get_parser(channel: str):
    """根据渠道返回对应的解析模块"""
    if channel == "wechat":
        from src.ocr import wechat_parser as mod
    else:
        from src.ocr import alipay_parser as mod
    return mod


@app.post("/api/ocr/parse")
async def api_ocr_parse(file: UploadFile = File(...), channel: str = Form("alipay")):
    parser = _get_parser(channel)

    if not parser.OCR_AVAILABLE:
        return JSONResponse(
            status_code=400,
            content={"error": "OCR 引擎未安装，请运行: pip install rapidocr_onnxruntime"},
        )

    try:
        contents = await file.read()
        results = parser.parse_screenshot(contents)

        session = get_session()
        try:
            for item in results:
                if not item.get("fund_code") and item.get("fund_name"):
                    matches = parser.search_fund_code(item["fund_name"])
                    if matches:
                        item["fund_code"] = matches[0]["code"]
                        if not item["fund_name"] or len(item["fund_name"]) < len(matches[0]["name"]):
                            item["fund_name"] = matches[0]["name"]

                if item.get("fund_code") and item.get("market_value"):
                    nav = get_latest_nav(session, item["fund_code"])
                    if nav:
                        item["latest_nav"] = float(nav)
                        item["estimated_shares"] = round(item["market_value"] / float(nav), 2)
        finally:
            session.close()

        return {"success": True, "funds": results}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=400, content={"error": f"解析失败: {e}"})


@app.post("/api/import/snapshot")
def api_import_snapshot(data: dict):
    """导入 OCR 解析结果为持仓"""
    funds = data.get("funds", [])
    channel_code = data.get("channel", "alipay")
    channel_name = {"alipay": "支付宝", "tiantian": "天天基金", "jd": "京东金融", "wechat": "微信理财通"}.get(channel_code, channel_code)

    snapshot_date_str = data.get("snapshot_date")
    snapshot_date = (
        datetime.strptime(snapshot_date_str, "%Y-%m-%d").date()
        if snapshot_date_str else date.today()
    )

    session = get_session()
    try:
        channel = ensure_channel(session, channel_code, channel_name)
        imported = 0

        for f in funds:
            code = f.get("fund_code", "").strip()
            if not code:
                continue

            fund = ensure_fund_exists(session, code, f.get("fund_name", ""))

            market_value = Decimal(str(f.get("market_value") or 0))
            profit = Decimal(str(f.get("profit") or 0))
            cost_amount = market_value - profit

            sync_nav(session, code, days=30)
            from datetime import timedelta
            nav_date = snapshot_date - timedelta(days=1)
            nav_val = get_nav_on_date(session, code, nav_date)
            if not nav_val:
                nav_val = get_latest_nav(session, code)


            if nav_val and nav_val > 0:
                shares = (market_value / nav_val).quantize(Decimal("0.01"))
                nav_used = nav_val
            else:
                shares = market_value
                nav_used = Decimal("1")

            holding = (
                session.query(Holding)
                .filter_by(fund_code=code, channel_id=channel.id)
                .first()
            )
            cost_nav = (cost_amount / shares).quantize(Decimal("0.0001")) if shares > 0 else None

            if holding:
                holding.shares = shares
                holding.cost_amount = cost_amount
                holding.cost_nav = cost_nav
            else:
                holding = Holding(
                    fund_code=code,
                    channel_id=channel.id,
                    shares=shares,
                    cost_amount=cost_amount,
                    cost_nav=cost_nav,
                )
                session.add(holding)

            txn = Transaction(
                fund_code=code,
                channel_id=channel.id,
                txn_type="adjust",
                txn_date=snapshot_date,
                amount=market_value,
                shares=shares,
                nav=nav_used,
                fee=Decimal(0),
                status=TransactionStatus.CONFIRMED.value,
                note=f"截图导入 ({snapshot_date})",
            )
            session.add(txn)
            imported += 1

        session.commit()
        return {"success": True, "count": imported}
    except Exception as e:
        session.rollback()
        traceback.print_exc()
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# API — Holdings Delete
# ---------------------------------------------------------------------------

@app.delete("/api/holdings/{holding_id}")
def api_delete_holding(holding_id: int):
    """删除一条持仓记录"""
    session = get_session()
    try:
        holding = session.query(Holding).filter_by(id=holding_id).first()
        if not holding:
            return JSONResponse(status_code=404, content={"error": "持仓记录不存在"})
        session.delete(holding)
        session.commit()
        return {"success": True}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# API — Channels
# ---------------------------------------------------------------------------

@app.get("/api/channels")
def api_channels():
    session = get_session()
    try:
        channels = session.query(Channel).all()
        return [{"code": c.code, "name": c.name} for c in channels]
    finally:
        session.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
