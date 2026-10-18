# -*- coding: utf-8 -*-
"""fin_fetcher.py — 东财真实财务拉取（2026-09-25 新增）

解决问题：原系统的财务字段（扣非/ROE/现金流/营收同比）依赖 data/manual_financial.json
**手工录入**，时效性和准确性取决于录入人。本模块直接用东财接口取真实值。

数据源（均已实测可用）：
  RPT_LICO_FN_CPD            业绩报表 → 营收同比YSTZ / 净利同比SJLTZ / 加权ROE /
                                        毛利率XSMLL / 每股经营现金流MGJYXJJE /
                                        扣非每股收益DEDUCT_BASIC_EPS / 基本EPS
  RPT_F10_FINANCE_GCASHFLOW  现金流量表 → 经营活动净现金流 NETCASH_OPERATE

输出（对齐 FinancialSnapshot 字段名）：
  revenue_yoy / net_profit_yoy / deduct_profit_yoy / gross_margin /
  roe / ocf_to_ni / net_margin / report_date / source

注意口径：ROE 为「加权平均ROE」，报告期口径（H1 为半年值，非年化）；
         扣非同比由「扣非EPS 本期 / 去年同期」推算。
"""
import json
import os
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fin_cache.json")


def _get(u, t=20):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read().decode("utf-8", "ignore")


def _secucode(code6):
    return f"{code6}.{'SH' if code6.startswith(('6', '9')) else 'SZ'}"


def fetch_financial(code6, use_cache=True, refresh=False):
    cache = {}
    if use_cache and os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    today = time.strftime("%Y-%m-%d")
    hit = cache.get(code6)
    if use_cache and not refresh and hit and hit.get("date") == today:
        return hit

    out = {"code": code6, "date": today, "source": "eastmoney"}
    # 1. 业绩报表（近 8 期，用于算同比）
    try:
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD"
             "&columns=ALL&pageNumber=1&pageSize=8&sortColumns=REPORTDATE&sortTypes=-1"
             f"&filter=(SECURITY_CODE%3D%22{code6}%22)")
        rows = (json.loads(_get(u)).get("result") or {}).get("data") or []
        if rows:
            cur = rows[0]
            out["report_date"] = str(cur.get("REPORTDATE", ""))[:10]
            out["revenue_yoy"] = round(float(cur["YSTZ"]), 2) if cur.get("YSTZ") is not None else None
            out["net_profit_yoy"] = round(float(cur["SJLTZ"]), 2) if cur.get("SJLTZ") is not None else None
            # ROE 年化（评分阈值 ≥15% 为年化口径，东财给的是报告期累计值）
            if cur.get("WEIGHTAVG_ROE") is not None:
                _raw = float(cur["WEIGHTAVG_ROE"])
                _mmdd = str(cur.get("REPORTDATE", ""))[5:10]
                _mult = {"03-31": 4.0, "06-30": 2.0, "09-30": 4.0/3.0, "12-31": 1.0}.get(_mmdd, 1.0)
                out["roe_raw"] = round(_raw, 2)
                out["roe"] = round(_raw * _mult, 2)   # 年化，对齐评分口径
                out["roe_mult"] = _mult
            out["gross_margin"] = round(float(cur["XSMLL"]), 2) if cur.get("XSMLL") is not None else None
            out["eps"] = cur.get("BASIC_EPS")
            out["deduct_eps"] = cur.get("DEDUCT_BASIC_EPS")
            out["parent_netprofit"] = cur.get("PARENT_NETPROFIT")
            out["revenue"] = cur.get("TOTAL_OPERATE_INCOME")
            # 扣非同比：找去年同报告期（月份/日相同）
            mmdd = str(cur.get("REPORTDATE", ""))[5:10]
            prev = next((r for r in rows[1:] if str(r.get("REPORTDATE", ""))[5:10] == mmdd), None)
            if prev and cur.get("DEDUCT_BASIC_EPS") is not None and prev.get("DEDUCT_BASIC_EPS"):
                try:
                    _cur_eps = float(cur["DEDUCT_BASIC_EPS"])
                    _prev_eps = float(prev["DEDUCT_BASIC_EPS"])
                    if _prev_eps > 0:
                        out["deduct_profit_yoy"] = round((_cur_eps / _prev_eps - 1) * 100, 2)
                    else:
                        # 去年同期扣非为负/零 → 同比无意义，宁可留空也不给假数
                        out["deduct_profit_yoy"] = None
                        out["deduct_yoy_note"] = f"去年同期扣非EPS为{_prev_eps}，同比不可比"
                except Exception:
                    pass
    except Exception as e:
        out["error_income"] = str(e)[:80]
    # 2. 经营现金流
    try:
        u2 = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_FINANCE_GCASHFLOW"
              "&columns=ALL&pageNumber=1&pageSize=4&sortColumns=REPORT_DATE&sortTypes=-1"
              f"&filter=(SECUCODE%3D%22{_secucode(code6)}%22)")
        rows2 = (json.loads(_get(u2)).get("result") or {}).get("data") or []
        if rows2 and rows2[0].get("NETCASH_OPERATE") is not None:
            ocf = float(rows2[0]["NETCASH_OPERATE"])
            out["ocf"] = ocf
            if out.get("parent_netprofit"):
                try:
                    out["ocf_to_ni"] = round(ocf / float(out["parent_netprofit"]), 2)
                except Exception:
                    pass
    except Exception as e:
        out["error_cash"] = str(e)[:80]

    cache[code6] = out
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    D = r"D:\\云吞下载\\紫苏叶跟踪"
    manual = {}
    try:
        manual = json.load(open(os.path.join(D, "data", "manual_financial.json"), encoding="utf-8"))
    except Exception:
        pass
    codes = sys.argv[1:] or list(manual.keys())[1:8]
    print("%-9s %-8s %-8s %-8s %-8s %-8s %-8s %-8s" % (
        "代码", "报告期", "营收同比", "净利同比", "扣非同比", "ROE", "毛利率", "现金/净利"))
    print("-" * 74)
    for c in codes:
        f = fetch_financial(c)
        print("%-9s %-8s %-8s %-8s %-8s %-8s %-8s %-8s" % (
            c, f.get("report_date", "-"), f.get("revenue_yoy"), f.get("net_profit_yoy"),
            f.get("deduct_profit_yoy"), f.get("roe"), f.get("gross_margin"), f.get("ocf_to_ni")))
        m = manual.get(c) or {}
        if m:
            print("   %-6s └ 手工录入: 营收%s 净利%s 扣非%s 毛利%s 现金流%s" % (
                "", m.get("revenue_yoy"), m.get("net_profit_yoy"), m.get("deduct_profit_yoy"),
                m.get("gross_margin"), m.get("ocf_to_ni")))
