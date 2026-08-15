#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块轮动概率雷达 —— 服务端抓取脚本（无第三方依赖，仅标准库）
配套手机版 index.html 使用：
  - 抓东方财富公开接口：指数 / 行业板块 / 概念板块 / 个股资金流 / 板块历史资金流
  - 输出 latest.json（供手机端断网兜底）
  - 归档 snapshots/<日期-时分>.json + snapshots/index.json（运行记录）
设计原则（沿用用户工程偏好）：
  - 配置化：HOST / UT / 板块数等集中在顶部
  - 关键步骤统计日志：原始 N -> 成功 M，失败原因可见
  - 先稳后快：多域名兜底 + 重试；单源失败不影响整体
  - 拒绝无证据：抓不到就记失败，不伪造数据
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

# ============================== 配置 ==============================
UT = "b2884a393a59ad64002292a3e90d46a5"
EM_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com"]          # 指数/板块/个股
EM_HIS_HOSTS = ["push2his.eastmoney.com", "push2delay.eastmoney.com"]   # 历史资金流
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REFERER = "https://quote.eastmoney.com/"
TIMEOUT = 15
RETRY = 2
HIST_TOP = 10            # 行业/概念各取前 N 个拉历史
ROOT = os.path.dirname(os.path.abspath(__file__))

CST = timezone(timedelta(hours=8))   # 北京时间


def log(msg):
    ts = datetime.now(CST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ============================== 请求层 ==============================
def _strip_jsonp(text, cb):
    """东财带 cb 时返回 cb({...});剥掉前后缀拿到纯 JSON。"""
    s = text.strip()
    if s.startswith(cb + "(") or s.startswith(cb + " ("):
        s = s[s.index("(") + 1:]
        if s.endswith(");"):
            s = s[:-2]
        elif s.endswith(")"):
            s = s[:-1]
    return s


def http_get_json(url, cb_name=None, timeout=TIMEOUT, retry=RETRY):
    last_err = None
    for attempt in range(1, retry + 1):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", UA)
            req.add_header("Referer", REFERER)
            req.add_header("Accept", "*/*")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            if cb_name:
                raw = _strip_jsonp(raw, cb_name)
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"    · 尝试{attempt}失败: {e}")
            if attempt < retry:
                time.sleep(1.5 * attempt)
    raise last_err or RuntimeError("未知错误")


def clist_url(host, fs, pz, fields):
    return ("https://" + host + "/api/qt/clist/get?pn=1&pz=" + str(pz) +
            "&po=1&np=1&ut=" + UT + "&fltt=2&invt=2&fid=f62&fs=" +
            urllib.parse.quote(fs, safe="") + "&fields=" + fields)


def ulist_url(host, secids, fields):
    return ("https://" + host + "/api/qt/ulist.np/get?fltt=2&invt=2&fields=" +
            fields + "&secids=" + secids + "&ut=" + UT)


def his_url(host, secid):
    return ("https://" + host + "/api/qt/stock/fflow/daykline/get?lmt=30&klt=101&secid=" +
            secid + "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&ut=" + UT)


def norm_diff(d):
    if not d:
        return []
    data = d.get("data")
    if not data:
        return []
    diff = data.get("diff")
    if diff is None:
        return []
    return list(diff.values()) if isinstance(diff, dict) else (diff if isinstance(diff, list) else [])


def norm_klines(d):
    if not d:
        return None
    data = d.get("data")
    if not data:
        return None
    return data.get("klines")


def fetch_boards(fs, pz):
    cb = "emcb_" + str(int(time.time() * 1000))
    for host in EM_HOSTS:
        try:
            url = clist_url(host, fs, pz, "f2,f3,f8,f10,f12,f14,f62,f66,f72,f78,f84,f104,f105,f184") + "&cb=" + cb
            return norm_diff(http_get_json(url, cb))
        except Exception as e:  # noqa: BLE001
            log(f"    · 域名 {host} 板块列表失败: {e}")
    return []


def fetch_indices():
    secids = "1.000001,0.399001,0.399006,1.000300,1.000905,1.000688"
    cb = "emcb_" + str(int(time.time() * 1000))
    for host in EM_HOSTS:
        try:
            url = ulist_url(host, secids, "f2,f3,f6,f12,f14") + "&cb=" + cb
            return norm_diff(http_get_json(url, cb))
        except Exception as e:  # noqa: BLE001
            log(f"    · 域名 {host} 指数失败: {e}")
    return []


def fetch_stocks():
    fs = ("m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,"
          "m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2")
    cb = "emcb_" + str(int(time.time() * 1000))
    for host in EM_HOSTS:
        try:
            url = clist_url(host, fs, 12, "f2,f3,f10,f12,f14,f62,f184") + "&cb=" + cb
            return norm_diff(http_get_json(url, cb))
        except Exception as e:  # noqa: BLE001
            log(f"    · 域名 {host} 个股失败: {e}")
    return []


def fetch_history(secid):
    cb = "emcb_" + str(int(time.time() * 1000))
    for host in EM_HIS_HOSTS:
        try:
            url = his_url(host, secid) + "&cb=" + cb
            kl = norm_klines(http_get_json(url, cb))
            if kl and len(kl) >= 2:
                return kl
            log(f"    · 域名 {host} 历史仅 {len(kl) if kl else 0} 天")
        except Exception as e:  # noqa: BLE001
            log(f"    · 域名 {host} 历史失败: {e}")
    return None


# ============================== 主流程 ==============================
def main():
    run_start = datetime.now(CST)
    log("=== 开始抓取（北京时间 %s）===" % run_start.strftime("%Y-%m-%d %H:%M:%S"))
    stats = {"indices": 0, "industry": 0, "concept": 0, "stocks": 0, "hist_ok": 0, "hist_fail": 0}

    log("① 指数 ...")
    indices = fetch_indices()
    stats["indices"] = len(indices)
    log(f"    -> 指数 {stats['indices']} 条")

    log("② 行业板块 (fs=m:90+t:2) ...")
    industry = fetch_boards("m:90+t:2", 30)
    stats["industry"] = len(industry)
    log(f"    -> 行业 {stats['industry']} 条")

    log("③ 概念板块 (fs=m:90+t:3) ...")
    concept = fetch_boards("m:90+t:3", 30)
    stats["concept"] = len(concept)
    log(f"    -> 概念 {stats['concept']} 条")

    log("④ 个股主力净流入 TOP12 ...")
    stocks = fetch_stocks()
    stats["stocks"] = len(stocks)
    log(f"    -> 个股 {stats['stocks']} 条")

    # 历史资金流（板块代码即 f12，如 BK0438；secid=90.BKxxxx）
    hist = {}
    top_boards = [b for b in industry[:HIST_TOP]] + [b for b in concept[:HIST_TOP]]
    log(f"⑤ 历史资金流（{len(top_boards)} 个板块）...")
    for b in top_boards:
        code = b.get("f12")
        if not code:
            continue
        kl = fetch_history("90." + code)
        if kl:
            hist[code] = kl
            stats["hist_ok"] += 1
        else:
            stats["hist_fail"] += 1
    log(f"    -> 历史成功 {stats['hist_ok']}，失败 {stats['hist_fail']}")

    ok = stats["indices"] and (stats["industry"] or stats["concept"])
    result = {
        "updated": run_start.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source": "server-snapshot",
        "status": "ok" if ok else "partial",
        "stats": stats,
        "indices": indices,
        "industry": industry,
        "concept": concept,
        "stocks": stocks,
        "hist": hist,
    }

    latest_path = os.path.join(ROOT, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    log(f"✅ 写入 latest.json（{os.path.getsize(latest_path)} 字节）")

    # 归档快照
    stamp = run_start.strftime("%Y-%m-%d-%H%M")
    snap_dir = os.path.join(ROOT, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    snap_path = os.path.join(snap_dir, stamp + ".json")
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    # 更新索引
    idx_path = os.path.join(snap_dir, "index.json")
    index = []
    if os.path.exists(idx_path):
        try:
            with open(idx_path, encoding="utf-8") as f:
                index = json.load(f)
        except Exception:  # noqa: BLE001
            index = []
    index.insert(0, {
        "time": result["updated"],
        "status": result["status"],
        "stats": stats,
        "file": "snapshots/" + stamp + ".json",
    })
    index = index[:60]
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    log("=== 完成：状态=%s，指数/行业/概念/个股=%s，历史 %d/%d ===" % (
        result["status"],
        (stats["indices"], stats["industry"], stats["concept"], stats["stocks"]),
        stats["hist_ok"], stats["hist_ok"] + stats["hist_fail"]))
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log("❌ 致命错误: " + str(e))
        # 即便全失败也写一份 latest.json 标记错误，保证手机端 fallback 有明确状态
        err = {
            "updated": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "source": "server-snapshot",
            "status": "error",
            "error": str(e),
            "indices": [], "industry": [], "concept": [], "stocks": [], "hist": {},
        }
        with open(os.path.join(ROOT, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(err, f, ensure_ascii=False, separators=(",", ":"))
        sys.exit(1)
