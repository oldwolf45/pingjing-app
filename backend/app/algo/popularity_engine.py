# -*- coding: utf-8 -*-
"""popularity_engine.py — 冷门度/认知差引擎

判断一只票还是不是「紫苏叶」（未被发现），还是已经变成「金枪鱼」（被市场充分认知）。

紫苏叶四特征里有两个关于"还没被市场注意"：
  ① 市值小 + 研报覆盖少
  ② 已有订单线索但没人提

四个客观指标（权重）：
  ① 研报覆盖数（近 12 月）        40%
  ② 主动资金持股占流通股比例      30%
  ③ 总市值                       20%
  ④ 换手率                       10%

=== 2026-10-17 重写说明 ===

金枪鱼判定拆成两个独立理由，各自单条成立：

  「变热了」研报 > 20 篇  或  主动资金占流通 > 8%
  「变大了」总市值 > 250 亿

为什么拆：原来是"三条中两条"，导致"变大了但没被发现"的票永远出不了池。
福晶科技研报 0 篇、主动基金 0.51%，只能命中市值一条，按两条制永远卡在池里。

同时修掉机构持仓取数的三处 bug（详见 inst_holdings 文档字符串）。
"""
import json
import os
import re
import time
import urllib.request

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'popularity_cache.json')
CACHE_VER = 4          # 缓存结构版本，旧版自动作废
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': 'https://data.eastmoney.com/'}

# ── 出池阈值（2026-10-17 与用户确认）──────────────────────────
HOT_REPORTS = 20          # 研报 > 20 篇 → 变热
HOT_ACTIVE_RATIO = 8.0    # 主动资金占流通 > 8% → 变热
BIG_MV_YI = 250.0         # 总市值 > 250 亿 → 变大

# 回归阈值（滞回区间，比出池线更严，防边界震荡）
BACK_REPORTS = 10         # 研报 ≤ 10 篇
BACK_ACTIVE_RATIO = 3.0   # 主动资金 ≤ 3%
BACK_MV_YI = 200.0        # 市值 < 200 亿


def _get(url, enc='utf-8', timeout=25):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode(enc, 'ignore')


def report_count(code6, months=12):
    """近 N 个月研报覆盖数（东财个股研报）"""
    end = time.strftime('%Y-%m-%d')
    beg = time.strftime('%Y-%m-%d', time.localtime(time.time() - months * 30 * 86400))
    url = ('https://reportapi.eastmoney.com/report/list?industryCode=*&pageSize=100&industry=*'
           '&rating=&ratingChange=&beginTime=%s&endTime=%s&pageNo=1'
           '&qType=0&orgCode=&code=%s&rcode=&p=1&pageNum=1&pageNumber=1' % (beg, end, code6))
    try:
        d = json.loads(_get(url))
        return int(d.get('hits', 0) or 0)
    except Exception:
        return None


def _is_passive(row):
    """是否被动/非研究型持仓（ETF、指数、指数增强、量化、债券、FOF、对冲）

    为什么排这么多：一只票被 300 只基金持有，不代表有人研究它。
    实测福晶科技 2026 中报有 375 只基金持有，其中指数增强 54 只、
    被动指数 36 只、混合债券 39 只，外加大量量化产品。
    这些是模型扫进来的，不是研究员看过。
    """
    s = '%s|%s' % (row.get('FUND_TYPE') or '', row.get('HOLDER_NAME') or '')
    if 'ETF' in s.upper():
        return True
    for w in ('量化', '指数', '增强', '对冲', '绝对收益', '套利', '保证金'):
        if w in s:
            return True
    # 债券型、偏债型、FOF 也不代表股票研究覆盖
    ft = row.get('FUND_TYPE') or ''
    if ('债券' in ft) or ('偏债' in ft) or ('FOF' in ft):
        return True
    return False


def _is_northbound(row):
    """北向资金（陆股通）—— 东财把它归在"一般法人"里，要单独拎出来"""
    return '香港中央结算' in (row.get('HOLDER_NAME') or '')


def _is_legal_person(row):
    """一般法人 = 原始股东 / 控股股东，不算机构关注度"""
    return (row.get('F9_ORGTYPE_NAME') or '') == '一般法人'


def _valid_period(rd):
    """只认年报(12-31)和中报(06-30)

    季报（03-31 / 09-30）不强制披露机构持仓明细，家数天然残缺。
    实测：福晶科技 2026-09-30 只有 2 条记录，汇川技术 2026-03-31 只有 213 条，
    而 2026-06-30 有 977 条。拿季报算，等于拿假数据判定。
    """
    return rd[5:10] in ('06-30', '12-31')


def inst_holdings(code6):
    """机构持仓明细 → 多口径统计

    修复三处历史 bug：

      ① 分页只取第一页
         原写法 pageSize=500 + sortColumns=HOLD_VALUE 降序，取回的 500 条
         大部分是历史报告期的大额持仓，最新报告期只剩零星几条。
         实测汇川技术明细共 18328 条，原逻辑算出 11 家机构（真实 977 条）。
         现改为 sortColumns=REPORT_DATE 降序 + pageSize=2000。

      ② 抓到刚开披露的空报告期
         原写法直接取 max(REPORT_DATE)。三季报刚开始披露时最新期只有一两条，
         会导致"这家公司没有机构持有"的假结论。
         现改为只用年报和中报（见 _valid_period）。

      ③ 口径混着"一般法人"和同公司多只产品
         原写法把原始股东（中科院福建物构所、香港中央结算）也算成"机构关注"，
         同时把量化基金、指数增强、债券基金都算进去。
         现拆成五类，只用「研究型主动资金 + 北向 + 其他主动机构」这一项。

    返回 dict（拿不到返回 None）：
      report_date      采用哪个报告期
      n_all            全部持有记录条数
      active_ratio     主动资金占流通股 %   ← 核心指标
      research_ratio   其中研究型公募基金部分
      northbound_ratio 其中北向资金部分
      passive_ratio    被动/量化/债券基金合计
      legal_ratio      一般法人（原始股东）
      all_ratio        全部机构合计
    """
    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'reportName=RPT_MAIN_ORGHOLDDETAIL&columns=ALL&pageSize=2000'
           '&sortColumns=REPORT_DATE,HOLD_VALUE&sortTypes=-1,-1'
           '&filter=(SECURITY_CODE%%3D%%22%s%%22)' % code6)
    try:
        d = json.loads(_get(url))
        rows = (d.get('result') or {}).get('data') or []
        if not rows:
            return None

        groups = {}
        for r in rows:
            rd = (r.get('REPORT_DATE') or '')[:10]
            if _valid_period(rd):
                groups.setdefault(rd, []).append(r)
        if not groups:
            return None
        rd = max(groups)               # 最近一个年报/中报
        cur = groups[rd]

        def _r(items):
            return round(sum(float(x.get('FREESHARES_RATIO') or 0) for x in items), 2)

        research, northbound, other_org, passive, legal = [], [], [], [], []
        for r in cur:
            if _is_northbound(r):
                northbound.append(r)
            elif _is_legal_person(r):
                legal.append(r)
            elif (r.get('F9_ORGTYPE_NAME') or '') == '基金':
                (passive if _is_passive(r) else research).append(r)
            else:
                other_org.append(r)     # QFII / 社保 / 保险 / 券商等

        # 主动资金 = 研究型公募基金 + QFII/社保/保险/券商
        # 不含北向：东财把"香港中央结算"归在一般法人里，实测蓝晓科技占流通 25.37%，
        # 大概率混进了股东持股平台或回购专户，比例失真，单独展示不参与判定。
        active_items = research + other_org
        return {
            'report_date': rd,
            'n_all': len(cur),
            'n_research': len(research),
            'active_ratio': _r(active_items),
            'research_ratio': _r(research),
            'northbound_ratio': _r(northbound),
            'other_org_ratio': _r(other_org),
            'passive_ratio': _r(passive),
            'legal_ratio': _r(legal),
            'all_ratio': _r(cur),
        }
    except Exception:
        return None


def inst_holders(code6):
    """兼容旧接口：返回 (机构条数, 占流通股比例)

    保留这个函数是为了不让外部调用方崩。新代码请用 inst_holdings。
    """
    h = inst_holdings(code6)
    if not h:
        return None, None
    return h['n_all'], h['all_ratio']


def tx_snapshot(code6):
    """腾讯行情：市值(亿)/换手率(%)/量比"""
    pre = 'sh' if code6.startswith(('6', '9')) else 'sz'
    try:
        txt = _get('https://qt.gtimg.cn/q=%s%s' % (pre, code6), enc='gbk', timeout=15)
        m = re.search(r'="([^"]+)"', txt)
        if not m:
            return None, None, None
        f = m.group(1).split('~')
        tot_mv = float(f[45]) if len(f) > 45 and f[45] else None      # 总市值(亿)
        turnover = float(f[38]) if len(f) > 38 and f[38] else None    # 换手率(%)
        vol_ratio = float(f[49]) if len(f) > 49 and f[49] else None   # 量比
        return tot_mv, turnover, vol_ratio
    except Exception:
        return None, None, None


def _score_reports(n):
    """研报覆盖数 → 冷门分（0~1）"""
    if n is None:
        return None
    if n == 0:
        return 1.00
    if n <= 3:
        return 0.85
    if n <= 10:
        return 0.60
    if n <= 30:
        return 0.35
    return 0.15


def _score_active_ratio(r):
    """主动资金占流通股比例 → 冷门分

    比"机构家数"干净：不受基金产品拆分影响，排除被动指数基金，
    也不把一般法人（原始股东）算成机构关注。
    """
    if r is None:
        return None
    if r <= 0.5:
        return 1.00
    if r <= 2.0:
        return 0.85
    if r <= 5.0:
        return 0.60
    if r <= 8.0:
        return 0.35
    return 0.10


def _score_mv(mv):
    """市值(亿) → 冷门分：越小越冷门"""
    if mv is None:
        return None
    if mv <= 30:
        return 1.00
    if mv <= 50:
        return 0.85
    if mv <= 100:
        return 0.65
    if mv <= 200:
        return 0.45
    if mv <= 250:
        return 0.30
    return 0.10


def _score_turnover(t):
    """换手率(%) → 冷门分：低换手=冷门"""
    if t is None:
        return None
    if t <= 1.0:
        return 1.00
    if t <= 2.5:
        return 0.80
    if t <= 5.0:
        return 0.55
    if t <= 10.0:
        return 0.30
    return 0.10


def judge_tuna(n_rep, active_ratio, mv):
    """金枪鱼判定 → (是否出池, 理由, 严重度)

    两个独立理由，各自单条成立：
      变热了：研报 > 20 篇  或  主动资金占流通 > 8%
      变大了：总市值 > 250 亿
    """
    is_hot = ((n_rep is not None and n_rep > HOT_REPORTS) or
              (active_ratio is not None and active_ratio > HOT_ACTIVE_RATIO))
    is_big = (mv is not None and mv > BIG_MV_YI)
    if is_hot and is_big:
        return True, 'both', 2
    if is_hot:
        return True, 'hot', 1
    if is_big:
        return True, 'big', 1
    return False, None, 0


def judge_back(n_rep, active_ratio, mv):
    """回归判定 → (是否满足回归条件, 缺哪项)

    三条必须同时满足，用的是比出池线更严的滞回区间。
    """
    miss = []
    if mv is None or mv >= BACK_MV_YI:
        miss.append('市值≥%.0f亿' % BACK_MV_YI)
    if n_rep is None or n_rep > BACK_REPORTS:
        miss.append('研报>%d篇' % BACK_REPORTS)
    if active_ratio is None or active_ratio > BACK_ACTIVE_RATIO:
        miss.append('主动资金>%.0f%%' % BACK_ACTIVE_RATIO)
    return (len(miss) == 0), miss


def evaluate(code6, use_cache=True, force=False):
    """评估单只标的的冷门度"""
    cache = {}
    if use_cache and os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding='utf-8'))
        except Exception:
            cache = {}
    today = time.strftime('%Y-%m-%d')
    hit = cache.get(code6)
    if (not force) and use_cache and hit and hit.get('date') == today and hit.get('v') == CACHE_VER:
        return hit

    n_rep = report_count(code6)
    hold = inst_holdings(code6)
    mv, turnover, vol_ratio = tx_snapshot(code6)

    active_ratio = hold['active_ratio'] if hold else None

    parts = []
    for val, w in [(_score_reports(n_rep), 0.40), (_score_active_ratio(active_ratio), 0.30),
                   (_score_mv(mv), 0.20), (_score_turnover(turnover), 0.10)]:
        if val is not None:
            parts.append((val, w))
    if parts:
        wsum = sum(w for _, w in parts)
        score = round(sum(v * w for v, w in parts) / wsum, 3)
    else:
        score = None

    tuna, reason, severity = judge_tuna(n_rep, active_ratio, mv)

    reasons = []
    if n_rep is not None and n_rep > HOT_REPORTS:
        reasons.append('研报%d篇>%d' % (n_rep, HOT_REPORTS))
    if active_ratio is not None and active_ratio > HOT_ACTIVE_RATIO:
        reasons.append('主动资金%.2f%%>%.0f%%' % (active_ratio, HOT_ACTIVE_RATIO))
    if mv is not None and mv > BIG_MV_YI:
        reasons.append('市值%.0f亿>%.0f亿' % (mv, BIG_MV_YI))

    out = {
        'v': CACHE_VER,
        'code': code6,
        'date': today,
        'popularity_score': score,
        'tuna_flag': tuna,
        'tuna_reason': reason,          # hot / big / both / None
        'tuna_reasons': reasons,        # 具体命中项，便于页面展示
        'tuna_severity': severity,      # 0=健康 1=命中一类 2=两类都中
        'details': {
            'reports_12m': n_rep,
            'inst_holders': hold['n_all'] if hold else None,       # 兼容旧字段：全部条数
            'n_research': hold['n_research'] if hold else None,    # 研究型主动基金只数
            'active_ratio_pct': active_ratio,                      # ← 核心指标
            'research_ratio_pct': hold['research_ratio'] if hold else None,
            'northbound_ratio_pct': hold['northbound_ratio'] if hold else None,
            'other_org_ratio_pct': hold['other_org_ratio'] if hold else None,
            'passive_ratio_pct': hold['passive_ratio'] if hold else None,
            'legal_ratio_pct': hold['legal_ratio'] if hold else None,
            'all_hold_ratio_pct': hold['all_ratio'] if hold else None,
            'hold_report_date': hold['report_date'] if hold else None,
            'total_mv_yi': mv,
            'turnover_pct': turnover,
            'vol_ratio': vol_ratio,
        },
        'missing': [k for k, v in [('reports', n_rep), ('holdings', hold),
                                   ('mv', mv), ('turnover', turnover)] if v is None],
    }
    cache[code6] = out
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return out


if __name__ == '__main__':
    import sys
    codes = sys.argv[1:] or ['002222']
    print('%-8s %-5s %-9s %-7s %-7s %-7s %-6s %-7s %s' % (
        '代码', '研报', '主动资金%', '北向%', '被动%', '市值亿', '冷门分', '出池?', '理由'))
    print('-' * 96)
    for c in codes:
        r = evaluate(c, force=True)
        d = r['details']
        print('%-8s %-5s %-9s %-7s %-7s %-7s %-6s %-7s %s' % (
            c, d['reports_12m'], d['active_ratio_pct'], d['northbound_ratio_pct'],
            d['passive_ratio_pct'], d['total_mv_yi'], r['popularity_score'],
            {'hot': '变热', 'big': '变大', 'both': '两者'}.get(r['tuna_reason'], '否'),
            ' / '.join(r['tuna_reasons'])))
