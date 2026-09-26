#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-backfill.py — 把「外部检索结论」回写进精讲笔记（检索→沉淀闭环）

背景：CISA 2024 新考纲的部分内容（AI、区块链、远程办公风险等）本地教材没有覆盖，
按《检索协议》第 7 条走外部检索（限 2 轮）答完题，结论就留在会话里蒸发了 ——
下次遇到同一考点还得再搜一遍。本工具把结论落进精讲笔记，让第 3 次成本归零。

用法：
  kb-backfill --from entry.json          # 写入/更新一条补录
  kb-backfill --from entry.json --dry-run  # 只打印将写入的内容，不落盘
  kb-backfill --list [--project CISA]    # 列出各域已有的补录条目

entry.json 字段（UTF-8）：
  {
    "project":   "CISA",                    # 必填，科目目录名
    "domain":    "D4",                      # 必填，D1~D5
    "topic":     "无人值守批处理的风险",      # 必填，身份键（同 topic 重复写=更新，不重复追加）
    "conclusion":"……",                      # 必填，核心结论
    "sources":   ["https://…"],             # 必填且至少 1 条 http(s)——**无来源不入库**
    "position":  "板块 D4 → A IS运营 → 无人值守",   # 选填
    "exam":      "……",                      # 选填，考法
    "pitfall":   "……",                      # 选填，易错点
    "related":   ["考点 4.2a …"],            # 选填，关联考点
    "date":      "2026-09-26"               # 选填，默认今天
  }

三条硬约束（都是为了让外部内容可复核、可撤销）：
  1. **无来源不入库** —— sources 为空或非 http(s) 直接拒绝，不写文件
  2. **只追加不覆写正文** —— 补录一律落在域文件末尾的「外部检索补录」节，
     正文考点原样不动；每次写入前把原文件备份到 精讲笔记/.kb-backfill/
  3. **条目自带状态与出处** —— 每个补录条目标 ⟨待 CRM28 确认⟩ + 来源链接 + 入库日期，
     台账 `_补录台账.md` 记一行摘要。核对 CRM28 后把条目并入正文并删除本节条目。

设计依据：与《检索协议》既有的「待 CRM28 确认」约定一致，不另造格式。
"""
import argparse
import codecs
import datetime
import glob
import json
import os
import re
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

KB_ROOT = os.environ.get("KB_ROOT") or os.path.join(
    os.path.expanduser("~"), "Documents", "Obsidian Vault", "考试知识库"
)

SECTION_TITLE = "# 外部检索补录（待官方教材确认）"
ENTRY_TITLE_PREFIX = "补录 "          # 条目标题正文（不含 #），也是 Obsidian 块链接的依据
ENTRY_PREFIX = "## " + ENTRY_TITLE_PREFIX
# 待确认标记：默认通用；各科目可自带（entry.json 的 pending_mark，或环境变量 KB_PENDING_MARK）
# 例：CISA 用 "⟨待 CRM28 确认⟩"（CRM28 = CISA Review Manual 2024）
PENDING_MARK = "⟨待官方教材确认⟩"
MARK_RE = re.compile(r"⟨[^⟩]*⟩")
LEDGER_NAME = "_补录台账.md"
BACKUP_DIRNAME = ".kb-backfill"
JINGJIANG = "精讲笔记"

SECTION_INTRO = (
    "> 本节由 `kb-backfill` 自动维护（别手改结构）。内容来自 **web 检索**，不是官方教材——\n"
    "> 核对官方教材后请把条目并入上方正文考点，并从这里删除；台账见 [[_补录台账]]。\n"
    "> **无来源不入库**：每条都必须带来源链接才允许写入。"
)


# ── 文件定位 ────────────────────────────────────────────────────────────
def find_domain_file(project, domain):
    """定位某科目的域精讲笔记文件。返回绝对路径；找不到/多解返回 None 并打印原因。"""
    base = os.path.join(KB_ROOT, project, JINGJIANG)
    if not os.path.isdir(base):
        print(f"（目录不存在：{base}）", file=sys.stderr)
        return None
    hits = sorted(glob.glob(os.path.join(base, f"{domain}-*.md")))
    if not hits:
        alt = os.path.join(base, f"{domain}.md")
        hits = [alt] if os.path.isfile(alt) else []
    if not hits:
        print(f"（未找到 {project}/{JINGJIANG}/{domain}-*.md —— 先建好该域笔记再回写）", file=sys.stderr)
        return None
    if len(hits) > 1:
        print(f"（{domain} 匹配到多个文件，无法确定写哪个：{hits}）", file=sys.stderr)
        return None
    return hits[0]


# ── 读写（保留换行符与 BOM，避免"只是追加却重写了整个文件"） ──────────────
def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(codecs.BOM_UTF8)
    if bom:
        raw = raw[len(codecs.BOM_UTF8):]
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    return text.replace("\r\n", "\n"), crlf, bom


def write_text(path, text, crlf, bom):
    out = text.replace("\n", "\r\n") if crlf else text
    data = out.encode("utf-8")
    if bom:
        data = codecs.BOM_UTF8 + data
    tmp = path + ".kbtmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def backup(path):
    d = os.path.join(os.path.dirname(path), BACKUP_DIRNAME)
    os.makedirs(d, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(d, f"{os.path.basename(path)}.{ts}.bak")
    shutil.copy2(path, dst)
    return dst


# ── 解析 / 渲染 ────────────────────────────────────────────────────────
def split_file(text):
    """把域文件切成 (正文, 已有补录条目列表)。正文原样保留，一个字符都不动。"""
    idx = text.find(SECTION_TITLE)
    if idx == -1:
        return text.rstrip("\n"), []
    head = text[:idx].rstrip("\n")
    head = re.sub(r"\n*-{3,}\s*$", "", head).rstrip("\n")
    return head, parse_entries(text[idx:])


def parse_entries(section_text):
    """从补录节里解析出已有条目（保留原始 markdown，避免手改被抹掉）"""
    entries = []
    for chunk in re.split(r"(?m)^(?=" + re.escape(ENTRY_PREFIX) + r")", section_text)[1:]:
        lines = chunk.split("\n")
        topic = lines[0][len(ENTRY_PREFIX):].strip()
        topic = re.sub(r"\s*⟨[^⟩]*⟩\s*$", "", topic).strip()
        raw = chunk.rstrip("\n")
        m = re.search(r"- \*\*入库\*\*：kb-backfill (\S+)", raw)
        mi = re.search(r"(?m)^\s*[-*]?\s*\*\*知识点\s*ID\*\*\s*[：:]\s*(\S+)", raw)
        entries.append({"topic": topic, "raw": raw, "date": m.group(1) if m else "—",
                        "id": mi.group(1) if mi else None})
    return entries


def pending_mark(entry=None):
    """本条补录用的「待确认」标记：条目自带 > 环境变量 > 默认"""
    if entry and str(entry.get("pending_mark") or "").strip():
        return str(entry["pending_mark"]).strip()
    return os.environ.get("KB_PENDING_MARK") or PENDING_MARK


def render_entry(e, today):
    L = [f"{ENTRY_PREFIX}{e['topic']}  {pending_mark(e)}", ""]
    L.append(f"- **大纲位置**：{e.get('position') or '（待补）'}")
    L.append(f"- **来源**（web 检索于 {today}）：")
    for s in e["sources"]:
        L.append(f"  - {s}")
    L.append(f"- **核心结论**：{e['conclusion']}")
    if e.get("exam"):
        L.append(f"- **考法**：{e['exam']}")
    if e.get("pitfall"):
        L.append(f"- **易错点**：{e['pitfall']}")
    if e.get("related"):
        L.append("- **关联考点**：" + " · ".join(e["related"]))
    L.append(f"- **入库**：kb-backfill {today}")
    return "\n".join(L)


def stamp_id(raw, id_):
    """在条目 raw 里写入/更新「知识点ID」行（没有就在「入库」行前插入）。
    只动这一行，条目其余内容（含手改）原样保留。"""
    line = f"- **知识点ID**：{id_}"
    if re.search(r"(?m)^\s*[-*]?\s*\*\*知识点\s*ID\*\*", raw):
        return re.sub(r"(?m)^\s*[-*]?\s*\*\*知识点\s*ID\*\*.*$", lambda m: line, raw, count=1)
    # 优先放在第一个字段位（大纲位置之前），与错题库的字段风格一致
    for anchor in (r"(?m)^- \*\*大纲位置\*\*：", r"(?m)^- \*\*入库\*\*："):
        m = re.search(anchor, raw)
        if m:
            return raw[:m.start()] + line + "\n" + raw[m.start():]
    return raw + "\n" + line


def render_section(entries, id_prefix=""):
    """渲染补录节。id_prefix（如 "D4"）非空时，按标题序给每条盖上稳定 ID `D4-BF-1`。"""
    ordered = sorted(entries, key=lambda x: x["topic"])
    if id_prefix:
        for i, e in enumerate(ordered, 1):
            e["id"] = f"{id_prefix}-BF-{i}"
            e["raw"] = stamp_id(e["raw"], e["id"])
    L = [SECTION_TITLE, "", SECTION_INTRO, ""]
    if ordered:
        L.append(f"**补录条目（{len(ordered)}）**")
        L.append("")
        for e in ordered:
            tag = f" `{e['id']}`" if e.get("id") else ""
            L.append(f"- [[#{ENTRY_TITLE_PREFIX}{e['topic']}]]（{e['date']}）{tag}")
        L.append("")
    for e in ordered:
        L.append(e["raw"])
        L.append("")
    return "\n".join(L).rstrip("\n") + "\n"


def upsert(file_path, entry, today, dry_run=False):
    """写入/更新一条补录。返回 (动作, 备份路径或 None, 新全文)"""
    text, crlf, bom = read_text(file_path)
    head, entries = split_file(text)

    action = "更新" if any(e["topic"] == entry["topic"] for e in entries) else "新增"
    entries = [e for e in entries if e["topic"] != entry["topic"]]
    entries.append({"topic": entry["topic"], "raw": render_entry(entry, today), "date": today})

    new_text = head + "\n\n---\n\n" + render_section(entries, id_prefix=entry["domain"])
    if dry_run:
        return action, None, new_text
    bak = backup(file_path)
    write_text(file_path, new_text, crlf, bom)
    return action, bak, new_text


LEDGER_HEADER = """# 补录台账（外部检索入库记录）

> 由 `kb-backfill` 自动追加，只记摘要，全文见各域精讲笔记的「外部检索补录」节。
> **核对流程**：以 CRM28 为准核对 → 把条目并入正文考点 → 从补录节删除该条 → 在本表该行末尾标 ✅。

| 日期 | 科目/域 | 知识点 | 动作 | 来源数 | 结论摘要 |
|---|---|---|---|---|---|
"""


def append_ledger(project, domain, entry, today, action):
    path = os.path.join(KB_ROOT, project, LEDGER_NAME)
    summary = entry["conclusion"].replace("|", "｜").replace("\n", " ")
    if len(summary) > 40:
        summary = summary[:40] + "…"
    row = f"| {today} | {project}/{domain} | {entry['topic']} | {action} | {len(entry['sources'])} | {summary} |\n"
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(LEDGER_HEADER)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(row)
    return path


# ── 校验 ───────────────────────────────────────────────────────────────
def validate(entry):
    """返回错误信息列表（空=通过）。核心规则：无来源不入库。"""
    errs = []
    for k in ("project", "domain", "topic", "conclusion"):
        if not str(entry.get(k) or "").strip():
            errs.append(f"缺必填字段：{k}")
    domain = str(entry.get("domain") or "")
    if domain and not re.match(r"^D\d+$", domain):
        errs.append(f"domain 格式应为 D1/D2/…（收到 {domain!r}）")
    srcs = entry.get("sources")
    if not isinstance(srcs, list) or not srcs:
        errs.append("sources 必须是非空数组 —— **无来源不入库**（外部内容必须可回溯）")
    else:
        bad = [s for s in srcs if not re.match(r"^https?://", str(s))]
        if bad:
            errs.append(f"来源必须是 http(s) 链接：{bad}")
    return errs


def cmd_list(project=None):
    if not os.path.isdir(KB_ROOT):
        print(f"（知识库根目录不存在：{KB_ROOT}）", file=sys.stderr)
        return 1
    projects = [project] if project else sorted(
        d for d in os.listdir(KB_ROOT)
        if os.path.isdir(os.path.join(KB_ROOT, d, JINGJIANG))
    )
    total = 0
    for proj in projects:
        for path in sorted(glob.glob(os.path.join(KB_ROOT, proj, JINGJIANG, "D*.md"))):
            text, _, _ = read_text(path)
            _, entries = split_file(text)
            if not entries:
                continue
            rel = os.path.relpath(path, KB_ROOT).replace(os.sep, "/")
            print(f"\n## {rel}（{len(entries)} 条）")
            for e in sorted(entries, key=lambda x: x["topic"]):
                state = "待确认" if MARK_RE.search(e["raw"]) else "已并入"
                tag = f"  {e['id']}" if e.get("id") else ""
                print(f"  - {e['topic']}{tag}  [{state}]  {e['date']}")
            total += len(entries)
    if total == 0:
        print("（暂无补录条目）")
        return 0
    print(f"\n合计 {total} 条补录（台账：<科目>/_补录台账.md）")
    return 0


def cmd_write(src, project_override=None, dry_run=False):
    if src == "-":
        entry = json.load(sys.stdin)
    else:
        if not os.path.isfile(src):
            print(f"（JSON 文件不存在：{src}）", file=sys.stderr)
            return 2
        # utf-8-sig：PowerShell 的 Out-File/Set-Content 默认会写 BOM，
        # 用 "utf-8" 读会拿到 \ufeff 开头直接 JSONDecodeError（实测坑）
        with open(src, encoding="utf-8-sig") as f:
            entry = json.load(f)

    if project_override:
        entry["project"] = project_override

    errs = validate(entry)
    if errs:
        print("❌ 拒绝写入：", file=sys.stderr)
        for e in errs:
            print(f"   - {e}", file=sys.stderr)
        return 2

    today = str(entry.get("date") or datetime.date.today().isoformat())
    path = find_domain_file(entry["project"], entry["domain"])
    if not path:
        return 1

    action, bak, new_text = upsert(path, entry, today, dry_run=dry_run)
    rel = os.path.relpath(path, KB_ROOT).replace(os.sep, "/")

    if dry_run:
        print(f"[dry-run] 将{action}：{rel} ← {entry['topic']}")
        print("-" * 72)
        text, _, _ = read_text(path)
        head, entries = split_file(text)
        print(render_section([e for e in entries if e["topic"] != entry["topic"]] +
                             [{"topic": entry["topic"], "raw": render_entry(entry, today), "date": today}],
                             id_prefix=entry["domain"]))
        print("-" * 72)
        print("（dry-run：未写入任何文件）")
        return 0

    ledger = append_ledger(entry["project"], entry["domain"], entry, today, action)
    print(f"✅ {action}：{rel}")
    print(f"   知识点：{entry['topic']}")
    print(f"   来源：{len(entry['sources'])} 条 · 标记 {PENDING_MARK}")
    print(f"   备份：{os.path.relpath(bak, KB_ROOT).replace(os.sep, '/')}")
    print(f"   台账：{os.path.relpath(ledger, KB_ROOT).replace(os.sep, '/')}")
    print("   下一步：下次遇到该考点将直接命中本地，不再外搜。核对 CRM28 后并入正文并删除补录条目。")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="kb-backfill",
        description="把外部检索结论回写进精讲笔记（检索→沉淀闭环）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--from", dest="src", metavar="entry.json", help="补录内容 JSON 文件（'-' 表示读 stdin）")
    p.add_argument("--list", action="store_true", help="列出已有补录条目")
    p.add_argument("--project", help="覆盖 JSON 里的科目（也用于 --list 限定科目）")
    p.add_argument("--dry-run", action="store_true", help="只打印将写入的内容，不落盘")
    args = p.parse_args(argv)

    if args.list:
        return cmd_list(args.project)
    if not args.src:
        p.print_help()
        return 2
    try:
        return cmd_write(args.src, args.project, args.dry_run)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
