# -*- coding: utf-8 -*-
"""flow_fetcher.py — 主力资金流 + 人气关注度（2026-09-25 新增）

补齐白毛股神方法论里 skill 原先标注「拿不到」的数据：
  ① 主力资金净流入 —— 白毛用它判断「哪些已启动、哪些还在低位」
     主源：新浪财经资金流历史（实测稳定，与东财 cross-check 一致：福晶 9-24 新浪-6256.6万 vs 东财-6192.9万）
     备源：东财 push2his 日线资金流（偶发 IP 限流，自动降级）
  ② 市场人气关注度 —— 对应方法论里的「搜索热度/关注度」
     数据源：东财人气榜（仅前 100 名可获取 → 用于「是否极端热门」预警）

用法：
    from flow_fetcher import fetch_flow, fetch_rank_top100, hot_flag
    f = fetch_flow("002222")     # 资金流
    hot_flag("002222")           # 是否在人气榜前100（极端热门预警）
"""
import json
import os
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CACHE_FLOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flow_cache.json")
CACHE_RANK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "rank_cache.json")


def _get(u, t=14, enc="utf-8", hdr=None, data=None):
    h = dict(UA); h.update(hdr or {})
    return urllib.request.urlopen(urllib.request.Request(u, headers=h, data=data), timeout=t).read().decode(enc, "ignore")


def _sina_flow(code6, num=22, tries=3):
    """新浪资金流（实测偶发空返回/限流 → 退避重试）"""
    pre = "sh" if code6.startswith(("6", "9")) else "sz"
    u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={num}&sort=opendate&asc=0&daima={pre}{code6}")
    for i in range(tries):
        try:
            txt = _get(u, hdr={"Referer": "https://finance.sina.com.cn/"})
            rows = json.loads(txt)
            if isinstance(rows, list) and rows:
                return rows
        except Exception:
            pass
        time.sleep(1.0 * (i + 1))
    return []


def _em_flow(code6, days=22):
    secid = ("1." if code6.startswith(("6", "9")) else "0.") + code6
    u = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt={days}&klt=101&secid={secid}"
         f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65")
    kl = (((json.loads(_get(u, hdr={"Referer": "https://quote.eastmoney.com/"})).get("data") or {})
           .get("klines")) or [])
    out = []
    for x in kl:
        r = x.split(",")
        out.append({"opendate": r[0], "netamount": r[1], "ratioamount": r[6] if len(r) > 6 else None})
    return out


def fetch_flow(code6, days=22, use_cache=True, refresh=False):
    """主力资金流：1日/5日/10日净流入（元）+ 5日流入天数"""
    cache = {}
    if use_cache and os.path.exists(CACHE_FLOW):
        try:
            cache = json.load(open(CACHE_FLOW, encoding="utf-8"))
        except Exception:
            cache = {}
    today = time.strftime("%Y-%m-%d")
    hit = cache.get(code6)
    if use_cache and not refresh and hit and hit.get("date") == today:
        return hit

    out = {"code": code6, "date": today}
    rows, src = [], None
    for name, fn in (("sina", _sina_flow), ("eastmoney", _em_flow)):
        try:
            rows = fn(code6, days)
            if rows:
                src = name
                break
        except Exception as e:
            out.setdefault("errors", []).append(f"{name}:{str(e)[:40]}")
        if rows:
            break
        time.sleep(1.2)
    out["source"] = src
    if rows:
        main = []
        for r in rows:
            try:
                main.append(float(r.get("netamount") or 0))
            except Exception:
                pass
        main = list(reversed(main))          # 新浪按日期倒序 → 转正序
        out["last_date"] = rows[0].get("opendate")
        out["d1"] = main[-1] if main else None
        out["d5"] = sum(main[-5:]) if len(main) >= 5 else None
        out["d10"] = sum(main[-10:]) if len(main) >= 10 else None
        out["inflow_days_5"] = sum(1 for x in main[-5:] if x > 0) if len(main) >= 5 else None
        try:
            out["ratio_d1"] = float(rows[0].get("ratioamount"))
        except Exception:
            pass
    if out.get("d5") is not None:      # 只缓存成功结果，失败下次重取
        cache[code6] = out
        try:
            os.makedirs(os.path.dirname(CACHE_FLOW), exist_ok=True)
            json.dump(cache, open(CACHE_FLOW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    return out


def fetch_rank_top100(use_cache=True):
    """东财人气榜前 100（该接口仅返回前100，实测分页无效）→ {SZ002222: rk}"""
    if use_cache and os.path.exists(CACHE_RANK):
        try:
            c = json.load(open(CACHE_RANK, encoding="utf-8"))
            if c.get("date") == time.strftime("%Y-%m-%d") and c.get("rank"):
                return c["rank"]
        except Exception:
            pass
    rank = {}
    try:
        body = json.dumps({"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
                           "marketType": "", "pageNumber": 1, "pageSize": 100}).encode()
        d = json.loads(_get("https://emappdata.eastmoney.com/stockrank/getAllCurrentList", data=body,
                            hdr={"Content-Type": "application/json", "Referer": "https://emappdata.eastmoney.com/"}))
        for r in (d.get("data") or []):
            if r.get("sc") and r.get("rk"):
                rank[r["sc"]] = int(r["rk"])
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(CACHE_RANK), exist_ok=True)
        json.dump({"date": time.strftime("%Y-%m-%d"), "rank": rank},
                  open(CACHE_RANK, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return rank


def hot_flag(code6, rank_map=None):
    """是否进入人气榜前100（= 极端热门，紫苏叶大忌）"""
    if rank_map is None:
        rank_map = fetch_rank_top100()
    for pre in ("SZ", "SH"):
        rk = rank_map.get(pre + code6)
        if rk:
            return {"in_top100": True, "rank": rk}
    return {"in_top100": False, "rank": None}


if __name__ == "__main__":
    import sys
    codes = sys.argv[1:] or ["002222"]
    print("=== 主力资金流（新浪主源）===")
    print("%-9s %-11s %-11s %-11s %-11s %-6s %s" % ("代码", "最新日", "1日(万)", "5日(万)", "10日(万)", "源", "5日流入天数"))
    print("-" * 80)
    for c in codes:
        f = fetch_flow(c)
        if f.get("d5") is not None:
            print("%-9s %-11s %-11.0f %-11.0f %-11.0f %-6s %s" % (
                c, f.get("last_date", "-"), f["d1"]/1e4, f["d5"]/1e4, f["d10"]/1e4,
                f.get("source"), f.get("inflow_days_5")))
        else:
            print("%-9s ❌ %s" % (c, f.get("errors", "无数据")))
    rk = fetch_rank_top100()
    print(f"\n=== 人气榜前100 ===\n   覆盖 {len(rk)} 只")
    for c in codes:
        h = hot_flag(c, rk)
        print(f"   {c}: {'⚠️ 前100名(第%s名) — 极端热门' % h['rank'] if h['in_top100'] else '未上榜（冷门 ✅）'}")
