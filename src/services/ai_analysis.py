"""AI 持仓分析服务

使用 OpenAI 兼容接口（DeepSeek / OpenAI / Qwen 等）对基金持仓进行智能分析。
"""

from __future__ import annotations

import yaml
from openai import OpenAI

_SYSTEM_PROMPT = """你是一位专业的投资顾问，擅长分析个人投资组合（包括基金和股票）。
请根据用户提供的持仓数据，从以下维度进行分析并给出专业建议：

1. **持仓风格画像**：根据持有资产的类型和行业分布，判断整体投资风格（激进/稳健/保守/均衡），并说明理由。

2. **资产配置分析**：
   - 基金与股票的配比是否合理
   - 股债配比是否合理（基金部分）
   - 行业集中度如何，是否存在过度集中风险
   - 各行业/类型的资金分布是否均衡

3. **持仓诊断**：
   - 指出收益表现好的和表现差的持仓（区分基金和股票）
   - 分析可能存在的风险点（如同质化持仓、个股集中、追涨杀跌等）

4. **调仓建议**：
   - 哪些行业可以适当增配或减配
   - 基金和股票之间的配比建议
   - 当前缺失哪些重要配置（如有）
   - 给出具体的调整方向和理由

要求：
- 使用中文回答
- 分析要结合实际数据，引用具体基金/股票名称和数字
- 建议要具体可操作，不要空泛
- 使用 Markdown 格式输出，用标题和列表让内容结构清晰
- 最后给一个 1-10 分的综合评分和一句话总结"""


def _load_ai_config() -> dict:
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("ai", {})
    except Exception:
        return {}


def build_portfolio_prompt(holdings: list[dict]) -> str:
    """将持仓数据构造为结构化的 prompt 文本"""
    if not holdings:
        return "当前没有持仓数据。"

    total_cost = sum(h.get("cost_amount", 0) or 0 for h in holdings)
    total_market = sum(h.get("market_value", 0) or 0 for h in holdings)
    total_profit = total_market - total_cost

    fund_list = [h for h in holdings if h.get("asset_type") != "stock"]
    stock_list = [h for h in holdings if h.get("asset_type") == "stock"]

    lines = [
        f"## 持仓概览",
        f"- 持有基金：{len(fund_list)} 只，股票：{len(stock_list)} 只",
        f"- 总投入成本：{total_cost:,.2f} 元",
        f"- 当前总市值：{total_market:,.2f} 元",
        f"- 总盈亏：{total_profit:+,.2f} 元（{total_profit / total_cost * 100:+.2f}%）" if total_cost > 0 else "",
        "",
    ]

    if fund_list:
        lines += [
            "## 基金持仓明细",
            "",
            "| 基金名称 | 行业 | 成本 | 市值 | 盈亏 | 收益率 | 占比 |",
            "|---------|------|------|------|------|--------|------|",
        ]
        for h in sorted(fund_list, key=lambda x: x.get("market_value", 0) or 0, reverse=True):
            name = h.get("fund_name", "")
            industry = h.get("industry", "其他")
            cost = h.get("cost_amount", 0) or 0
            mv = h.get("market_value", 0) or 0
            profit = h.get("profit", 0) or 0
            rate = h.get("profit_rate", 0) or 0
            pct = mv / total_market * 100 if total_market > 0 else 0
            lines.append(
                f"| {name} | {industry} | {cost:,.2f} | {mv:,.2f} | {profit:+,.2f} | {rate:+.2f}% | {pct:.1f}% |"
            )

    if stock_list:
        lines += [
            "",
            "## 股票持仓明细",
            "",
            "| 股票名称 | 行业 | 成本 | 市值 | 盈亏 | 收益率 | 占比 |",
            "|---------|------|------|------|------|--------|------|",
        ]
        for h in sorted(stock_list, key=lambda x: x.get("market_value", 0) or 0, reverse=True):
            name = h.get("stock_name", "")
            industry = h.get("industry", "其他")
            cost = h.get("cost_amount", 0) or 0
            mv = h.get("market_value", 0) or 0
            profit = h.get("profit", 0) or 0
            rate = h.get("profit_rate", 0) or 0
            pct = mv / total_market * 100 if total_market > 0 else 0
            lines.append(
                f"| {name} | {industry} | {cost:,.2f} | {mv:,.2f} | {profit:+,.2f} | {rate:+.2f}% | {pct:.1f}% |"
            )

    # 行业汇总
    ind_map: dict[str, dict] = {}
    for h in holdings:
        ind = h.get("industry", "其他")
        if ind not in ind_map:
            ind_map[ind] = {"cost": 0, "market": 0, "count": 0}
        ind_map[ind]["cost"] += h.get("cost_amount", 0) or 0
        ind_map[ind]["market"] += h.get("market_value", 0) or 0
        ind_map[ind]["count"] += 1

    lines += [
        "",
        "## 行业分布汇总",
        "",
        "| 行业 | 基金数 | 市值 | 占比 | 盈亏 | 收益率 |",
        "|------|--------|------|------|------|--------|",
    ]
    for ind, v in sorted(ind_map.items(), key=lambda x: x[1]["market"], reverse=True):
        profit = v["market"] - v["cost"]
        rate = profit / v["cost"] * 100 if v["cost"] > 0 else 0
        pct = v["market"] / total_market * 100 if total_market > 0 else 0
        lines.append(
            f"| {ind} | {v['count']} | {v['market']:,.2f} | {pct:.1f}% | {profit:+,.2f} | {rate:+.2f}% |"
        )

    return "\n".join(lines)


def analyze_portfolio(holdings: list[dict]) -> str:
    """调用 AI 接口分析持仓组合，返回 Markdown 格式的分析结果"""
    config = _load_ai_config()
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "https://api.deepseek.com")
    model = config.get("model", "deepseek-chat")

    if not api_key or api_key.startswith("sk-xxx"):
        raise ValueError(
            "请先在 config.yaml 中配置 AI API Key。\n"
            "示例：\n"
            "ai:\n"
            '  api_key: "sk-your-real-key"\n'
            '  base_url: "https://api.deepseek.com"\n'
            '  model: "deepseek-chat"'
        )

    client = OpenAI(api_key=api_key, base_url=base_url)

    user_prompt = build_portfolio_prompt(holdings)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"请分析以下基金持仓组合：\n\n{user_prompt}"},
        ],
        temperature=0.7,
        max_tokens=3000,
    )

    return response.choices[0].message.content
