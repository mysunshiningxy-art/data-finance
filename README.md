# 全渠道基金管理系统

管理多平台基金投资的持仓、交易和收益。通过截图 OCR 识别或手动录入导入数据，自动同步净值并计算收益。

目前已接入**支付宝（蚂蚁财富）**、**京东金融**和**微信（腾讯理财通）**渠道，架构支持灵活扩展更多渠道。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 截图 OCR 导入 | 上传支付宝/京东金融/微信理财通基金持仓截图，自动识别基金名称、金额、收益，匹配基金代码；支持多选图片或整个文件夹 |
| CSV/Excel 导入 | 解析支付宝导出的交易记录文件 |
| 手动录入 | 表单录入买入/卖出/分红等交易 |
| 净值同步 | 从东方财富自动获取历史净值数据，支持一键同步全部持仓净值 |
| 持仓追踪 | 根据交易自动计算份额和成本，支持按市值、盈亏、收益率等多列排序 |
| 持仓下载 | 一键导出持仓明细为 CSV 文件（Excel 兼容），文件名含日期 |
| 收益计算 | 计算单只基金和组合整体的市值、盈亏、收益率，金额精确到分 |
| 批量操作 | 一键清空全部持仓、批量同步净值 |
| 多渠道管理 | 已接入支付宝、京东金融、微信理财通，抽象渠道层可扩展接入天天基金、直销等平台 |
| 双入口 | Web 界面 + CLI 命令行 |

---

## 快速开始

### 环境要求

- **Python 3.11+**
- macOS / Linux / Windows

### 安装

```bash
# 克隆项目
cd /path/to/your/workspace

# 安装依赖
pip install -r requirements.txt
```

### 启动 Web 服务

```bash
python app.py
```

启动后访问 **http://localhost:8000**，即可使用 Web 界面。

服务默认开启热重载（`reload=True`），修改代码后自动重启。

### CLI 命令行（可选）

```bash
python main.py --help
```

---

## 使用指南

### 1. 截图 OCR 导入（推荐）

最快速的数据录入方式：

1. 打开支付宝/京东金融/微信理财通 → 基金 → 持有，截图基金列表页面
2. 访问 Web 界面，切换到「截图导入」tab
3. 选择渠道（支付宝 / 京东金融 / 微信理财通 / 天天基金）
4. 选择截图日期（截图当天的日期，用于确定计算份额所使用的净值）
5. 拖拽或点击上传截图（支持多选图片、选择文件夹批量导入）
6. 点击「开始识别」，系统自动提取：
   - 基金名称（自动拼接多行名称，如"易方达中证军工指数" + "(LOF)A"）
   - 持有金额
   - 昨日收益（包括 0.00 的情况）
   - 持有收益 & 收益率
   - 自动匹配基金代码（渐进式模糊搜索 + 份额类别 A/B/C 优先）
7. 核对/修正识别结果，点击「确认导入」
8. 系统自动同步净值，用 **截图日期前一个交易日的净值** 计算持有份额

> 截图显示的市值 = 份额 × 前一交易日净值，因此份额 = 市值 / 前一交易日净值。例如截图日期为 2 月 27 日，则使用 2 月 26 日（或更早的最近交易日）的净值计算份额。

### 2. CSV/Excel 文件导入

从支付宝导出基金交易记录文件：

```bash
# CLI 方式
python main.py alipay import your_file.csv

# 示例文件
python main.py alipay import examples/alipay_sample.csv
```

支持的列名（至少需要前 4 列）：

| 列名 | 说明 | 必填 |
|------|------|------|
| 基金代码 | 6 位代码 | 是 |
| 交易类型 | 买入/卖出/分红等 | 是 |
| 交易日期 | 日期 | 是 |
| 交易金额 | 金额 | 是 |
| 基金名称 | 基金名称 | 否 |
| 确认份额 | 确认份额 | 否 |
| 确认净值 | 确认净值 | 否 |
| 手续费 | 手续费 | 否 |
| 交易状态 | 交易成功/交易中等 | 否 |

### 3. 手动录入

Web 界面「手动录入」tab 或 CLI：

```bash
python main.py alipay add \
  --code 000001 \
  --type buy \
  --date 2026-02-26 \
  --amount 10000 \
  --shares 5200 \
  --nav 1.9230 \
  --fee 0
```

### 4. 净值同步

```bash
# CLI 同步最近 30 天净值
python main.py fund sync-nav 000001 --days 30

# Web 界面：持仓列表点击「同步净值」按钮
```

### 5. 查看持仓和收益

```bash
python main.py holdings              # 持仓明细
python main.py holdings --channel alipay  # 按渠道过滤
python main.py summary               # 组合汇总
python main.py transactions          # 交易记录
python main.py transactions --code 000001  # 按基金过滤
```

Web 界面「总览」tab 展示汇总卡片 + 持仓表格（支持按市值、盈亏、收益率等排序）。

持仓列表功能：
- 点击表头列名排序（升序/降序切换）
- 最新净值显示净值日期
- 「同步全部净值」一键更新所有持仓的最新净值
- 「下载」导出持仓明细为 CSV 文件
- 「一键清空」删除全部持仓
- 单条持仓可点击「删除」移除

---

## 项目结构

```
data-finance/
├── app.py                  # FastAPI Web 应用（API 路由 + 页面渲染）
├── main.py                 # CLI 入口（Click 命令行）
├── config.yaml             # 配置文件（数据库路径、渠道配置）
├── requirements.txt        # Python 依赖
├── fund_manager.db         # SQLite 数据库（自动生成）
│
├── templates/
│   └── index.html          # 前端页面（Tailwind CSS + Alpine.js SPA）
│
├── examples/
│   └── alipay_sample.csv   # 示例 CSV 导入文件
│
└── src/
    ├── database.py         # 数据库连接 & 会话管理
    ├── models.py           # SQLAlchemy ORM 模型
    │
    ├── channels/           # 渠道适配层
    │   ├── base.py         # BaseChannel 抽象基类
    │   ├── alipay.py       # 支付宝渠道（CSV 解析 + 手动录入）
    │   ├── jd.py           # 京东金融渠道（截图 OCR + 手动录入）
    │   └── wechat.py       # 微信理财通渠道（截图 OCR + 手动录入）
    │
    ├── ocr/                # OCR 截图识别
    │   ├── alipay_parser.py # 表格式截图解析（支持支付宝/京东金融）
    │   ├── jd_parser.py    # 京东金融解析（复用表格式解析逻辑）
    │   └── wechat_parser.py # 微信理财通解析（卡片式布局，独立解析器）
    │
    └── services/           # 业务逻辑
        ├── fund.py         # 基金信息查询、净值同步
        └── portfolio.py    # 持仓管理、交易导入、收益计算
```

---

## 技术架构

### 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI | REST API + 页面渲染 |
| 前端 | Tailwind CSS + Alpine.js (CDN) | 单页应用，无需构建 |
| ORM | SQLAlchemy 2.0 | 数据库模型 & 查询 |
| 数据库 | SQLite | 本地持久化存储 |
| OCR | RapidOCR (ONNX Runtime) | 中文截图文字识别 |
| 数据源 | akshare | 基金信息 & 净值获取（东方财富） |
| CLI | Click + Rich | 命令行界面 |
| 数据处理 | Pandas | CSV/Excel 解析 |

### 数据模型

```
┌──────────┐     ┌──────────────┐     ┌───────────┐
│  Fund    │     │  NAVRecord   │     │  Channel  │
│──────────│     │──────────────│     │───────────│
│ code(PK) │◄────│ fund_code    │     │ id(PK)    │
│ name     │     │ nav_date     │     │ code      │
│ fund_type│     │ nav          │     │ name      │
│ manager  │     │ acc_nav      │     │ type      │
│ company  │     │ daily_return │     └─────┬─────┘
└────┬─────┘     └──────────────┘           │
     │                                      │
     │           ┌──────────────┐           │
     ├───────────│  Holding     │───────────┤
     │           │──────────────│           │
     │           │ fund_code    │           │
     │           │ channel_id   │           │
     │           │ shares       │           │
     │           │ cost_amount  │           │
     │           │ cost_nav     │           │
     │           └──────────────┘           │
     │                                      │
     │           ┌──────────────┐           │
     └───────────│ Transaction  │───────────┘
                 │──────────────│
                 │ fund_code    │
                 │ channel_id   │
                 │ txn_type     │
                 │ txn_date     │
                 │ amount/shares│
                 │ nav/fee      │
                 │ status/note  │
                 └──────────────┘
```

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 界面主页 |
| GET | `/api/summary` | 组合汇总 + 持仓列表 |
| GET | `/api/holdings` | 持仓列表（可按渠道过滤） |
| GET | `/api/transactions` | 交易记录（可按基金/渠道过滤） |
| POST | `/api/transactions` | 添加手动交易 |
| POST | `/api/ocr/parse` | 上传截图进行 OCR 识别 |
| POST | `/api/import/snapshot` | 导入 OCR 识别结果为持仓 |
| GET | `/api/fund/{code}/info` | 查询基金信息 |
| POST | `/api/fund/{code}/sync` | 同步基金净值 |
| GET | `/api/fund/search?q=` | 按名称搜索基金代码 |
| DELETE | `/api/holdings/{id}` | 删除持仓记录 |
| GET | `/api/channels` | 渠道列表 |

### OCR 截图识别原理

系统支持两种截图布局的自动解析：

#### 表格式布局（支付宝 / 京东金融）

```
名称/基金名称        金额/昨日收益      持有收益/率
───────────────────────────────────────────────
易方达中证军工指数    128.16            +28.16
(LOF)A              +2.17             +28.16%
```

解析流程：
1. **RapidOCR** 识别图片中所有文字块及其坐标位置
2. **自适应列检测** — 从表头行计算三列分界线
3. **内容区域过滤** — 排除状态栏、标签页、底部导航等干扰
4. **行分组 + 列分类** — 按坐标将文字块分配到左/中/右三列
5. **基金卡片识别** — 通过中列和右列组合判断行类型：
   - 中列为无符号数字 + 右列为非百分比 → 新基金第 1 行（金额）
   - 中列为数字 + 右列为百分比 → 第 2 行（昨日收益），解决了 0.00 误判问题
6. **噪音过滤** — 自动过滤标签（定投、屡创新高、热门定投榜等）
7. **基金代码匹配** — 渐进式模糊搜索 + 份额类别优先排序

#### 卡片式布局（微信理财通）

```
富国天盈债券C
持有金额          持仓收益          昨日收益
72,613.15        +2,613.15        0.00
```

解析流程：
1. **RapidOCR** 识别图片中所有文字块
2. **锚点定位** — 以 "持有金额" 文本块为锚点，定位每只基金
3. **基金名称提取** — 向上查找最近的非标签文本
4. **数值提取** — 根据标签列位置（持有金额/持仓收益/昨日收益）对齐下方数值
5. **基金代码匹配** — 复用通用模糊搜索逻辑

---

## 扩展新渠道

继承 `BaseChannel` 并实现 `parse_transactions` 方法：

```python
from src.channels.base import BaseChannel

class TiantianChannel(BaseChannel):
    @property
    def channel_code(self) -> str:
        return "tiantian"

    @property
    def channel_name(self) -> str:
        return "天天基金"

    def parse_transactions(self, file_path):
        # 解析该渠道的导出文件，返回统一格式 DataFrame
        # 字段: fund_code, fund_name, txn_type, txn_date,
        #       amount, shares, nav, fee, status
        ...
```

---

## 配置说明

`config.yaml`：

```yaml
database:
  url: "sqlite:///fund_manager.db"    # 数据库路径，支持 SQLAlchemy URL 格式

channels:
  alipay:
    name: "支付宝"
    enabled: true
  jd:
    name: "京东金融"
    enabled: true
  wechat:
    name: "微信理财通"
    enabled: true
```

如需使用其他数据库（如 PostgreSQL），修改 `url` 即可：

```yaml
database:
  url: "postgresql://user:pass@localhost:5432/fund_db"
```

---

## 打包部署

打包项目（排除数据库和缓存文件）：

```bash
tar czf data-finance.tar.gz \
  --exclude='*.db' \
  --exclude='__pycache__' \
  --exclude='uploads' \
  --exclude='.venv' \
  -C /path/to/parent data-finance
```

部署到新环境后：

```bash
pip install -r requirements.txt
python app.py
```

数据库文件（`fund_manager.db`）会在首次启动时自动创建。

---

## 常见问题

**Q: OCR 识别不到基金？**
确保截图是支付宝/京东金融/微信理财通的基金持有列表页面，并在导入时选择正确的渠道（不同渠道使用不同解析器）。

**Q: OCR 识别出不存在的基金？**
旧版本在昨日收益为 0.00 时会将每只基金的第二行（如 `(LOF)A`）误判为新基金。已修复：通过右列是否为百分比来区分第 1 行和第 2 行。

**Q: 基金代码没有自动匹配？**
可在识别结果中手动填写 6 位基金代码，然后导入。搜索时系统会自动去除 `(LOF)`、`(QDII)` 等后缀进行模糊匹配。

**Q: 份额计算不准确？**
截图日期要选对——系统用截图日期前一个交易日的净值计算份额（份额 = 市值 / 前一交易日净值）。如果该日期没有净值数据，会回退到最近可用的净值。

**Q: 总市值和明细加起来差一分钱？**
已修复。市值和盈亏在计算时精确到分（四舍五入），确保汇总值与明细行之和一致。

**Q: 如何删除错误的持仓？**
在 Web 界面持仓列表点击「删除」按钮，或通过 API `DELETE /api/holdings/{id}`。也可使用「一键清空」删除全部持仓。

**Q: 如何导出持仓数据？**
在持仓明细标题栏点击「下载」按钮，导出为 CSV 文件（UTF-8 with BOM，Excel 直接打开不乱码）。
