"""全渠道基金管理系统 — Web 应用"""

import io
import traceback
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Request, Body
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
    """AI 智能持仓分析（基金+股票）"""
    from src.services.ai_analysis import analyze_portfolio
    session = get_session()
    try:
        fund_s = get_portfolio_summary(session)
        fund_holdings = [_holding_to_dict(h) for h in fund_s["holdings"]]
        stock_s = get_stock_summary(session)
        stock_holdings = [{**_stock_holding_to_dict(h), "asset_type": "stock"} for h in stock_s["holdings"]]
        all_holdings = [{**h, "asset_type": "fund"} for h in fund_holdings] + stock_holdings
        if not all_holdings:
            return JSONResponse(status_code=400, content={"error": "暂无持仓数据"})
        result = analyze_portfolio(all_holdings)
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
# API — Stock
# ---------------------------------------------------------------------------

from src.services.stock import (
    fetch_stock_info as _fetch_stock_info,
    ensure_stock_exists, sync_stock_price,
    get_stock_holdings, get_stock_summary,
    add_stock_transaction, StockHoldingSummary,
    ensure_channel as ensure_stock_channel,
    get_latest_price, get_latest_price_with_date as get_stock_price_with_date,
)
from src.models import StockHolding, StockTransaction


def _stock_holding_to_dict(h: StockHoldingSummary) -> dict:
    return {
        "holding_id": h.holding_id,
        "stock_code": h.stock_code,
        "stock_name": h.stock_name,
        "market": h.market,
        "channel_name": h.channel_name,
        "industry": h.industry,
        "shares": float(h.shares),
        "cost_amount": float(h.cost_amount),
        "cost_price": float(h.cost_price) if h.cost_price else None,
        "latest_price": float(h.latest_price) if h.latest_price else None,
        "latest_price_date": h.latest_price_date,
        "market_value": float(h.market_value) if h.market_value else None,
        "profit": float(h.profit) if h.profit is not None else None,
        "profit_rate": float(h.profit_rate) if h.profit_rate is not None else None,
    }


@app.get("/api/stock/summary")
def api_stock_summary():
    session = get_session()
    try:
        s = get_stock_summary(session)
        return {
            "stock_count": s["stock_count"],
            "total_cost": float(s["total_cost"]),
            "total_market_value": float(s["total_market_value"]),
            "total_profit": float(s["total_profit"]) if s["total_profit"] else 0,
            "total_profit_rate": float(s["total_profit_rate"]) if s["total_profit_rate"] else 0,
            "holdings": [_stock_holding_to_dict(h) for h in s["holdings"]],
        }
    finally:
        session.close()


@app.get("/api/stock/{code}/info")
def api_stock_info(code: str):
    info = _fetch_stock_info(code)
    return info or JSONResponse(status_code=404, content={"error": "股票未找到"})


@app.post("/api/stock/{code}/sync")
def api_sync_stock_price(code: str):
    session = get_session()
    try:
        ensure_stock_exists(session, code)
        count = sync_stock_price(session, code)
        return {"success": True, "count": count}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


@app.post("/api/stock/transaction")
def api_add_stock_transaction(
    stock_code: str = Form(...),
    channel_code: str = Form("other"),
    txn_type: str = Form("buy"),
    txn_date: str = Form(...),
    price: str = Form(...),
    shares: str = Form(...),
    fee: str = Form("0"),
    note: str = Form(""),
):
    channel_map = {
        "alipay": "支付宝", "jd": "京东金融", "wechat": "微信理财通",
        "eastmoney": "东方财富", "huatai": "华泰证券", "other": "其他券商",
    }
    session = get_session()
    try:
        txn = add_stock_transaction(
            session,
            stock_code=stock_code,
            channel_code=channel_code,
            channel_name=channel_map.get(channel_code, channel_code),
            txn_type=txn_type,
            txn_date=datetime.strptime(txn_date, "%Y-%m-%d").date(),
            price=Decimal(price),
            shares=Decimal(shares),
            fee=Decimal(fee),
            note=note,
        )
        return {"success": True, "id": txn.id}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


@app.put("/api/stock/holdings/{holding_id}/code")
def api_update_stock_code(holding_id: int, body: dict = Body(...)):
    """修改持仓的股票代码"""
    new_code = body.get("new_code", "").strip()
    if not new_code:
        return JSONResponse(status_code=400, content={"error": "代码不能为空"})
    session = get_session()
    try:
        h = session.get(StockHolding, holding_id)
        if not h:
            return JSONResponse(status_code=404, content={"error": "持仓不存在"})

        old_code = h.stock_code
        if old_code == new_code:
            return {"success": True}

        # Ensure new stock exists
        stock = ensure_stock_exists(session, new_code)

        # Update holding and related transactions
        h.stock_code = new_code
        session.query(StockTransaction).filter_by(
            stock_code=old_code, channel_id=h.channel_id
        ).update({"stock_code": new_code})

        # Sync price history for the new code
        from datetime import timedelta
        from src.models import StockPrice as SP
        sync_stock_price(session, new_code,
                         start_date=date.today() - timedelta(days=60),
                         end_date=date.today())

        # Get latest price; fallback: copy old code's price if sync failed
        latest_price, price_date = get_stock_price_with_date(session, new_code)
        if not latest_price:
            old_price_rec = (
                session.query(SP).filter_by(stock_code=old_code)
                .order_by(SP.price_date.desc()).first()
            )
            if old_price_rec:
                existing = session.query(SP).filter_by(
                    stock_code=new_code, price_date=old_price_rec.price_date).first()
                if not existing:
                    session.add(SP(stock_code=new_code,
                                   price_date=old_price_rec.price_date,
                                   close=old_price_rec.close))
                    session.flush()
                latest_price = old_price_rec.close
                price_date = old_price_rec.price_date
        market_value = None
        profit = None
        profit_rate = None
        if latest_price and latest_price > 0:
            market_value = float((h.shares * latest_price).quantize(Decimal("0.01")))
            profit = float((Decimal(str(market_value)) - h.cost_amount).quantize(Decimal("0.01")))
            if h.cost_amount > 0:
                profit_rate = float(((Decimal(str(profit)) / h.cost_amount) * 100).quantize(Decimal("0.01")))

        session.commit()
        return {
            "success": True,
            "stock_name": stock.name,
            "latest_price": float(latest_price) if latest_price else None,
            "price_date": price_date.isoformat() if price_date else None,
            "market_value": market_value,
            "profit": profit,
            "profit_rate": profit_rate,
        }
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        session.close()


@app.delete("/api/stock/holdings/{holding_id}")
def api_delete_stock_holding(holding_id: int):
    session = get_session()
    try:
        h = session.get(StockHolding, holding_id)
        if not h:
            return JSONResponse(status_code=404, content={"error": "持仓不存在"})
        session.delete(h)
        session.commit()
        return {"success": True}
    finally:
        session.close()


@app.get("/api/stock/transactions")
def api_stock_transactions(code: str | None = None, limit: int = 50):
    session = get_session()
    try:
        query = session.query(StockTransaction).order_by(StockTransaction.txn_date.desc())
        if code:
            query = query.filter_by(stock_code=code)
        txns = query.limit(limit).all()
        return [{
            "id": t.id,
            "stock_code": t.stock_code,
            "stock_name": t.stock.name if t.stock else t.stock_code,
            "channel_name": t.channel.name if t.channel else "",
            "txn_type": t.txn_type,
            "txn_date": t.txn_date.isoformat(),
            "price": float(t.price),
            "shares": float(t.shares),
            "amount": float(t.amount),
            "fee": float(t.fee),
            "note": t.note or "",
        } for t in txns]
    finally:
        session.close()


@app.post("/api/stock/ocr/parse")
async def api_stock_ocr_parse(files: list[UploadFile] = File(...)):
    """股票持仓截图 OCR 解析"""
    from src.ocr.stock_parser import parse_screenshot, search_stock_code, OCR_AVAILABLE
    from PIL import Image as PILImage
    if not OCR_AVAILABLE:
        return JSONResponse(status_code=400, content={"error": "OCR 依赖未安装"})

    all_results = []
    for f in files:
        data = await f.read()
        img = PILImage.open(io.BytesIO(data))
        parsed = parse_screenshot(img)
        for p in parsed:
            code_info = None
            try:
                code_info = search_stock_code(p.stock_name)
            except Exception:
                pass

            matched_name = code_info["name"] if code_info else ""
            matched_code = code_info["code"] if code_info else ""

            # Price verification: check if matched code's latest price is close
            price_warning = ""
            if matched_code and p.current_price > 0:
                session = get_session()
                try:
                    from datetime import timedelta as _td
                    sync_stock_price(session, matched_code,
                                     start_date=date.today() - _td(days=5),
                                     end_date=date.today())
                    db_price = get_latest_price(session, matched_code)
                    if db_price and db_price > 0:
                        diff_pct = abs(float(db_price) - p.current_price) / p.current_price * 100
                        if diff_pct > 10:
                            price_warning = f"价格偏差 {diff_pct:.0f}%（匹配到 {matched_name}，行情价 {db_price}，截图价 {p.current_price:.3f}）"
                except Exception:
                    pass
                finally:
                    session.close()

            all_results.append({
                "stock_name": p.stock_name,
                "stock_code": matched_code,
                "matched_name": matched_name,
                "market_value": p.market_value,
                "current_price": p.current_price,
                "cost_price": p.cost_price,
                "total_profit": p.total_profit,
                "total_profit_rate": p.total_profit_rate,
                "daily_profit": p.daily_profit,
                "daily_profit_rate": p.daily_profit_rate,
                "price_warning": price_warning,
            })
    return {"results": all_results}


@app.post("/api/stock/import/snapshot")
def api_stock_import_snapshot(request_body: dict = Body(...)):
    """确认导入股票截图识别结果"""
    from datetime import timedelta
    body = request_body or {}
    stocks = body.get("stocks", [])
    snapshot_date_str = body.get("snapshot_date", "")
    channel_code = body.get("channel_code", "pingan")
    channel_name_map = {
        "pingan": "平安证券", "eastmoney": "东方财富",
        "huatai": "华泰证券", "other": "其他券商",
    }

    if not stocks or not snapshot_date_str:
        return JSONResponse(status_code=400, content={"error": "缺少数据"})

    snapshot_date = datetime.strptime(snapshot_date_str, "%Y-%m-%d").date()
    session = get_session()
    try:
        channel = ensure_stock_channel(session, channel_code, channel_name_map.get(channel_code, channel_code))
        imported = 0

        for s in stocks:
            code = s.get("stock_code", "").strip()
            name = s.get("stock_name", "")
            market_value = abs(Decimal(str(s.get("market_value", 0))))
            cost_price = Decimal(str(s.get("cost_price", 0)))

            if not code or market_value <= 0:
                continue

            stock = ensure_stock_exists(session, code, name)

            # Always use screenshot current_price for share calculation
            # (market_value = shares × current_price at snapshot time)
            current_price = abs(Decimal(str(s.get("current_price", 0))))
            if current_price <= 0:
                current_price = Decimal(1)
            shares = (market_value / current_price).quantize(Decimal("1"))

            # Sync latest price for display; fallback to screenshot price
            sync_stock_price(session, code, start_date=snapshot_date - timedelta(days=5), end_date=snapshot_date)
            db_price, _ = get_stock_price_with_date(session, code)
            if not db_price or db_price <= 0:
                from src.models import StockPrice as SP
                existing = session.query(SP).filter_by(stock_code=code, price_date=snapshot_date).first()
                if not existing:
                    session.add(SP(stock_code=code, price_date=snapshot_date, close=current_price))
                    session.flush()

            holding = (
                session.query(StockHolding)
                .filter_by(stock_code=code, channel_id=channel.id)
                .first()
            )
            if not holding:
                holding = StockHolding(stock_code=code, channel_id=channel.id)
                session.add(holding)
                session.flush()

            holding.shares = shares
            holding.cost_amount = (shares * cost_price).quantize(Decimal("0.01"))
            holding.cost_price = cost_price

            txn = StockTransaction(
                stock_code=code,
                channel_id=channel.id,
                txn_type="buy",
                txn_date=snapshot_date,
                price=price,
                shares=shares,
                amount=market_value,
                fee=Decimal(0),
                status="confirmed",
                note=f"截图导入 {snapshot_date_str}",
            )
            session.add(txn)
            imported += 1

        session.commit()
        return {"success": True, "imported": imported}
    except Exception as e:
        session.rollback()
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
