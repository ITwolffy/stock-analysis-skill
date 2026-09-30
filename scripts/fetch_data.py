# -*- coding: utf-8 -*-
"""
stock-analysis skill · 数据层
拉取 A股/港股数据,算好全部衍生指标,输出底稿 markdown 供 Claude 分析。

用法:
    python fetch_data.py 600519 [--refresh]
    python fetch_data.py 00700 [--refresh]
    python fetch_data.py 贵州茅台 [--refresh]

规则(设计定稿,勿改):
    - 6位纯数字 = A股(60/68/00/30 开头);北交所(4/8/92开头)与 B股(900/200)拒绝
    - 4~5位纯数字 = 港股(自动补零到5位)
    - 名称 = 先查 A股代码表(缓存7天),再查内置常见港股表;多命中列候选退出
    - 输出固定 D:\\WorkSpace\\Claude\\股票分析\\_data\\<code>-<YYYYMMDD>.md,与 cwd 无关
    - 当日底稿已存在直接复用;--refresh 强制重拉
    - 单项接口失败只标注「获取失败」,不中断整体
"""
from __future__ import annotations

import os
import socket
import sys
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("TQDM_DISABLE", "1")

# 全局 30s 超时:akshare 内部请求不设超时,挂死的接口会拖垮整个底稿
socket.setdefaulttimeout(30)

warnings.filterwarnings("ignore")

# 报告输出根目录:默认本机路径,可用环境变量 STOCK_ANALYSIS_DIR 覆盖(发布到 GitHub 供他人使用)
REPORT_DIR = Path(os.environ.get("STOCK_ANALYSIS_DIR") or r"D:\WorkSpace\Claude\股票分析")
DATA_DIR = REPORT_DIR / "_data"
NAME_CACHE = DATA_DIR / "name_cache_a.csv"
NAME_CACHE_TTL_DAYS = 7

A_PREFIX_OK = ("60", "68", "00", "30")
BJ_OR_B_PREFIX = ("4", "8", "92", "900", "200")

# 常见港股映射(名称→代码):stock_hk_spot_em 本机不可用,以此兜底
HK_COMMON = {
    "腾讯控股": "00700", "阿里巴巴-SW": "09988", "美团-W": "03690", "小米集团-W": "01810",
    "京东集团-SW": "09618", "网易-S": "09999", "百度集团-SW": "09888", "快手-W": "01024",
    "哔哩哔哩-W": "09626", "携程集团-S": "09961", "金山软件": "03888", "金蝶国际": "00268",
    "阅文集团": "00772", "比亚迪股份": "01211", "比亚迪电子": "00285", "吉利汽车": "00175",
    "长城汽车": "02333", "理想汽车-W": "02015", "蔚来-SW": "09866", "小鹏汽车-W": "09868",
    "中国海洋石油": "00883", "中国石油股份": "00857", "中国石油化工股份": "00386", "中国神华": "01088",
    "中国移动": "00941", "中国电信": "00728", "中国联通": "00762",
    "中国平安": "02318", "友邦保险": "01299", "汇丰控股": "00005", "香港交易所": "00388",
    "招商银行": "03968", "工商银行": "01398", "建设银行": "00939", "农业银行": "01288",
    "中国银行": "03988", "邮储银行": "01658", "中信证券": "06030",
    "蒙牛乳业": "02319", "农夫山泉": "09633", "海底捞": "06862", "颐海国际": "01579",
    "安踏体育": "02020", "李宁": "02331", "泡泡玛特": "09992", "药明生物": "02269",
    "药明康德": "02359", "信达生物": "01801", "百济神州": "06160", "石药集团": "01093",
    "紫金矿业": "02899", "洛阳钼业": "03993", "江西铜业股份": "00358",
    "长和": "00001", "长实集团": "01113", "恒基兆业地产": "00012", "新鸿基地产": "00016",
    "太古股份公司A": "00019", "华住集团-S": "01179", "九毛九": "09922", "东岳集团": "00189",
}

BS_KEYWORDS = ["资产总计", "负债合计", "股东权益", "所有者权益", "货币资金", "存货",
               "应收账款", "商誉", "流动资产", "流动负债", "非流动资产", "非流动负债"]
IS_KEYWORDS = ["营业额", "营业收入", "毛利", "经营溢利", "税前", "净利润", "每股盈利",
               "每股收益", "基本每股"]


def log(msg: str) -> None:
    print(msg, flush=True)


def retry(fn, n: int = 3):
    """网络接口重试:东财域名本机时好时坏,重试2次(间隔2s/5s)再判死"""
    for i in range(n):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == n - 1:
                raise
            time.sleep(2 + 3 * i)


# ---------------------------------------------------------------- 解析输入
def strip_market_marks(q: str) -> str:
    q = q.strip()
    for suf in (".HK", ".hk", "-HK", " HK"):
        if q.endswith(suf):
            q = q[: -len(suf)]
    for suf in (".SH", ".sz", ".SZ", ".sh"):
        if q.upper().endswith(suf.upper()) and q[:-3].isdigit() if len(q) > 3 else False:
            q = q[:-3]
    for pre in ("hk", "HK", "Hk"):
        if q.startswith(pre) and q[len(pre):].isdigit():
            q = q[len(pre):]
    for pre in ("sh", "sz", "SH", "SZ"):
        if q.lower().startswith(pre.lower()) and q[2:].isdigit():
            q = q[2:]
    return q.strip()


def load_a_name_table() -> "tuple[list[tuple[str, str]], str | None]":
    """A 股代码名称表,磁盘缓存 7 天。返回 ([(code, name)...], 错误信息)"""
    import pandas as pd
    import akshare as ak
    if NAME_CACHE.exists() and (date.today() - date.fromtimestamp(NAME_CACHE.stat().st_mtime)).days < NAME_CACHE_TTL_DAYS:
        df = pd.read_csv(NAME_CACHE, dtype=str)
        return list(df.itertuples(index=False, name=None)), None
    try:
        df = ak.stock_info_a_code_name()
    except Exception as e:  # noqa: BLE001
        return [], f"stock_info_a_code_name 失败: {e}"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(NAME_CACHE, index=False, encoding="utf-8-sig")
    return list(df.itertuples(index=False, name=None)), None


def resolve(query: str) -> "tuple[str, str, str]":
    """返回 (market, code, name);解析失败 SystemExit 退出并给出候选"""
    q = strip_market_marks(query)
    if q.isdigit():
        if len(q) == 6:
            if q.startswith(BJ_OR_B_PREFIX[:3]) or q[:3] in BJ_OR_B_PREFIX[3:]:
                sys.exit(f"✗ {q} 是北交所/三板/ B股代码,v1 仅支持沪深主板/创业板/科创板 A股与港股")
            if not q.startswith(A_PREFIX_OK):
                sys.exit(f"✗ {q} 不是有效的沪深A股代码(60/68/00/30 开头)")
            table, err = load_a_name_table()
            name = next((n for c, n in table if c == q), "")
            if not name:
                sys.exit(f"✗ A股代码表中未找到 {q}: {err or '请确认代码'}")
            return "A", q, name
        if 4 <= len(q) <= 5:
            code = q.zfill(5)
            return "HK", code, next((n for n, c in HK_COMMON.items() if c == code), "")
        sys.exit(f"✗ 无法识别 {query}:A股应为6位数字,港股为4~5位数字")
    # 名称查询
    table, err = load_a_name_table()
    if err and not table:
        log(f"! A股名称表不可用({err}),仅能用代码查询")
    exact = [t for t in table if t[1] == q]
    if len(exact) == 1:
        return "A", exact[0][0], exact[0][1]
    fuzzy = [t for t in table if q in t[1]][:8]
    hk_exact = [(n, c) for n, c in HK_COMMON.items() if n == q]
    if len(hk_exact) == 1 and not exact:
        return "HK", hk_exact[0][1], hk_exact[0][0]
    hk_fuzzy = [(n, c) for n, c in HK_COMMON.items() if q in n][:8]
    if exact or fuzzy or hk_fuzzy:
        lines = [f"✗ 「{query}」命中多个结果,请用代码重跑:"]
        for c, n in (exact + fuzzy):
            lines.append(f"    A股  {c}  {n}")
        for n, c in hk_fuzzy:
            lines.append(f"    港股  {c}  {n}")
        sys.exit("\n".join(lines))
    sys.exit(f"✗ 未找到「{query}」:A股名称未命中;常见港股表也未命中,请直接给代码"
             f"(港股4~5位数字,如腾讯 00700)")


# ---------------------------------------------------------------- 工具
def fnum(v, digits=2, suffix="") -> str:
    try:
        f = float(v)
        if f != f:  # NaN
            return "—"
        return f"{f:,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def fyi(v) -> str:
    """亿元"""
    try:
        f = float(v)
        if f != f:
            return "—"
        return f"{f / 1e8:,.1f} 亿"
    except (TypeError, ValueError):
        return "—"


def fpct(v, signed=True) -> str:
    try:
        f = float(v)
        if f != f:
            return "—"
        return f"{f:+.1%}" if signed else f"{f:.1%}"
    except (TypeError, ValueError):
        return "—"


def pct_rank(series, current) -> "float | None":
    s = series.dropna()
    if s.empty:
        return None
    try:
        cur = float(current)
    except (TypeError, ValueError):
        return None
    if cur != cur:
        return None
    return round(float((s <= cur).mean() * 100), 1)


def max_drawdown(closes: "list[float]") -> "float | None":
    if not closes:
        return None
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return mdd


def md_table(headers: list, rows: "list[list]") -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join("—" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def num(s):
    import pandas as pd
    return pd.to_numeric(pd.Series(s), errors="coerce")


# ---------------------------------------------------------------- A股
def fetch_a(code: str, name: str, failures: list, out: list) -> dict:
    import pandas as pd
    import akshare as ak
    today = date.today()
    d5 = today - timedelta(days=365 * 5 + 10)
    y4 = f"{today.year - 6}0101"
    key = {}

    # --- 估值/市值/股本 历史(stock_value_em 一次全包) ---
    val = None
    try:
        val = retry(lambda: ak.stock_value_em(symbol=code))
        val["日期"] = pd.to_datetime(val["数据日期"])
        val5 = val[val["日期"] >= pd.Timestamp(d5)]
        last = val.iloc[-1]
        price = float(last["当日收盘价"])
        key.update(price=price, chg=float(last["当日涨跌幅"]),
                   mcap=float(last["总市值"]), mcap_float=float(last["流通市值"]),
                   shares=float(last["总股本"]))
        pe_rows, pb_rows = [], []
        for label, col in [("PE(TTM)", "PE(TTM)"), ("PE(静)", "PE(静)"), ("市净率", "市净率"),
                           ("总市值", "总市值"), ("市销率", "市销率"), ("市现率", "市现率")]:
            s = num(val5[col])
            cur = s.iloc[-1] if not s.empty else None
            fmt = fyi if col == "总市值" else (lambda x: fnum(x))
            row = [label, fmt(cur), f"{pct_rank(s, cur)}%", fmt(s.median()),
                   fmt(s.min()), fmt(s.max())]
            (pb_rows if label == "市净率" else pe_rows).append(row)
            key[f"cur_{col}"] = None if cur is None or cur != cur else round(float(cur), 2)
            key[f"pct_{col}"] = pct_rank(s, cur)
        out.append(("## 1. 快照(来源 stock_value_em)",
                    md_table(["项目", "数值"],
                             [["现价", fnum(price) + " 元"],
                              ["今日涨跌", fpct(key.get("chg") / 100 if key.get("chg") is not None else None)],
                              ["总市值", fyi(key.get("mcap"))],
                              ["流通市值", fyi(key.get("mcap_float"))],
                              ["总股本", fnum(last["总股本"], 0, " 股")],
                              ["PE(TTM) / PE(静)", f"{fnum(key.get('cur_PE(TTM)'))} / {fnum(key.get('cur_PE(静)'))}"],
                              ["市净率 PB", fnum(key.get("cur_市净率"))],
                              ["市销率 / 市现率", f"{fnum(key.get('cur_市销率'))} / {fnum(key.get('cur_市现率'))}"]])))
        out.append(("## 2. 估值水位(近5年,来源 stock_value_em)",
                    md_table(["指标", "现值", "5年分位", "5年中位", "5年最低", "5年最高"], pe_rows + pb_rows)))
    except Exception as e:  # noqa: BLE001
        failures.append(f"估值快照 stock_value_em: {type(e).__name__} {str(e)[:90]}")

    # --- 月线(东财挂了退回新浪日线重采样,不同机房) ---
    src = "stock_zh_a_hist"
    try:
        try:
            m = retry(lambda: ak.stock_zh_a_hist(symbol=code, period="monthly",
                                                 start_date=d5.strftime("%Y%m%d"), end_date=today.strftime("%Y%m%d"),
                                                 adjust="qfq"))
        except Exception:  # noqa: BLE001
            src = "stock_zh_a_daily(新浪,重采样月线)"
            sym2 = ("sh" if code.startswith("6") else "sz") + code
            d = retry(lambda: ak.stock_zh_a_daily(symbol=sym2, adjust="qfq"))
            d["date"] = pd.to_datetime(d["date"])
            d5f = d[d["date"] >= pd.Timestamp(d5)].set_index("date")
            m = pd.DataFrame({
                "收盘": d5f["close"].resample("ME").last(),
                "最高": d5f["high"].resample("ME").max(),
                "最低": d5f["low"].resample("ME").min(),
            }).dropna()
            m["涨跌幅"] = m["收盘"].pct_change() * 100
            m = m.reset_index().rename(columns={"date": "日期"})
        closes = [float(x) for x in m["收盘"]]
        hi, lo = float(m["最高"].max()), float(m["最低"].min())
        stats = [["最新月线收盘(前复权)", fnum(closes[-1])],
                 ["5年最高 / 最低", f"{fnum(hi)} / {fnum(lo)}"],
                 ["距5年高点", fpct(closes[-1] / hi - 1)],
                 ["最大回撤(月线)", fpct(max_drawdown(closes))],
                 ["5年涨幅(前复权)", fpct(closes[-1] / closes[0] - 1)]]
        key["mdd"] = max_drawdown(closes)
        out.append((f"## 3. 股价(近5年月线,前复权,来源 {src})",
                    md_table(["项目", "数值"], stats) + "\n\n" +
                    md_table(["月份", "收盘", "涨跌幅"],
                             [[str(r["日期"])[:7], fnum(r["收盘"]), fpct(r["涨跌幅"] / 100)]
                              for _, r in m.tail(61).iterrows()])))
    except Exception as e:  # noqa: BLE001
        failures.append(f"月线 stock_zh_a_hist: {type(e).__name__} {str(e)[:90]}")

    # --- 财务摘要(科目×报告期)+ 衍生指标 ---
    try:
        fa = retry(lambda: ak.stock_financial_abstract(symbol=code))
        fa.columns = [str(c) for c in fa.columns]
        dates = [c for c in fa.columns if c not in ("选项", "指标") and c.isdigit()]
        for d in dates:
            fa[d] = num(fa[d])

        def row_of(*keywords, excludes=()):
            for _, r in fa.iterrows():
                ind = str(r["指标"])
                if all(k in ind for k in keywords) and not any(x in ind for x in excludes):
                    return r
            return None

        r_rev = row_of("营业总收入")
        if r_rev is None:
            r_rev = row_of("营业收入")
        r_np = row_of("归母净利润")
        r_kf = row_of("扣非净利润")
        r_ocf = row_of("经营现金流量净额")
        r_gw = row_of("商誉")

        annuals = sorted([d for d in dates if d.endswith("1231")], reverse=True)

        def val(r, d):
            return None if r is None or d not in r.index or r[d] != r[d] else float(r[d])

        def yoy(a, b):
            return None if not a or not b else a / b - 1

        vals = {d: {"rev": val(r_rev, d), "np": val(r_np, d),
                    "kf": val(r_kf, d), "ocf": val(r_ocf, d)} for d in annuals[:7]}
        rows = []
        for i, d in enumerate(annuals[:6]):
            v, p = vals[d], vals.get(annuals[i + 1], {})
            npx = None if not v["ocf"] or not v["np"] else v["ocf"] / v["np"]
            rows.append([d[:4], fyi(v["rev"]), fpct(yoy(v["rev"], p.get("rev"))),
                         fyi(v["np"]), fpct(yoy(v["np"], p.get("np"))), fyi(v["kf"]),
                         fyi(v["ocf"]), fnum(npx, 2)])
        body = md_table(["年度", "营业总收入", "YoY", "归母净利", "YoY", "扣非净利", "经营现金流", "净现比(OCF/归母)"], rows)
        notes = []
        for note_label, note_row in [("商誉", r_gw), ("毛利率", row_of("毛利率")), ("销售净利率", row_of("销售净利率")),
                                     ("净资产收益率(ROE)", row_of("净资产收益率(ROE)"))]:
            if note_row is not None:
                d1 = next((c for c in dates if note_row[c] == note_row[c]), None)
                if d1:
                    notes.append(f"{note_label}({d1}):{fnum(float(note_row[d1]))}")
        if notes:
            body += "\n\n" + " · ".join(notes)
        out.append(("## 4.1 年度关键科目(来源 stock_financial_abstract)", body))

        interim = sorted([d for d in dates if not d.endswith("1231")], reverse=True)
        if interim:
            d1 = interim[0]
            d0 = str(int(d1[:4]) - 1) + d1[4:]
            rows2 = []
            for label, r in [("营业总收入", r_rev), ("归母净利", r_np), ("扣非净利", r_kf), ("经营现金流", r_ocf)]:
                a, b = val(r, d1), val(r, d0)
                rows2.append([f"{label}({d1}累计)", fyi(a), fyi(b), fpct(yoy(a, b))])
            out.append(("## 4.2 最新报告期累计同比(来源 stock_financial_abstract)",
                        md_table(["科目", "本期", "上年同期", "同比"], rows2)))
    except Exception as e:  # noqa: BLE001
        failures.append(f"财务摘要 stock_financial_abstract: {type(e).__name__} {str(e)[:90]}")

    # --- 财务指标(ROE/毛利率等) ---
    try:
        fi = retry(lambda: ak.stock_financial_analysis_indicator(symbol=code, start_year=y4[:4]))
        keep = [c for c in fi.columns if any(k in str(c) for k in
                ("日期", "净资产收益率", "毛利率", "净利率", "增长率", "资产负债率", "经营现金"))]
        fi = fi[keep].copy()
        fi["日期"] = fi["日期"].astype(str).str[:10]
        fi = fi.sort_values("日期", ascending=False).head(12)
        out.append(("## 4.3 财务指标(近12期,来源 stock_financial_analysis_indicator)",
                    md_table(list(fi.columns),
                             [[v if isinstance(v, str) else ("—" if v != v else fnum(v))
                               for v in r] for r in fi.itertuples(index=False)])))
    except Exception as e:  # noqa: BLE001
        failures.append(f"财务指标 stock_financial_analysis_indicator: {type(e).__name__} {str(e)[:90]}")

    # --- 十大流通股东(报告期回溯) ---
    try:
        qs = []
        for y in (today.year, today.year - 1):
            for md_ in ("1231", "0930", "0630", "0331"):
                ds = f"{y}{md_}"
                if ds < today.strftime("%Y%m%d"):
                    qs.append(ds)
        qs = sorted(set(qs), reverse=True)[:6]
        sym = ("sh" if code.startswith("6") else "sz") + code
        gd = None
        for d in qs:
            try:
                t = retry(lambda: ak.stock_gdfx_free_top_10_em(symbol=sym, date=d))
                if t is not None and len(t):
                    gd, used = t, d
                    break
            except Exception:  # noqa: BLE001, BLE001
                continue
        if gd is None:
            raise RuntimeError(f"近6个报告期({qs[0]}~{qs[-1]})均无数据")
        def _chg(r):
            base = str(r.get("增减", ""))
            ratio = r.get("变动比率")
            try:
                f = float(ratio)
                if base not in ("不变", "新进", "None", "") and f == f:
                    return f"{base} 股({f:+.2f}%)"
            except (TypeError, ValueError):
                pass
            return base or "—"

        out.append((f"## 5. 十大流通股东(报告期 {used},来源 stock_gdfx_free_top_10_em)",
                    md_table(["名次", "股东名称", "股东性质", "持股比例(%)", "变动"],
                             [[r["名次"], r["股东名称"], r["股东性质"],
                               fnum(r["占总流通股本持股比例"]), _chg(r)]
                              for _, r in gd.iterrows()])))
    except Exception as e:  # noqa: BLE001
        failures.append(f"十大流通股东 stock_gdfx_free_top_10_em: {type(e).__name__} {str(e)[:90]}")

    # --- 分红(过滤「报告期已建立但预案未出」的全空行) ---
    try:
        fh = retry(lambda: ak.stock_fhps_detail_em(symbol=code))
        fh = fh.sort_values("报告期", ascending=False)
        rows = []
        for _, r in fh.iterrows():
            send, cash = fnum(r["送转股份-送转总比例"]), fnum(r["现金分红-现金分红比例"])
            if send == "—" and cash == "—":
                continue
            rows.append([str(r["报告期"])[:10], send, cash,
                         fpct(r["现金分红-股息率"], signed=False)])
            if len(rows) >= 10:
                break
        out.append(("## 6. 分红送配(近10期,来源 stock_fhps_detail_em)",
                    md_table(["报告期", "送转(每10股)", "派息(每10股,元)", "股息率"], rows)))
    except Exception as e:  # noqa: BLE001
        failures.append(f"分红 stock_fhps_detail_em: {type(e).__name__} {str(e)[:90]}")

    # --- 公告(近60天) ---
    try:
        st = (today - timedelta(days=60)).strftime("%Y%m%d")
        an = retry(lambda: ak.stock_zh_a_disclosure_report_cninfo(symbol=code, market="沪深京", keyword="",
                                                                  category="", start_date=st,
                                                                  end_date=today.strftime("%Y%m%d")))
        an = an.sort_values("公告时间", ascending=False)
        out.append(("## 7. 公告(近60天,来源 stock_zh_a_disclosure_report_cninfo)",
                    md_table(["时间", "标题"],
                             [[str(r["公告时间"])[:10], r["公告标题"]] for _, r in an.iterrows()])))
    except Exception as e:  # noqa: BLE001
        failures.append(f"公告 stock_zh_a_disclosure_report_cninfo: {type(e).__name__} {str(e)[:90]}")

    return key


# ---------------------------------------------------------------- 港股
def fetch_hk(code: str, name: str, failures: list, out: list) -> dict:
    import pandas as pd
    import akshare as ak
    today = date.today()
    d5 = today - timedelta(days=365 * 5 + 10)
    key = {"currency": "HKD"}

    # --- 月线 + 现价(东财挂了退回新浪日线重采样) ---
    src = "stock_hk_hist"
    try:
        try:
            m = retry(lambda: ak.stock_hk_hist(symbol=code, period="monthly",
                                               start_date=d5.strftime("%Y%m%d"), end_date=today.strftime("%Y%m%d"),
                                               adjust="qfq"))
        except Exception:  # noqa: BLE001
            src = "stock_hk_daily(新浪,重采样月线)"
            d = retry(lambda: ak.stock_hk_daily(symbol=code, adjust="qfq"))
            d["date"] = pd.to_datetime(d["date"])
            d5f = d[d["date"] >= pd.Timestamp(d5)].set_index("date")
            m = pd.DataFrame({
                "收盘": d5f["close"].resample("ME").last(),
                "最高": d5f["high"].resample("ME").max(),
                "最低": d5f["low"].resample("ME").min(),
            }).dropna()
            m["涨跌幅"] = m["收盘"].pct_change() * 100
            m = m.reset_index().rename(columns={"date": "日期"})
        closes = [float(x) for x in m["收盘"]]
        hi, lo = float(m["最高"].max()), float(m["最低"].min())
        key["price"] = closes[-1]
        key["mdd"] = max_drawdown(closes)
        out.append((f"## 1. 股价快照(近5年月线,前复权,HKD,来源 {src})",
                    md_table(["项目", "数值"],
                             [["现价(前复权,HKD)", fnum(closes[-1])],
                              ["5年最高 / 最低", f"{fnum(hi)} / {fnum(lo)}"],
                              ["距5年高点", fpct(closes[-1] / hi - 1)],
                              ["最大回撤(月线)", fpct(max_drawdown(closes))],
                              ["5年涨幅(前复权)", fpct(closes[-1] / closes[0] - 1)]]) +
                    "\n\n" + md_table(["月份", "收盘", "涨跌幅"],
                                      [[str(r["日期"])[:7], fnum(r["收盘"]), fpct(r["涨跌幅"] / 100)]
                                       for _, r in m.tail(61).iterrows()])))
    except Exception as e:  # noqa: BLE001
        failures.append(f"月线 stock_hk_hist: {type(e).__name__} {str(e)[:90]}")

    # --- 估值历史(百度:PE TTM / PB / 总市值) ---
    try:
        est_rows = []
        for label, ind in [("市盈率PE(TTM)", "市盈率(TTM)"), ("市净率PB", "市净率"), ("总市值(HKD)", "总市值")]:
            try:
                v = retry(lambda: ak.stock_hk_valuation_baidu(symbol=code, indicator=ind, period="全部"))
                v["date"] = pd.to_datetime(v["date"])
                s = num(v.loc[v["date"] >= pd.Timestamp(d5), "value"])
                cur = s.iloc[-1] if not s.empty else None
                fmt = (lambda x: fnum(x, 1) + " 亿") if ind == "总市值" else (lambda x: fnum(x))
                est_rows.append([label, fmt(cur), f"{pct_rank(s, cur)}%", fmt(s.median()),
                                 fmt(s.min()), fmt(s.max())])
                if ind == "市盈率(TTM)":
                    key["pe"] = None if cur is None or cur != cur else round(float(cur), 2)
                    key["pe_pct"] = pct_rank(s, cur)
                if ind == "总市值":
                    key["mcap_yi"] = None if cur is None or cur != cur else round(float(cur), 1)
            except Exception as e:  # noqa: BLE001
                failures.append(f"港股估值[{ind}] stock_hk_valuation_baidu: {type(e).__name__} {str(e)[:80]}")
        out.append(("## 2. 估值水位(近5年,来源 stock_hk_valuation_baidu)",
                    md_table(["指标", "现值", "5年分位", "5年中位", "5年最低", "5年最高"], est_rows)))
    except Exception as e:  # noqa: BLE001
        failures.append(f"港股估值 stock_hk_valuation_baidu: {type(e).__name__} {str(e)[:90]}")

    # --- 年度财务指标(英文科目,转置) ---
    try:
        fi = retry(lambda: ak.stock_financial_hk_analysis_indicator_em(symbol=code, indicator="年度"))
        fi = fi.drop(columns=[c for c in ("SECUCODE", "ORG_CODE", "DATE_TYPE_CODE") if c in fi.columns])
        fi["REPORT_DATE"] = fi["REPORT_DATE"].astype(str).str[:4]
        fi = fi.set_index("REPORT_DATE").transpose().reset_index()
        fi.columns = ["指标"] + list(fi.columns[1:])
        noise = {"SECURITY_CODE", "SECURITY_NAME_ABBR", "START_DATE", "FISCAL_YEAR", "IS_CNY_CODE"}
        fi = fi[~fi["指标"].isin(noise)]
        def cell(v):
            if isinstance(v, str):
                return v
            return "—" if v != v else fnum(v)
        out.append(("## 3.1 年度财务指标(注意 CURRENCY 行=报表币种,含义见 hk-notes.md,来源 stock_financial_hk_analysis_indicator_em)",
                    md_table(list(fi.columns),
                             [[r[0]] + [cell(x) for x in r[1:]] for r in fi.itertuples(index=False)])))
    except Exception as e:  # noqa: BLE001
        failures.append(f"港股财务指标 stock_financial_hk_analysis_indicator_em: {type(e).__name__} {str(e)[:90]}")

    # --- 利润表 / 资产负债表(长表→透视,中文标准科目) ---
    for label, sym, kws in (("3.2 利润表(年度)", "利润表", IS_KEYWORDS),
                            ("3.3 资产负债表(年度)", "资产负债表", BS_KEYWORDS)):
        try:
            rep = retry(lambda: ak.stock_financial_hk_report_em(stock=code, symbol=sym, indicator="年度"))
            rep["STD_ITEM_NAME"] = rep["STD_ITEM_NAME"].astype(str)
            rep["年"] = rep["REPORT_DATE"].astype(str).str[:4]
            items = [i for i in rep["STD_ITEM_NAME"].unique() if any(k in i for k in kws)]
            piv = (rep[rep["STD_ITEM_NAME"].isin(items)]
                   .pivot_table(index="STD_ITEM_NAME", columns="年", values="AMOUNT", aggfunc="sum")
                   .sort_index())
            piv = piv[sorted(piv.columns, reverse=True)[:6]]  # 近6个年度
            out.append((f"## {label}(来源 stock_financial_hk_report_em,币种以公司报表为准)",
                        md_table(["科目"] + list(piv.columns),
                                 [[idx] + [fyi(v) for v in row] for idx, row in piv.iterrows()])))
        except Exception as e:  # noqa: BLE001
            failures.append(f"港股{sym} stock_financial_hk_report_em: {type(e).__name__} {str(e)[:90]}")

    # --- 公司概况 ---
    try:
        p = retry(lambda: ak.stock_hk_company_profile_em(symbol=code))
        r = p.iloc[0]
        fields = ["公司名称", "英文名称", "注册地", "所属行业", "董事长", "公司成立日期", "员工人数", "公司网址"]
        out.append(("## 4. 公司概况(来源 stock_hk_company_profile_em)",
                    md_table(["项目", "内容"], [[f, r.get(f, "—")] for f in fields])))
        if not name:
            name = str(r.get("公司名称", ""))
            key["name"] = name  # 回传 main,避免生僻港股底稿标题空名
    except Exception as e:  # noqa: BLE001
        failures.append(f"公司概况 stock_hk_company_profile_em: {type(e).__name__} {str(e)[:90]}")

    out.append(("## 5. 已知数据缺口(用 WebSearch 补)",
                f"- 股息率历史:接口不支持,WebSearch「{name or code} 股息率」\n"
                f"- 股东变化/质押/公告:港股无可用接口,WebSearch 补\n"
                f"- 行业与竞争格局:WebSearch 补"))
    return key


# ---------------------------------------------------------------- 主流程
def main() -> int:
    import akshare as ak
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = [a for a in sys.argv[1:] if a != "--refresh"]
    refresh = "--refresh" in sys.argv
    if not args:
        sys.exit(__doc__.split("用法:")[1].split("规则")[0] if "用法:" in __doc__ else __doc__)
    market, code, name = resolve(args[0])
    label = f"{code} {name}".strip()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{code}-{date.today().strftime('%Y%m%d')}.md"
    if path.exists() and not refresh:
        log(f"✓ 当日底稿已存在,直接复用:{path}(加 --refresh 强制重拉)")
        return 0

    failures: list = []
    sections: list = []
    log(f"→ 拉取 {label}({'A股' if market == 'A' else '港股'})…")
    key = fetch_a(code, name, failures, sections) if market == "A" else fetch_hk(code, name, failures, sections)
    if market == "HK" and not name:
        name = key.get("name") or HK_COMMON.get(code, "")

    lines = [f"# 底稿 · {name}({code})· {'A股' if market == 'A' else '港股'}"
             f"· {'CNY' if market == 'A' else 'HKD'}",
             "",
             f"> akshare {getattr(ak, '__version__', '?')} · 拉取时间 {date.today().isoformat()} "
             f"· 命令 fetch_data.py {args[0]}{' --refresh' if refresh else ''}",
             f"> 失败项:{'无' if not failures else ''}"]
    for f in failures:
        lines.append(f"> - {f}")
    lines.append("> 数字一律以本底稿为准;分析纪律见 SKILL.md。")
    lines.append("")
    for title, body in sections:
        lines += [title, "", body, ""]
    lines.append("---")
    lines.append(f"*底稿完 · {len(sections)} 个数据节 · {len(failures)} 个失败项*")
    path.write_text("\n".join(lines), encoding="utf-8")

    mcap = key.get("mcap_yi")
    mcap_str = f"{fnum(mcap, 1)} 亿HKD" if mcap else fyi(key.get("mcap"))
    log(f"✓ 底稿已生成:{path}")
    log(f"  关键数:现价 {fnum(key.get('price'))} · PE(TTM) {fnum(key.get('cur_PE(TTM)') or key.get('pe'))}"
        f"(5年分位 {key.get('pct_PE(TTM)') or key.get('pe_pct')}%)"
        f" · 最大回撤 {fpct(key.get('mdd'))} · 总市值 {mcap_str}")
    if failures:
        log(f"  失败 {len(failures)} 项(底稿内已标注,WebSearch 补)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
