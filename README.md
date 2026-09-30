# stock-analysis · Claude Code 个股深度分析 Skill

一个 [Claude Code](https://claude.com/claude-code) skill:输入 A股/港股代码或名称,自动拉取经过验证的结构化数据(行情、估值分位、财务、股东、分红、公告),按价值投资框架产出九节深度研究报告(Markdown + HTML)。

**设计哲学**:数据可靠性交给脚本,判断深度交给模型——数字一律来自 [akshare](https://github.com/akfamily/akshare) 底稿,搜索只做定性补充,每个结论标注证据出处。

## 产出什么

一份九节结构的报告(结论先行):

```
0. 一页结论(关键数字表 / 三个关注 / 三个风险 / 结论:深度研究|观望|回避)
1. 这门生意          5. 估值(5年分位 + 悲观/中性/乐观三情景粗算)
2. 生意质量(红旗核对)  6. 风险清单(概率×影响 + 兑现信号)
3. 成长与天花板        7. 结论与跟进(重估触发条件 + 跟踪清单)
4. 治理与股东回报      附录(数据与来源)
```

外加同名 HTML(明暗双主题、可打印)。

## 安装

```bash
# 1. 复制到 Claude Code 的用户级 skills 目录
git clone https://github.com/<你的用户名>/stock-analysis.git ~/.claude/skills/stock-analysis

# 2. 安装依赖(版本锁定,akshare 接口变动频繁)
pip install -r ~/.claude/skills/stock-analysis/requirements.txt

# 3.(可选)自定义报告输出目录;不设则写到脚本内默认路径
export STOCK_ANALYSIS_DIR=/path/to/reports
```

## 用法

在 Claude Code 会话中:

```
/stock-analysis 600519
/stock-analysis 00700
/stock-analysis 贵州茅台          # 名称自动解析,多命中会列候选
"帮我分析一下腾讯值不值得买"        # 自然语言同样触发
```

也可单独使用数据层(不经过 Claude):

```bash
python scripts/fetch_data.py 600519 [--refresh]
# 输出底稿: <STOCK_ANALYSIS_DIR>/_data/600519-YYYYMMDD.md
```

底稿当日缓存,`--refresh` 强制重拉;所有衍生指标(估值 5 年分位、最大回撤、同比、净现比)由脚本算好,避免模型心算出错。

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

- A股:`stock_value_em`(估值/市值历史)、`stock_financial_abstract`、`stock_financial_analysis_indicator`、`stock_gdfx_free_top_10_em`、`stock_fhps_detail_em`、巨潮公告等
- 港股:`stock_hk_hist`、`stock_hk_valuation_baidu`(PE/PB/总市值历史)、东财港股财务三表、公司概况
- 范围:沪深 A股 + 港股;北交所/B股/美股拒绝
- 内置容错:接口闪断自动重试;东财行情接口挂掉自动降级新浪日线重采样;全局 30s 超时防挂死;单项失败只标注不中断
- 已知缺口(靠 WebSearch 定性补):港股股息率/股东/质押/公告、大股东质押、审计意见
- 本机适配:脚本设 `NO_PROXY=*`(国内数据源直连);eastmoney push2 域名在部分代理环境下不可用,脚本已规避

## 免责声明

本项目产出的报告为个人研究底稿,不构成投资建议;不预测短期股价,不提供买卖点位。
