# 技术文档 — 全渠道基金管理系统

> 本文档面向开发者，介绍系统架构、代码结构、核心模块实现细节和扩展方式。

---

## 目录

- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [架构设计](#架构设计)
- [数据模型](#数据模型)
- [服务层详解](#服务层详解)
- [OCR 解析引擎](#ocr-解析引擎)
- [渠道抽象与扩展](#渠道抽象与扩展)
- [Web API 接口](#web-api-接口)
- [前端架构](#前端架构)
- [AI 分析模块](#ai-分析模块)
- [部署与运维](#部署与运维)

---

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| **后端框架** | FastAPI + Uvicorn | 异步 Web 服务 |
| **ORM** | SQLAlchemy 2.0 | 数据库操作，声明式映射 |
| **数据库** | SQLite | 轻量本地存储 |
| **数据源** | Akshare | 基金信息、净值获取（东方财富等公开源） |
| **OCR** | RapidOCR (ONNX Runtime) + Pillow | 截图文字识别 |
| **AI** | OpenAI SDK（兼容 DeepSeek/OpenAI/Qwen） | 持仓智能分析 |
| **CLI** | Click + Rich | 命令行工具 |
| **前端** | Jinja2 + Alpine.js + Tailwind CSS + Chart.js + marked.js | 单页应用 |
| **数据处理** | Pandas | CSV/Excel 解析、数据清洗 |

---

## 项目结构

```
data-finance/
├── app.py                      # FastAPI Web 应用（路由、API）
├── main.py                     # CLI 入口（Click 命令组）
├── config.yaml                 # 配置文件（数据库、AI 密钥）
├── requirements.txt            # Python 依赖
├── .gitignore
│
├── templates/
│   └── index.html              # 前端单页应用（Alpine.js）
│
├── examples/
│   └── alipay_sample.csv       # 支付宝交易 CSV 样例
│
└── src/
    ├── __init__.py
    ├── database.py             # 数据库引擎与会话管理
    ├── models.py               # ORM 模型定义（5 张表、4 组枚举）
    │
    ├── channels/               # 渠道抽象层
    │   ├── base.py             #   BaseChannel 抽象基类
    │   ├── alipay.py           #   支付宝渠道（CSV/Excel 导入）
    │   ├── jd.py               #   京东金融渠道（OCR/手动）
    │   └── wechat.py           #   微信理财通渠道（OCR/手动）
    │
    ├── ocr/                    # OCR 截图解析
    │   ├── alipay_parser.py    #   表格式布局解析（支付宝/京东通用）
    │   ├── jd_parser.py        #   京东解析（薄封装，复用 alipay_parser）
    │   └── wechat_parser.py    #   卡片式布局解析（微信专用）
    │
    └── services/               # 业务逻辑层
        ├── fund.py             #   基金信息、净值同步、行业分类推断
        ├── portfolio.py        #   持仓管理、交易导入、收益计算
        └── ai_analysis.py      #   AI 持仓分析（Prompt 构造 + API 调用）
```

---

## 架构设计

### 分层架构

```
┌──────────────────────────────────────────────────────────┐
│                    Frontend (SPA)                         │
│   Alpine.js + Tailwind CSS + Chart.js + marked.js        │
│   templates/index.html                                   │
└─────────────────────────┬────────────────────────────────┘
                          │ HTTP / REST API
┌─────────────────────────▼────────────────────────────────┐
│                    app.py (FastAPI)                       │
│   路由层：API 端点、文件上传、模板渲染                         │
└───────┬──────────┬───────────┬────────────────────────────┘
        │          │           │
   ┌────▼───┐ ┌───▼────┐ ┌───▼──────┐
   │Services│ │  OCR   │ │ Channels │
   │ fund   │ │ alipay │ │  base    │
   │portfol.│ │ wechat │ │  alipay  │
   │ai_anal.│ │ jd     │ │  jd      │
   └───┬────┘ └────────┘ │  wechat  │
       │                  └──────────┘
  ┌────▼──────────────┐
  │   models.py       │
  │   SQLAlchemy ORM  │
  └────┬──────────────┘
       │
  ┌────▼──────────────┐      ┌───────────────┐
  │   database.py     │      │  Akshare API  │
  │   SQLite          │      │  东方财富/雪球   │
  └───────────────────┘      └───────────────┘
```

### 核心数据流 — 截图导入

```
截图 → OCR 识别 → 文字块解析 → 基金名/金额/收益提取
     → 模糊搜索匹配基金代码 → 前端确认
     → 后端创建基金记录 → 获取前一日净值 → 计算份额
     → 写入 Holding + Transaction → 返回持仓列表
```

---

## 数据模型

### ER 关系

```
Fund (1) ──── (N) NAVRecord
  │
  │ (1)
  ├──── (N) Holding ──── (N:1) Channel
  │
  └──── (N) Transaction ──── (N:1) Channel
```

### 枚举定义

| 枚举 | 值 |
|------|----|
| `ChannelType` | alipay, tiantian, direct, other |
| `FundType` | stock, bond, hybrid, money, index, qdii, other |
| `TransactionType` | buy, sell, dividend, bonus |
| `TransactionStatus` | pending, confirmed, failed |

### 表结构

#### `funds` — 基金基本信息

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | String(10), PK | 基金代码 |
| `name` | String(100) | 基金名称（全称） |
| `fund_type` | String(20) | 基金类型枚举值 |
| `industry` | String(30), Nullable | 行业分类（由 `guess_industry()` 自动推断） |
| `manager` | String(50), Nullable | 基金经理 |
| `company` | String(100), Nullable | 基金公司 |
| `created_at` | DateTime | 创建时间 |

#### `nav_records` — 净值记录

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer, PK, Auto | 自增主键 |
| `fund_code` | String(10), FK → funds.code | 基金代码 |
| `nav_date` | Date | 净值日期 |
| `nav` | Numeric(10,4) | 单位净值 |
| `acc_nav` | Numeric(10,4), Nullable | 累计净值 |
| `daily_return` | Numeric(8,4), Nullable | 日涨幅 % |

- 唯一约束：`(fund_code, nav_date)`
- 索引：`(fund_code, nav_date)`

#### `channels` — 渠道

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer, PK, Auto | 自增主键 |
| `code` | String(20), Unique | 渠道代码（alipay/jd/wechat） |
| `name` | String(50) | 显示名称 |
| `channel_type` | String(20) | ChannelType 枚举值 |

#### `holdings` — 持仓（每渠道每基金一条）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer, PK, Auto | 自增主键 |
| `fund_code` | String(10), FK → funds.code | 基金代码 |
| `channel_id` | Integer, FK → channels.id | 渠道 ID |
| `shares` | Numeric(16,4) | 持有份额 |
| `cost_amount` | Numeric(16,4) | 累计投入成本 |
| `cost_nav` | Numeric(10,4), Nullable | 持仓成本净值 |
| `updated_at` | DateTime | 更新时间（自动） |

- 唯一约束：`(fund_code, channel_id)`

#### `transactions` — 交易记录

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer, PK, Auto | 自增主键 |
| `fund_code` | String(10), FK → funds.code | 基金代码 |
| `channel_id` | Integer, FK → channels.id | 渠道 ID |
| `txn_type` | String(20) | TransactionType 值 |
| `txn_date` | Date | 交易日期 |
| `amount` | Numeric(16,4) | 交易金额 |
| `shares` | Numeric(16,4), Nullable | 确认份额 |
| `nav` | Numeric(10,4), Nullable | 确认净值 |
| `fee` | Numeric(10,4), default=0 | 手续费 |
| `status` | String(20) | TransactionStatus 值 |
| `note` | String(200), Nullable | 备注 |
| `created_at` | DateTime | 创建时间 |

- 索引：`fund_code`、`txn_date`

---

## 服务层详解

### `src/database.py` — 数据库管理

| 函数 | 说明 |
|------|------|
| `_load_config()` | 读取 config.yaml |
| `get_engine()` | 创建 SQLAlchemy Engine（单例） |
| `get_session() → Session` | 获取数据库会话 |
| `init_db()` | 创建所有表（`Base.metadata.create_all`） |

### `src/services/fund.py` — 基金数据服务

| 函数 | 签名 | 说明 |
|------|------|------|
| `fetch_fund_info` | `(fund_code) → dict \| None` | 通过 `ak.fund_individual_basic_info_xq` 获取，失败回退 `ak.fund_name_em` |
| `ensure_fund_exists` | `(session, fund_code, fund_name) → Fund` | 确保数据库中有该基金记录 |
| `sync_nav` | `(session, fund_code, start_date, end_date) → int` | 从 akshare 同步净值到数据库，返回新增条数 |
| `get_latest_nav` | `(session, fund_code) → Decimal \| None` | 数据库中最新净值 |
| `get_latest_nav_with_date` | `(session, fund_code) → (Decimal, date)` | 最新净值及其日期 |
| `get_nav_on_date` | `(session, fund_code, target_date) → Decimal \| None` | 指定日期净值（向前查找最近的） |
| `guess_industry` | `(fund_name, fund_type) → str` | 基于关键词规则推断行业 |
| `sync_fund_industries` | `(session) → int` | 批量为所有基金推断行业 |

**行业推断规则** `_INDUSTRY_RULES`：

使用有序规则列表，按优先级匹配基金名称中的关键词。覆盖 20+ 行业类别：

```
军工/国防 → 军工国防     医疗/医药/健康/生物 → 医疗健康
半导体/芯片 → 半导体      人工智能/AI/机器人 → 人工智能
科技/计算机/电子 → 科技    互联网/数字经济 → 互联网
新能源/光伏/电力 → 新能源  消费/白酒/食品 → 消费
银行/保险/券商 → 金融     房地产/REITs → 房地产
钢铁/煤炭/有色 → 资源材料  黄金/贵金属 → 黄金
港股/恒生/纳斯达克 → 海外   沪深300/上证50/创业板 → 宽基指数
债券/短债/纯债 → 债券      货币/现金 → 货币
```

兜底逻辑：混合型 → "混合"，指数型 → "指数其他"，股票型 → "股票其他"，其余 → "其他"。

> 注意："证券" 已从金融关键词中移除，避免 "证券投资基金" 通用后缀造成误匹配。

### `src/services/portfolio.py` — 持仓与交易服务

| 函数 | 签名 | 说明 |
|------|------|------|
| `ensure_channel` | `(session, code, name) → Channel` | 确保渠道存在 |
| `import_transactions` | `(session, records, channel_code) → int` | 批量导入交易并更新持仓 |
| `_update_holding` | `(session, fund_code, channel_id, txn)` | 根据交易更新 Holding（买入增/卖出减） |
| `get_holdings` | `(session, channel_code?) → list[HoldingSummary]` | 查询持仓，实时计算市值和盈亏 |
| `get_portfolio_summary` | `(session) → dict` | 全局汇总 |

**HoldingSummary 数据类**：

```python
@dataclass
class HoldingSummary:
    holding_id: int
    fund_code: str
    fund_name: str
    channel_name: str
    industry: str           # 行业分类
    shares: Decimal         # 持有份额
    cost_amount: Decimal    # 投入成本
    cost_nav: Decimal | None
    latest_nav: Decimal | None
    latest_nav_date: str | None
    market_value: Decimal   # = shares × latest_nav
    profit: Decimal         # = market_value - cost_amount
    profit_rate: Decimal    # = profit / cost_amount × 100
```

**精度处理**：`market_value` 和 `profit` 使用 `Decimal("0.01").quantize(..., ROUND_HALF_UP)` 确保四舍五入到分，避免浮点精度问题。

### `src/services/ai_analysis.py` — AI 分析服务

| 函数 | 说明 |
|------|------|
| `_load_ai_config()` | 从 config.yaml 读取 AI 配置 |
| `build_portfolio_prompt(holdings)` | 将持仓数据构造为 Markdown 表格（概览 + 明细 + 行业汇总） |
| `analyze_portfolio(holdings) → str` | 调用 OpenAI 兼容接口，返回 Markdown 分析报告 |

System Prompt 指令要求模型从 4 个维度分析：持仓风格画像、资产配置、持仓诊断、调仓建议，最后给出 1-10 分评分。

---

## OCR 解析引擎

### 表格式布局 — `alipay_parser.py`（支付宝 / 京东通用）

截图特征：标题行 + 两行一组的基金信息。

```
┌────────────────────────────────────────┐
│ 基金名称              持有金额    持有收益 │  ← 表头
├────────────────────────────────────────┤
│ 富国中证军工指数(LOF)   1,234.56   +98.76 │  ← Row 1：名称+金额+收益
│ A                      2.17    +8.16% │  ← Row 2：名称续+昨日+收益率
└────────────────────────────────────────┘
```

**解析流程**：

1. `_get_ocr()` — 获取 RapidOCR 引擎（单例）
2. OCR 识别 → `list[(bbox, text, score)]`
3. `_detect_columns(blocks, img_width)` — 根据 "名称/基金名称" 和 "持有收益/持仓收益" 定位左右列 X 坐标边界
4. `_detect_content_area(blocks, img_height)` — 识别底部导航栏关键词（"基金市场/机会" 或 "全球投资/基金圈"），确定内容区上下边界
5. `_group_into_rows(blocks, threshold)` — 按 Y 坐标聚类分行
6. 逐行状态机解析：
   - 左列非噪音文本 → 追加到基金名称
   - 右列无符号数字 + 无百分比 → 市值（Row 1）或昨日收益（Row 2）
   - 右列带 ± 符号数字 → 持有收益
   - 右列百分比 → 收益率（标记为 Row 2，触发保存）
   - 噪音过滤：`_is_noise_text()` 排除 "定投/热门/屡创新高" 等标签
7. `search_fund_code(fund_name)` — 名称归一化 + `ak.fund_name_em()` 模糊搜索 + 份额类（A/B/C）优先匹配

**关键设计决策**：

- `0.00` 昨日收益的处理：当右列同时出现无符号数字和百分比时，判定为 Row 2 的 `0.00` 昨日收益，而非新基金的市值
- `jd_parser.py` 直接 `from src.ocr.alipay_parser import *` 复用全部逻辑

### 卡片式布局 — `wechat_parser.py`（微信理财通专用）

截图特征：以"持有金额"标签为锚点的卡片式布局。

```
┌───────────────────────────────┐
│  易方达上证50增强C              │  ← 名称（锚点上方最近的非数字文本）
│  持有金额    持仓收益    昨日收益 │  ← 标签行（"持有金额"为锚点）
│  67,758.68  7,813.50    0.00  │  ← 数值行（按 X 对齐标签列）
└───────────────────────────────┘
```

**解析流程**：

1. 遍历 OCR 块，找到 "持有金额" 文本作为锚点
2. 向上搜索：找最近的非标签、非噪音、非数字文本块 → 基金名称
3. 记录 "持有金额/持仓收益/昨日收益" 各标签的 X 坐标
4. 向下搜索数字块，按 X 坐标与对应标签列对齐 → 市值/收益/昨日
5. 复用 `alipay_parser.search_fund_code()` 匹配基金代码

---

## 渠道抽象与扩展

### 抽象基类

```python
class BaseChannel(ABC):
    @property
    @abstractmethod
    def channel_code(self) -> str: ...     # "alipay" / "jd" / "wechat"

    @property
    @abstractmethod
    def channel_name(self) -> str: ...     # "支付宝" / "京东金融" / "微信理财通"

    @abstractmethod
    def parse_transactions(self, file_path: Path) -> list[dict]: ...

    @staticmethod
    def build_manual_record(...) -> dict: ...   # 构造手动交易记录
```

### 渠道能力矩阵

| 渠道 | channel_code | CSV/Excel 导入 | OCR 导入 | 手动录入 | OCR 解析器 |
|------|-------------|---------------|---------|---------|-----------|
| 支付宝 | `alipay` | `parse_transactions()` | `alipay_parser` | `build_manual_record()` | 表格式 |
| 京东金融 | `jd` | NotImplemented | `jd_parser`（复用） | `build_manual_record()` | 表格式 |
| 微信理财通 | `wechat` | NotImplemented | `wechat_parser` | `build_manual_record()` | 卡片式 |

### 扩展新渠道

1. 新建 `src/channels/xxx.py`，继承 `BaseChannel`
2. 如需 OCR，新建 `src/ocr/xxx_parser.py`，实现 `parse_screenshot()` 和 `search_fund_code()`
3. 在 `app.py` 中注册：
   - `api_add_transaction` / `api_import_snapshot` 的渠道映射 dict
   - `_get_parser()` 函数添加分支
   - `channelOptions` 前端数组（在 `templates/index.html`）

---

## Web API 接口

### 持仓与交易

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| `GET` | `/api/summary` | — | 持仓汇总 + 所有持仓明细 |
| `GET` | `/api/holdings` | `?channel=alipay` | 持仓列表（可选过滤） |
| `GET` | `/api/transactions` | — | 交易记录列表 |
| `POST` | `/api/transactions` | Form: fund_code, channel_code, amount, date, note | 手动添加交易 |
| `DELETE` | `/api/holdings/{id}` | — | 删除单条持仓 |
| `GET` | `/api/channels` | — | 已有渠道列表 |

### 基金

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| `GET` | `/api/fund/{code}/info` | — | 基金详情 |
| `POST` | `/api/fund/{code}/sync` | — | 同步净值到数据库 |
| `GET` | `/api/fund/search` | `?q=军工` | 模糊搜索基金代码 |
| `POST` | `/api/fund/sync-industries` | — | 批量推断行业分类 |

### OCR & 导入

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| `POST` | `/api/ocr/parse` | Form: files (多文件), channel | 截图 OCR 解析 |
| `POST` | `/api/import/snapshot` | JSON: funds[], snapshot_date, channel | 确认导入识别结果 |

### AI

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| `POST` | `/api/ai/analyze` | — | AI 智能持仓分析，返回 Markdown |

### 返回格式

所有 API 返回 JSON。成功时直接返回数据对象，失败时返回：

```json
{ "error": "错误描述" }
```

---

## 前端架构

### 技术方案

单页应用（SPA），无需构建工具，全部通过 CDN 引入：

| 库 | 版本 | 用途 |
|----|------|------|
| Alpine.js | 3.x | 响应式状态管理 |
| Tailwind CSS | CDN | 原子化样式 |
| Chart.js | 4.x | 7 种数据图表 |
| marked.js | latest | AI 结果 Markdown 渲染 |

### Tab 结构

| Tab ID | 标签 | 核心功能 |
|--------|------|---------|
| `dashboard` | 总览 | 汇总卡片、渠道/行业筛选、持仓表格、排序、CSV 下载 |
| `analysis` | 持仓分析 | 关键指标、7 种图表、AI 智能解读 |
| `import` | 截图导入 | 渠道选择、文件上传（多选/文件夹/拖拽）、OCR 结果、确认导入 |
| `add` | 手动录入 | 交易表单 |
| `history` | 交易记录 | 交易列表 |

### Alpine.js 状态变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `tab` | String | 当前激活 Tab |
| `summary` | Object | `/api/summary` 缓存 |
| `channelFilter` | String | 渠道筛选值 |
| `industryFilter` | String | 行业筛选值 |
| `sortKey` / `sortAsc` | String / Boolean | 排序键与方向 |
| `ocrResults` | Array | OCR 解析结果 |
| `selectedFiles` | Array | 待上传文件列表 |
| `importChannel` | String | OCR 导入渠道 |
| `aiResult` / `aiLoading` / `aiError` | String / Boolean / String | AI 分析状态 |
| `_charts` | Object | Chart.js 实例（Tab 切换时销毁重建） |
| `syncIndustryLoading` | Boolean | 行业同步状态 |

### 计算属性

| 属性 | 说明 |
|------|------|
| `sortedHoldings` | 渠道 + 行业筛选 → 排序（支持字符串 localeCompare 和数值排序） |
| `filteredStats` | 筛选后的汇总统计（基金数/成本/市值/盈亏/收益率） |
| `channelStatsItems` | 按渠道聚合：数量/成本/市值/盈亏/收益率 |
| `industryStatsItems` | 按行业聚合：数量/成本/市值/盈亏/收益率 |
| `analysisMetrics` | 最大持仓、最高/最低收益率、最大盈利/亏损 |
| `renderedAiResult` | `marked.parse(aiResult)` → HTML |

### 图表列表

| Canvas ID | 图表类型 | 数据 |
|-----------|---------|------|
| `chartChannel` | Doughnut | channel_name → market_value |
| `chartIndustry` | Doughnut | industry → market_value |
| `chartAllocation` | Doughnut | fund_name → market_value |
| `chartIndustryProfit` | Bar (horizontal) | industry → profit |
| `chartRate` | Bar (horizontal) | fund → profit_rate |
| `chartProfit` | Bar (horizontal) | fund → profit |
| `chartCostMarket` | Bar (horizontal, grouped) | fund → [cost_amount, market_value] |

图表高度根据数据条数动态调整：`Math.max(260, count * 34) + 'px'`

---

## AI 分析模块

### 数据流

```
holdings (list[dict])
    │
    ▼
build_portfolio_prompt()    ← 构造 Markdown 表格
    │
    ▼
OpenAI Chat Completions API (DeepSeek)
    │    system: 投资顾问 Prompt
    │    user:   持仓数据表格
    │    temperature: 0.7
    │    max_tokens: 3000
    ▼
Markdown 分析报告
    │
    ▼
marked.parse()  → HTML  → 前端渲染
```

### Prompt 数据结构

```markdown
## 持仓概览
- 持有基金数量：N 只
- 总投入成本：X 元
- 当前总市值：Y 元
- 总盈亏：Z 元（R%）

## 持仓明细
| 基金名称 | 行业 | 成本 | 市值 | 盈亏 | 收益率 | 占比 |

## 行业分布汇总
| 行业 | 基金数 | 市值 | 占比 | 盈亏 | 收益率 |
```

### 错误处理

- API Key 未配置或无效 → 返回 400 + 配置示例
- 无持仓数据 → 返回 400
- 网络/API 异常 → 返回 500

---

## 部署与运维

### 开发环境

```bash
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

### 生产环境

```bash
pip install gunicorn
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### 打包

```bash
zip -r data-finance.zip . \
  -x "*.db" "__pycache__/*" ".venv/*" "venv/*" \
  -x ".DS_Store" "config.yaml" "uploads/*" "*.zip" ".git/*"
```

### 数据库

- SQLite 文件：`fund_manager.db`（自动创建）
- 清空数据：删除 `fund_manager.db` 后重启
- 表结构变更：手动 `ALTER TABLE` 或删库重建

### 依赖清单

```
sqlalchemy>=2.0        # ORM
pandas>=2.0            # 数据处理
rich>=13.0             # CLI 格式化输出
click>=8.0             # CLI 框架
akshare>=1.10          # 基金数据源
pyyaml>=6.0            # 配置解析
fastapi>=0.110         # Web 框架
uvicorn>=0.29          # ASGI 服务器
jinja2>=3.1            # 模板引擎
python-multipart>=0.0.9 # 文件上传
Pillow>=10.0           # 图像处理
rapidocr_onnxruntime>=1.3 # OCR 引擎
openai>=1.0            # AI 分析
```
