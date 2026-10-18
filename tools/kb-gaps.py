#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-gaps.py — 知识库覆盖率体检（精讲笔记 → 空心考点清单）

回答一个具体问题：**哪些考点是「空心」的**——即存在于笔记里、但内容没有可靠依据。
三档判据，风险从高到低：

  🔴 内容缺口   标了「待确认/待官方/待 CRM28」且**找不到任何权威外部源** → 内容本身不可靠
  🟡 用词待核   有权威外部源（NIST / ISO / ISACA 官方域名）→ 内容已坐实，只剩术语用词待核
  ⚪ 声明未覆盖 正文自述「本地教材未覆盖 / 本地无专节 / 大纲级提纲」→ 已知盲区
  另外单独统计：无引证（整段找不到 `[L123](教材原文/…)` 这种真引用）

用法：
  kb-gaps.py [--project CISA] [--list]     # 默认打印分域汇总；--list 追加逐条明细
  kb-gaps.py --project CISA --json         # 机器可读
"""
import argparse
import glob
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KB_ROOT = os.environ.get("KB_ROOT") or os.path.join(
    os.path.expanduser("~"), "Documents", "Obsidian Vault", "考试知识库"
)

POINT = re.compile(r"^## 考点 (\S+)\s*(.*)$")
HEAD = re.compile(r"^#{1,3} ")
# 真引证：本地锚点必须带行号
RE_CITE = re.compile(r"\[L\d+[^\]]*\]\(教材原文/")
# 待核对标记
RE_PENDING = re.compile(r"待\s*CRM28|待确认|待官方|待核")
# 自述未覆盖
RE_GAP = re.compile(r"本地教材未覆盖|本地无专节|未覆盖|素材不足|待补|大纲级提纲")
# 权威外部源：必须是指向权威域名的**链接**。
# 注意：只出现 "COBIT"/"ITAF" 这类词不算——那通常是在讲内容，不是在给出处。
# 下表就是"什么算权威"的定义；引入新来源时加这里（只影响体检分级，不影响内容）。
AUTHORITY_HOSTS = (
    "nist.gov",          # NIST（含 csrc. / nvlpubs. 子域）
    "iso.org",           # ISO
    "isaca.org",         # ISACA（含 isaca.net.cn 另列）
    "isaca.net.cn",
    "gao.gov",           # GAO / FISCAM
    "opengroup.org",     # The Open Group / TOGAF
    "cmmiinstitute.com", # CMMI Institute
    "coso.org",          # COSO
    "theiia.org",        # IIA（内部审计准则 IPPF / QAIP / CSA）
    "pcaobus.org",       # PCAOB（美国审计准则，如 AS 2315 审计抽样）
    "acfe.com",          # ACFE（舞弊三角 / 舞弊防范）
    "aicpa-cima.com",    # AICPA & CIMA（SOC 1/2/3 标准制定方）
    "aicpa.org",         # AICPA（旧域，指向同一机构）
)
RE_AUTHORITY = re.compile(
    r"\]\(https?://(?:[A-Za-z0-9._-]+\.)?(?:"
    + "|".join(re.escape(h) for h in AUTHORITY_HOSTS)
    + r")(?=[/\s)#?])"
)


def parse_points(path):
    """切出该域文件里的所有 `## 考点` 块"""
    blocks, cur = [], None
    for i, ln in enumerate(open(path, encoding="utf-8", errors="replace").read().split("\n")):
        m = POINT.match(ln)
        if m:
            if cur:
                blocks.append(cur)
            cur = {"key": m.group(1), "title": m.group(2).strip(), "line": i + 1, "body": []}
        elif cur is not None:
            if HEAD.match(ln):
                blocks.append(cur)
                cur = None
            else:
                cur["body"].append(ln)
    if cur:
        blocks.append(cur)
    return blocks


def scan(project):
    rows, totals = [], {}
    for path in sorted(glob.glob(os.path.join(KB_ROOT, project, "精讲笔记", "D*.md"))):
        dom = os.path.basename(path).split("-")[0]
        if path:
            pass
        d = totals.setdefault(dom, dict(n=0, nocite=0, red=0, yellow=0, gap=0))
        for b in parse_points(path):
            # ⚠️ 先剥掉 Obsidian 双链 [[…]]：关联考点里会引用**别的考点标题**，
            # 而标题里可能带「待CRM28确认」——不剥掉就会把它串味到本考点上（实测踩过假阳性）
            body = re.sub(r"\[\[[^\]]*\]\]", "", "\n".join(b["body"]))
            no_cite = not RE_CITE.search(body)
            pending = bool(RE_PENDING.search(body))
            gap = bool(RE_GAP.search(body))
            authority = bool(RE_AUTHORITY.search(body))
            d["n"] += 1
            d["nocite"] += no_cite
            d["gap"] += gap
            level = None
            if pending and not authority:
                level = "red"
                d["red"] += 1
            elif pending and authority:
                level = "yellow"
                d["yellow"] += 1
            if level or no_cite:
                rows.append({
                    "domain": dom, "key": b["key"], "title": b["title"], "line": b["line"],
                    "level": level or "info", "no_cite": no_cite,
                    "flags": ([l for l, ok in (("无引证", no_cite), ("自述未覆盖", gap)) if ok]),
                })
    return totals, rows


def main(argv=None):
    p = argparse.ArgumentParser(prog="kb-gaps", description="知识库覆盖率体检")
    p.add_argument("--project", default="CISA")
    p.add_argument("--list", action="store_true", help="追加逐条明细")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args(argv)

    totals, rows = scan(args.project)
    if not totals:
        print(f"（没有找到 {args.project}/精讲笔记/D*.md）", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"totals": totals, "items": rows}, ensure_ascii=False, indent=2))
        return 1 if any(d["red"] for d in totals.values()) else 0

    print(f"=== 覆盖率体检：{args.project} ===")
    print(f"{'域':<5}{'考点':>5}{'🔴内容缺口':>11}{'🟡用词待核':>11}{'自述未覆盖':>11}{'无引证':>8}")
    gt = dict.fromkeys(("n", "red", "yellow", "gap", "nocite"), 0)
    for dom in sorted(totals):
        d = totals[dom]
        for k in gt:
            gt[k] += d[k]
        print(f"{dom:<5}{d['n']:>5}{d['red']:>11}{d['yellow']:>11}{d['gap']:>11}{d['nocite']:>8}")
    print(f"{'合计':<5}{gt['n']:>5}{gt['red']:>11}{gt['yellow']:>11}{gt['gap']:>11}{gt['nocite']:>8}")

    reds = [r for r in rows if r["level"] == "red"]
    if reds:
        print(f"\n🔴 内容缺口（{len(reds)}）—— 优先处理：无权威来源")
        for r in reds:
            print(f"  {r['domain']}-{r['key']:<8} {r['title'][:38]:<38} L{r['line']}")
    if args.list:
        yel = [r for r in rows if r["level"] == "yellow"]
        if yel:
            print(f"\n🟡 用词待核（{len(yel)}）—— 内容已坐实，仅 ISACA 用词待核")
            for r in yel:
                print(f"  {r['domain']}-{r['key']:<8} {r['title'][:38]:<38} L{r['line']}")
        info = [r for r in rows if r["level"] == "info"]
        if info:
            print(f"\n⚪ 无引证（{len(info)}）")
            for r in info:
                print(f"  {r['domain']}-{r['key']:<8} {r['title'][:38]:<38} L{r['line']}")
    print("\n提示：🔴 = 内容本身没有可靠依据（要补）；🟡 = 内容已用权威源坐实，只剩术语用词待核。")
    return 1 if gt["red"] else 0


if __name__ == "__main__":
    sys.exit(main())
