#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brief.py — 每日要闻简报生成器（服务端，GitHub Actions 运行）
抓取最新财经新闻 → 词库匹配筛选 → 按"进攻/平缓/避险"分组 → 输出 brief.json
数据源：新浪财经 7x24 滚动（主）+ 东方财富快讯（备）
无第三方依赖（仅标准库 urllib / json / re / datetime）。
"""
import json, os, re, urllib.request, urllib.error, datetime

# 北京时间（用于时间戳转换与生成时间标注）
BJ = datetime.timezone(datetime.timedelta(hours=8))
TODAY = datetime.datetime.now(BJ)

SINA_URL = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=60&page=1"
EM_URL   = "https://newsapi.eastmoney.com/api/idx/get?type=1&page=1&page_size=60&callback=emcb"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def strip_jsonp(raw, cb):
    s = raw.strip()
    if s.startswith('try{'):
        s = s[5:]
    if cb and s.startswith(cb + '('):
        s = s[len(cb) + 1:]
    dec = json.JSONDecoder()
    idx = s.find('{')
    if idx < 0:
        return '{}'
    obj, _ = dec.raw_decode(s, idx)
    return json.dumps(obj, ensure_ascii=False)


def fmt_ts(s):
    try:
        ts = int(str(s))
        if ts <= 0:
            return ''
        return datetime.datetime.fromtimestamp(ts, BJ).strftime('%m-%d %H:%M')
    except Exception:
        return str(s or '')


def parse_sina(raw):
    s = strip_jsonp(raw, 'cb123')
    try:
        obj = json.loads(s)
    except Exception:
        return []
    data = (obj.get('result') or {}).get('data') or []
    out = []
    for d in data:
        title = str(d.get('title', '') or '')
        intro = str(d.get('intro') or d.get('content') or d.get('summary') or '')
        text = (title + ' ' + intro).strip()
        if text:
            out.append({'title': title, 'brief': intro[:120], 'text': text,
                        'src': '新浪财经', 'time': fmt_ts(d.get('intime') or d.get('ctime') or '')})
    return out


def parse_em(raw):
    s = strip_jsonp(raw, 'emcb')
    try:
        obj = json.loads(s)
    except Exception:
        return []
    data = (obj.get('data') or {}).get('list') or []
    out = []
    for d in data:
        title = str(d.get('title') or d.get('name') or '')
        brief = str(d.get('summary') or d.get('content') or '')
        text = (title + ' ' + brief).strip()
        if text:
            out.append({'title': title, 'brief': brief[:120], 'text': text,
                        'src': '东方财富', 'time': str(d.get('datetime') or d.get('date') or '')})
    return out


def indices_of(text, kw):
    out = []
    i = 0
    lc = text.lower()
    k = kw.lower() if re.match(r'^[a-z0-9\s]+$', kw, re.I) else kw
    if not kw:
        return out
    while True:
        i = lc.find(k, i)
        if i < 0:
            break
        out.append(i)
        i += max(1, len(k))
    return out


def item_scan(items, lex):
    neg = lex.get('negators', [])
    den = lex.get('deniers', [])
    neg_terms = lex.get('negTerms', [])
    strong_neg = set(lex.get('strongNeg', []))
    matched = []
    for it in items:
        text = it['text']
        # 每个关键词只计一次（取首次出现）；否定词在关键词之前出现即翻转方向
        seen = {}
        for k in lex.get('keywords', []):
            pps = indices_of(text, k['kw'])
            if not pps:
                continue
            pp = pps[0]
            before = text[:pp]
            dirn = k['dir']
            if dirn > 0 and any(n in before for n in den):
                continue  # 利好被否定（暂未/否认…）→ 该关键词整条失效
            if dirn > 0 and any(n in before for n in neg):
                dirn = -dirn
            if k['kw'] in seen:
                continue
            seen[k['kw']] = {'dir': dirn, 'macro': k.get('macro', 0),
                             'w': k.get('w', 1), 'secs': k.get('secs', []), 'cat': k.get('cat', '')}
        hits = list(seen.values())
        if not hits:
            continue
        pos = sum(h['w'] for h in hits if h['dir'] > 0)
        negw = sum(h['w'] for h in hits if h['dir'] < 0)
        wsum = pos + negw
        neg_count = sum(1 for t in neg_terms if t in text)
        net = pos - negw - 2 * neg_count
        md = 'on' if net > 0 else ('off' if net < 0 else 'flat')
        # 强利空事件词（退市/暴雷/制裁…）：除非被 denier 否定，否则直接判避险
        denied = any(n in text for n in den)
        if any(t in text for t in strong_neg) and not denied:
            md = 'off'
        secs = []
        for h in hits:
            for s in h['secs']:
                if s not in secs:
                    secs.append(s)
        kws = list(seen.keys())
        matched.append({'title': it['title'], 'brief': it['brief'], 'src': it['src'],
                        'time': it.get('time', ''), 'weight': wsum, 'on': pos, 'off': negw,
                        'macro_dir': md, 'secs': secs, 'kws': kws})
    matched.sort(key=lambda x: x['weight'], reverse=True)
    return matched


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    lex_path = os.path.join(here, 'lexicon.json')
    if os.path.exists(lex_path):
        lex = json.load(open(lex_path, encoding='utf-8'))
    else:
        lex = {"negators": ["严查", "打击", "收紧"], "deniers": ["暂未", "未落地", "落空"],
               "negatorWindow": 10, "keywords": []}

    items = []
    src_stat = []
    # 主源：新浪财经 7x24
    try:
        raw = http_get(SINA_URL)
        items = parse_sina(raw)
        src_stat.append('新浪财经 %d 条' % len(items))
    except Exception as e:
        src_stat.append('新浪财经 失败: %s' % e)
    # 备源：东方财富（新浪不足时补）
    if len(items) < 20:
        try:
            raw = http_get(EM_URL)
            em = parse_em(raw)
            items = items + em
            src_stat.append('东方财富 %d 条' % len(em))
        except Exception as e:
            src_stat.append('东方财富 失败: %s' % e)

    matched = item_scan(items, lex)
    top = matched[:14]

    on = sum(m['on'] for m in matched)
    off = sum(m['off'] for m in matched)
    total = on + off
    net = on - off
    ratio = net / total if total > 0 else 0
    if total < 3:
        regime, label, strength = 'flat', '平缓（信号不足）', 0
    elif ratio > 0.15:
        regime, label, strength = 'on', '进攻', min(1, ratio)
    elif ratio < -0.15:
        regime, label, strength = 'off', '避险 / 下跌风险', min(1, abs(ratio))
    else:
        regime, label, strength = 'flat', '平缓', abs(ratio)

    # 综述：汇总 top drivers（按 macro 归类）
    on_kw, off_kw = {}, {}
    for m in matched:
        for k in m['kws']:
            mk = [x for x in lex.get('keywords', []) if x['kw'] == k]
            mac = mk[0].get('macro', 0) if mk else 0
            if mac == 1:
                on_kw[k] = on_kw.get(k, 0) + 1
            elif mac == -1:
                off_kw[k] = off_kw.get(k, 0) + 1
    on_drivers = sorted(on_kw, key=on_kw.get, reverse=True)[:5]
    off_drivers = sorted(off_kw, key=off_kw.get, reverse=True)[:5]
    sp = []
    if on_drivers:
        sp.append('进攻线索：' + '、'.join(on_drivers))
    if off_drivers:
        sp.append('避险线索：' + '、'.join(off_drivers))
    summary = '；'.join(sp) if sp else '当日新闻未触发显著关键词信号。'

    brief = {
        'generated_at': TODAY.strftime('%Y-%m-%d %H:%M'),
        'date': TODAY.strftime('%Y-%m-%d'),
        'source': ' | '.join(src_stat),
        'regime': {'regime': regime, 'label': label, 'onScore': round(on, 1),
                   'offScore': round(off, 1), 'strength': round(strength, 2)},
        'summary': summary,
        'items': [{'title': m['title'], 'brief': m['brief'], 'dir': m['macro_dir'],
                   'secs': m['secs'], 'kws': m['kws'], 'src': m['src'], 'time': m['time'],
                   'weight': m['weight']} for m in top]
    }
    out_path = os.path.join(here, 'brief.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(brief, f, ensure_ascii=False, indent=1)

    # 统计日志（GitHub Actions 可见）
    print('=== 每日要闻简报生成 ===')
    print('源：%s' % ' | '.join(src_stat))
    print('新闻总数 %d → 命中关键词 %d 条 → 取 top %d' % (len(items), len(matched), len(top)))
    print('进攻分 %.1f / 避险分 %.1f → 判定：%s (强度 %.0f%%)' % (on, off, label, strength * 100))
    print('综述：%s' % summary)
    for i, m in enumerate(top[:8], 1):
        print('  %d. [%s] %s — %s' % (i, m['macro_dir'], m['title'][:50], '、'.join(m['kws'][:4])))


if __name__ == '__main__':
    main()
