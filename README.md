# stock-analysis · 适用于所有 AI Agent 的个股深度分析 Skill

输入 A股/港股代码或名称,自动拉取**经过验证的结构化数据**(行情、估值分位、财务、股东、分红、公告),按价值投资框架产出九节深度研究报告(Markdown + HTML)。

不绑定特定 Agent——任何能执行 Python、读取 Markdown 指令的 AI Agent 都能用:Claude Code 原生安装,Cursor / Codex / Windsurf 等作为规则文件加载,数据层脚本亦可完全独立运行。

> **设计哲学**:数据可靠性交给脚本,判断深度交给模型。数字一律来自 [akshare](https://github.com/akfamily/akshare) 底稿,搜索只做定性补充,每个结论标注证据出处。

## 它产出什么

一份九节结构的研究报告(结论先行),外加同名 HTML(明暗双主题、可打印):

```
0. 一页结论   关键数字表 · 三个关注 · 三个风险 · 结论(深度研究 | 观望 | 回避)
1. 这门生意   收入结构 · 商业模式核心杠杆 · 行业格局
2. 生意质量   毛利率/ROE/净现比 + 财报红旗清单逐项核对
3. 成长与天花板
4. 治理与股东回报
5. 估值      5年分位 + 悲观/中性/乐观三情景粗算(假设全部显式)
6. 风险清单   概率×影响排序,每条附「兑现信号」
7. 结论与跟进  重估触发条件 + 未来两个季度跟踪清单
附录         数据与来源(接口清单、公告/新闻链接)
```

## 快速开始

### Claude Code(原生 skill)

```bash
git clone https://github.com/ITwolffy/stock-analysis-skill.git ~/.claude/skills/stock-analysis
# 注意目录名保持 stock-analysis,与 SKILL.md 的 name 一致
# Windows PowerShell: git clone ... $HOME\.claude\skills\stock-analysis
```

### 其他 Agent(Cursor / Codex / Windsurf / 自建)

```bash
git clone https://github.com/ITwolffy/stock-analysis-skill.git
```

任选一种接入方式:
- 把 `SKILL.md` 内容作为项目规则/系统提示词加载(如 `.cursorrules`、`AGENTS.md` 引用)
- 直接对 Agent 说:「阅读 `<目录>/SKILL.md` 并按其流程执行」
- 只用数据层:独立运行脚本,把底稿喂给任何模型分析

### 依赖

```bash
pip install -r requirements.txt   # akshare==1.18.97(锁版本,接口变动频繁)
```

## 用法

```
/stock-analysis 600519          # A股代码
/stock-analysis 00700           # 港股代码(4~5位自动补零)
/stock-analysis 贵州茅台          # 名称解析,多命中会列候选让你选
"帮我分析一下腾讯值不值得买"        # 自然语言(需 Agent 支持 skill 触发)
```

独立使用数据层(不经过 Agent):

```bash
python scripts/fetch_data.py 600519 [--refresh]
```

- 底稿按日缓存,`--refresh` 强制重拉
- 所有衍生指标(估值 5 年分位、最大回撤、同比、净现比)由脚本算好,避免模型心算出错
- 单项接口失败只标注「获取失败」,不中断整体

## 输出目录

| 情况 | 输出位置 |
|---|---|
| 设置了环境变量 `STOCK_ANALYSIS_DIR` | 该目录(**推荐**,与运行时 cwd 无关) |
| 未设置 | 当前目录下的 `股票分析/` 子目录 |

报告:`<输出目录>/<code>-<名称>-<YYYY-MM>.md` + 同名 `.html`;底稿:`<输出目录>/_data/<code>-<YYYYMMDD>.md`。同月重跑覆盖旧报告(每月一份最新)。

## 目录结构

```
stock-analysis/
├── SKILL.md              # 分析框架:九节结构 + 分析纪律(数字只认底稿/不给买卖点位/结论标证据)
├── requirements.txt      # akshare==1.18.97(锁版本)
├── scripts/
│   └── fetch_data.py     # 数据层:解析/缓存/重试/降级/衍生指标
└── references/
    ├── valuation.md      # 估值方法适用场景与反例(PE/PB/股息率/三情景)
    ├── red-flags.md      # 财报红旗清单(阈值 + 误报情形,映射底稿章节)
    └── hk-notes.md       # 港股事项(币种陷阱/英文科目映射/老千股特征)
```

## 数据源与已知限制

- **范围**:沪深 A股 + 港股;北交所/三板/B股/美股不支持(礼貌拒绝)
- **A股数据**:`stock_value_em`(估值/市值历史)、财务摘要与指标、十大流通股东、分红送配、巨潮公告
- **港股数据**:月线、百度估值历史(PE/PB/总市值)、东财财务三表、公司概况
- **内置容错**:接口闪断自动重试;东财行情接口挂掉自动降级新浪日线重采样;全局 30s 超时防挂死
- **已知缺口**(靠 WebSearch 定性补):港股股息率/股东/质押/公告、大股东质押、审计意见
- **网络适配**:脚本设 `NO_PROXY=*`(国内数据源直连);eastmoney push2 域名在部分代理环境下不可用,脚本已规避

## 免责声明

本项目产出的报告为个人研究底稿,不构成投资建议;不预测短期股价,不提供买卖点位。
