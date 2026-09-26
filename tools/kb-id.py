#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-id.py — 知识点ID 规范化工具（错题库的机器键）

为什么要它：旧格式 `D4-B 业务连续性计划（BCP）` 把「机器键」和「人读的名称」揉在一个自由文本字段里，
导致 ① 统计只能靠人/LLM 手算，必然漂移；② 同知识点多种写法（实测 CIA-Part3 里 `V-9.5` 与 `V-09.5` 并存、
`IT 控制框架` 与 `IT控制框架` 并存）；③ 无法把错题对回精讲笔记锚点。
本工具把两者拆开：**第一个空格前 = 机器键（ID），其后 = 人读名称**。

ID 语法（以精讲笔记为唯一权威来源）：
  D<域>-<A|B>-<考点号>    业务知识类，且该域笔记声明了 `# A`/`# B` 小节（D1–D4）
  D<域>-<考点号>          业务知识类，该域笔记未声明分组（当前 D5）
  D<域>-BF-<序号>         外部检索补录条目（kb-backfill 写入）
  ISACA-<条款号>          标准/准则类
  IPPF-<条款号>           准则类（CIA 遗留）
  <罗马数字>-<章.节>      CIA-Part3 归档旧格式（只识别、不生成）

用法：
  kb-id.py --index   [--project CISA]     # 生成 _知识点索引.md（ID 注册表 + 检索定位层）
  kb-id.py --check   [--project CISA]     # 校验错题库里的知识点ID，报告问题（有 ❌ 时退出码 1）
  kb-id.py --resolve D4-B-4.10a           # ID（或裸考点号 4.10a）→ 精讲笔记锚点
  kb-id.py --list    [--project CISA]     # 列出全部合法 ID（不写文件）
"""
import argparse
import datetime
import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

KB_ROOT = os.environ.get("KB_ROOT") or os.path.join(
    os.path.expanduser("~"), "Documents", "Obsidian Vault", "考试知识库"
)

JINGJIANG = "精讲笔记"
CUOTI = "错题库"
INDEX_NAME = "_知识点索引.md"
RECORD_NAME = "错题记录.md"

# 考点号：实测 CISA 132 个考点 100% 符合，无例外
KEY_RE = re.compile(r"^\d+\.\d+[a-z]?$")
DOMAIN_RE = re.compile(r"^(D\d+)")
GROUP_RE = re.compile(r"^# ([AB])\s")            # '# A 运营' / '# B 韧性'
POINT_RE = re.compile(r"^## 考点 (\S+)\s*(.*)$")  # '## 考点 4.10a BCP 与 DRP 的关系'
BACKFILL_RE = re.compile(r"^## 补录 (.*?)\s*⟨[^⟩]*⟩\s*$")
ID_LINE_RE = re.compile(r"^\s*[-*]?\s*\*\*知识点\s*ID\*\*\s*[：:]\s*(.+?)\s*$")
REC_HEAD_RE = re.compile(r"^## 错题 (\S+)")

# 归档旧格式（只识别）：<罗马数字>-<数字…> / <罗马数字>/…
LEGACY_RE = re.compile(r"^[IVX]+(?:[-/].*)?$")
STD_RE = re.compile(r"^(ISACA|IPPF)-(.+)$")


# ── 读取（保留字节，只读不改） ──────────────────────────────────────────
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def strip_comments(text):
    """把 HTML 注释替换为等量空行（保持行号不变）。
    ⚠️ 必须做：错题库文件顶部有一块被 <!-- --> 包住的「模板」，里面的示例 ID
    不是真记录，不剥掉会永远报一条假问题（实测踩过）。"""
    return COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8").replace("\r\n", "\n")


# ── 扫描精讲笔记 = 权威注册表 ──────────────────────────────────────────
def scan_notes(project):
    """返回 (points, backfills, notes)。points 里每项含 id/domain/group/key/title/file/line。"""
    base = os.path.join(KB_ROOT, project, JINGJIANG)
    points, backfills, notes = [], [], []
    for path in sorted(glob.glob(os.path.join(base, "D*.md"))):
        name = os.path.basename(path)
        m = DOMAIN_RE.match(name)
        if not m:
            continue
        domain = m.group(1)
        rel = f"{JINGJIANG}/{name}"
        group = None
        declared_group = False
        for lineno, line in enumerate(strip_comments(read_text(path)).split("\n"), 1):
            mg = GROUP_RE.match(line)
            if mg:
                group, declared_group = mg.group(1), True
                continue
            mp = POINT_RE.match(line)
            if mp:
                key, title = mp.group(1), mp.group(2).strip()
                points.append({
                    "id": f"{domain}-{group}-{key}" if group else f"{domain}-{key}",
                    "domain": domain, "group": group, "key": key, "title": title,
                    "file": rel, "line": lineno,
                    "key_ok": bool(KEY_RE.match(key)),
                })
                continue
            mb = BACKFILL_RE.match(line)
            if mb:
                backfills.append({
                    "domain": domain, "topic": mb.group(1).strip(),
                    "file": rel, "line": lineno, "id": None,
                })
        if not declared_group:
            notes.append(f"{domain} 的笔记未声明 `# A` / `# B` 小节 → 该域 ID 省略组段（如 `{domain}-1.1a`）")
    # 补录条目：优先用 kb-backfill 盖在条目里的 ID，缺则按标题序兜底编号
    per_domain = {}
    for b in sorted(backfills, key=lambda x: (x["domain"], x["topic"])):
        group = per_domain.setdefault(b["domain"], [])
        group.append(b)
    for domain, items in per_domain.items():
        for i, b in enumerate(items, 1):
            text = strip_comments(read_text(os.path.join(KB_ROOT, project, b["file"])))
            seg = text.split("\n")[b["line"] - 1: b["line"] + 12]
            found = None
            for line in seg:
                mi = ID_LINE_RE.match(line)
                if mi:
                    found = mi.group(1).split()[0]
                    break
            b["id"] = found or f"{domain}-BF-{i}"
            b["derived"] = found is None
    return points, backfills, notes


def build_registry(project):
    points, backfills, notes = scan_notes(project)
    by_id, by_key = {}, {}
    for p in points:
        by_id[p["id"]] = p
        by_key.setdefault(p["key"], []).append(p)
    for b in backfills:
        by_id.setdefault(b["id"], b)
    return {"project": project, "points": points, "backfills": backfills,
            "notes": notes, "by_id": by_id, "by_key": by_key}


# ── ID 解析 ────────────────────────────────────────────────────────────
def split_id(raw):
    """把错题库里的字段值拆成 (机器键, 人读名称)"""
    parts = (raw or "").strip().split(None, 1)
    token = parts[0] if parts else ""
    label = parts[1].strip() if len(parts) > 1 else ""
    return token, label


def classify(token, reg):
    """返回 (等级, 说明)。等级：ok / std / legacy / warn / bad"""
    if not token:
        return "bad", "知识点ID 为空"
    if any(ch in token for ch in "（）()[]*，,、;；"):
        # 旧格式常把名称直接粘在键后面（`III/风险管理（保险部门有效管理）`），
        # 报「名称与键未分隔」比笼统说「混入标点」有用得多。
        # 只对「带括号的粘连」这么判——`V-8.*` 那种通配符仍算不合规。
        glued = any(ch in token for ch in "（）()")
        if glued and re.match(r"^(?:[IVX]+|D\d+)[-/]", token) \
                and not re.match(r"^D\d+-(?:[AB]|BF)-", token):
            return "legacy", "旧格式·名称与键未用空格分隔（ID 段只能是机器键）"
        return "bad", "ID 段含标注/通配符/标点（ID 只能是机器键，名称放在空格之后）"
    m = re.match(r"^D\d+-(?:[AB]|BF)-(.+)$", token) or re.match(r"^D\d+-(\d+\.\d+[a-z]?)$", token)
    if m:
        if token in reg["by_id"]:
            return "ok", ""
        key = token.rsplit("-", 1)[-1]
        if KEY_RE.match(key) and key in reg["by_key"]:
            got = ", ".join(p["id"] for p in reg["by_key"][key])
            return "warn", f"未登记（考点号 {key} 存在，但组段/域对不上，应为 {got}）"
        if key.isdigit() and any(b["id"] == token for b in reg["backfills"]):
            return "ok", ""
        return "warn", "未登记（该 ID 不在精讲笔记注册表里：新考点需先写精讲笔记，未覆盖考点走 kb-backfill）"
    ms = STD_RE.match(token)
    if ms:
        return "std", ""
    if LEGACY_RE.match(token):
        if "/" in token:
            return "legacy", "旧格式·一个 ID 含多个章节（一次错题只应挂一个主考点）"
        if re.match(r"^[IVX]+-?\d*$", token) or not re.search(r"\d+\.\d+", token):
            return "legacy", "旧格式·缺章节号（无法定位到考点）"
        return "legacy", ""
    return "bad", "格式不合规（既不是 D<域>-[组-]<考点号>，也不是 ISACA-/IPPF- 条款号）"


def variant_kind(variants):
    """给一组同义写法定性，把「真问题」和「粒度差异」分开：
    写法不一致 → 同一个考点写了两种编号（统计必劈叉，要修）
    粒度不同   → 粗编号与细编号并存（如 VI-13 与 VI-13.3），不是错误
    需人工判定 → 其余"""
    norms = {norm_token(v).lower() for v in variants}
    if len(norms) == 1:
        return "写法不一致（实为同一考点）"
    if any(len(a) < len(b) and b.startswith(a) for a in norms for b in norms):
        return "粒度不同（粗/细编号并存，非错误）"
    return "名称同义但 ID 不同（需人工判定是否同一考点）"


def norm_name(s):
    """名称归一化：去空白 + 去括号内容 + 小写。用于发现同知识点的多种写法。"""
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s or "")
    return re.sub(r"[\s\u3000]", "", s).lower()


def norm_token(t):
    """键归一化：去掉数字段的前导零（V-09.5 → V-9.5），并去空白。"""
    t = re.sub(r"[\s\u3000]", "", t or "")
    return re.sub(r"(?<![0-9])0+(\d)", r"\1", t)


# ── 错题库解析 ─────────────────────────────────────────────────────────
def parse_records(path):
    recs, cur = [], None
    for lineno, line in enumerate(strip_comments(read_text(path)).split("\n"), 1):
        m = REC_HEAD_RE.match(line)
        if m:
            cur = {"no": m.group(1), "lineno": lineno, "raw": None, "raw_lineno": None}
            recs.append(cur)
            continue
        if cur is not None and cur["raw"] is None:
            mi = ID_LINE_RE.match(line)
            if mi:
                cur["raw"] = mi.group(1)
                cur["raw_lineno"] = lineno
    return recs


def cmd_check(project):
    path = os.path.join(KB_ROOT, project, CUOTI, RECORD_NAME)
    if not os.path.isfile(path):
        print(f"（没有错题记录：{path}）", file=sys.stderr)
        return 1
    reg = build_registry(project)
    recs = parse_records(path)
    rel = f"{CUOTI}/{RECORD_NAME}"

    counts = {"ok": 0, "std": 0, "legacy": 0, "warn": 0, "bad": 0}
    problems, groups, token_labels = [], {}, {}
    for r in recs:
        if r["raw"] is None:
            counts["bad"] += 1
            problems.append((r["lineno"], r["no"], "—", "❌", "整条错题没有「知识点ID」字段"))
            continue
        token, label = split_id(r["raw"])
        level, why = classify(token, reg)
        counts[level] += 1
        if level in ("warn", "bad"):
            problems.append((r["raw_lineno"], r["no"], token, "❌" if level == "bad" else "⚠️", why))
        domains = re.match(r"^([IVX]+|D\d+)", token)
        dom = domains.group(1) if domains else token
        groups.setdefault((dom, norm_name(label)), set()).add(token)
        token_labels.setdefault(norm_token(token), set()).add(norm_name(label))

    print(f"=== 知识点ID 校验：{project}/{rel} ===")
    print(f"错题 {len(recs)} 条 · 规范 {counts['ok']} · 标准类 {counts['std']} · "
          f"归档旧格式 {counts['legacy']} · 未登记 {counts['warn']} · 不合规 {counts['bad']}")
    if reg["by_id"]:
        print(f"（注册表：{len(reg['points'])} 个考点 + {len(reg['backfills'])} 条补录）")

    syn = {k: v for k, v in groups.items() if len(v) > 1}
    if syn:
        real = {k: v for k, v in syn.items() if variant_kind(v).startswith("写法不一致")}
        print(f"\n⚠️ 同一知识点的多种写法：{len(syn)} 组"
              f"（其中「实为同一考点、只是写法不同」{len(real)} 组——这些会让统计劈叉，优先修）")
        for (dom, name), variants in sorted(syn.items())[:15]:
            print(f"  · {dom} / {name}：{'  vs  '.join(sorted(variants))}")
            print(f"      ← {variant_kind(variants)}")
        if len(syn) > 15:
            print(f"  … 另有 {len(syn) - 15} 组")

    ambiguous = {t: v for t, v in token_labels.items() if len(v) > 1}
    if ambiguous:
        print(f"\n⚠️ 同一个 ID 挂了不同名称：{len(ambiguous)} 组（可能是两个考点挤进了一个 ID）")
        for t, names in sorted(ambiguous.items())[:10]:
            print(f"  · {t}：{'  |  '.join(sorted(names))}")

    if problems:
        print(f"\n需要处理：{len(problems)} 条")
        for lineno, no, token, mark, why in problems[:30]:
            print(f"  {mark} L{lineno} {no}  {token}  —— {why}")
        if len(problems) > 30:
            print(f"  … 另有 {len(problems) - 30} 条")
    else:
        print("\n✅ 全部合规（无未登记、无不合规 ID）")
    for n in reg["notes"]:
        print(f"\n说明：{n}")
    return 1 if counts["bad"] or counts["warn"] else 0


# ── 注册表生成 ─────────────────────────────────────────────────────────
HEADER = """# {project} 知识点索引（ID 注册表）

> ⚙️ **本文件由 `kb-id --index` 自动生成，请勿手改**（重跑即覆盖）。
> **用途**：① 错题库「知识点ID」的合法性来源；② ID → 精讲笔记锚点 的解析；③ 检索定位层。
> **ID 语法**：第一个空格前是机器键、其后是人读名称，例如 `D4-B-4.10a BCP 与 DRP 的关系`。
> 生成时间：{now} · 考点 {np} 个 · 补录 {nb} 条
"""


def natural_key(s):
    """自然排序键：让 1.2 排在 1.10 前面（纯字符串排序会反过来）"""
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "") if t != "")


def cmd_index(project, out_path=None):
    reg = build_registry(project)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [HEADER.format(project=project, now=now, np=len(reg["points"]), nb=len(reg["backfills"]))]
    for n in reg["notes"]:
        L.append(f"> ⚠️ {n}")
    L.append("\n## 考点（业务知识类）\n")
    L.append("| ID | 考点号 | 标题 | 锚点 |")
    L.append("|---|---|---|---|")
    for p in sorted(reg["points"], key=lambda x: (natural_key(x["domain"]), natural_key(x["key"]))):
        L.append(f"| `{p['id']}` | {p['key']} | {p['title']} | `{p['file']}:{p['line']}` |")
    L.append(f"\n## 补录（外部检索入库）\n")
    if reg["backfills"]:
        L.append("| ID | 标题 | 锚点 |")
        L.append("|---|---|---|")
        for b in reg["backfills"]:
            L.append(f"| `{b['id']}` | {b['topic']} | `{b['file']}:{b['line']}` |")
    else:
        L.append("（暂无。`kb-backfill` 回写外部检索结论后会出现在这里）")

    dest = out_path or os.path.join(KB_ROOT, project, INDEX_NAME)
    text = "\n".join(L).rstrip("\n") + "\n"
    tmp = dest + ".kbtmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, dest)
    print(f"✅ 已生成 {os.path.relpath(dest, KB_ROOT).replace(os.sep, '/')}")
    print(f"   考点 {len(reg['points'])} 个 · 补录 {len(reg['backfills'])} 条")
    bad_keys = [p for p in reg["points"] if not p["key_ok"]]
    if bad_keys:
        print(f"   ⚠️ 考点号格式异常 {len(bad_keys)} 个：{[p['key'] for p in bad_keys][:10]}")
    for n in reg["notes"]:
        print(f"   ⚠️ {n}")
    return 0


def cmd_resolve(arg, project):
    reg = build_registry(project)
    hit = reg["by_id"].get(arg)
    if not hit:
        cands = reg["by_key"].get(arg, [])
        if len(cands) == 1:
            hit = cands[0]
            print(f"（按裸考点号 `{arg}` 解析到唯一命中）")
        elif len(cands) > 1:
            print(f"考点号 `{arg}` 在多个域存在，请用完整 ID：")
            for c in cands:
                print(f"  {c['id']}  {c['title']}")
            return 2
    if not hit:
        print(f"未登记：{arg}", file=sys.stderr)
        return 1
    print(f"ID      ：{hit['id']}")
    print(f"标题    ：{hit.get('title') or hit.get('topic')}")
    print(f"锚点    ：{hit['file']}:{hit['line']}")
    print(f"Obsidian：[[{hit['file']}#{'考点 ' + hit['key'] + ' ' + hit['title'] if 'key' in hit else '补录 ' + hit.get('topic', '')}]]")
    return 0


def cmd_list(project):
    reg = build_registry(project)
    for p in sorted(reg["points"], key=lambda x: (natural_key(x["domain"]), natural_key(x["key"]))):
        print(f"{p['id']:<16} {p['title']}")
    for b in reg["backfills"]:
        print(f"{b['id']:<16} {b['topic']}  ⟨补录⟩")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="kb-id", description="知识点ID 规范化工具（错题库的机器键）")
    p.add_argument("--index", action="store_true", help="生成 _知识点索引.md")
    p.add_argument("--check", action="store_true", help="校验错题库的知识点ID")
    p.add_argument("--list", action="store_true", help="列出全部合法 ID")
    p.add_argument("--resolve", metavar="ID", help="ID（或裸考点号）→ 精讲笔记锚点")
    p.add_argument("--project", default="CISA", help="科目（默认 CISA）")
    p.add_argument("--out", help="--index 的输出路径（默认 <科目>/_知识点索引.md）")
    args = p.parse_args(argv)

    if args.resolve:
        return cmd_resolve(args.resolve, args.project)
    if args.index:
        return cmd_index(args.project, args.out)
    if args.check:
        return cmd_check(args.project)
    if args.list:
        return cmd_list(args.project)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
