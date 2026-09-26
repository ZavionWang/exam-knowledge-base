#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-search.py — 考试知识库搜索工具

给 dsh / Claude / 终端使用：搜《考试知识库》里的教材原文、精讲笔记、错题库、索引。
输出 UTF-8 纯文本到 stdout，agent 走命令执行直接读，不依赖工作区文件权限。

默认知识库根：~/Documents/Obsidian Vault/考试知识库（可用环境变量 KB_ROOT 覆盖）

用法：
  kb-search "关键词"                                 全文搜索（教材+笔记+错题库+索引）
  kb-search "词A 词B"                                多词检索（OR 语义，含全词的排前面）
  kb-search "关键词" --dir 教材原文                   只在某子目录搜（教材原文/精讲笔记/错题库）
  kb-search "关键词" --project CISA                   只搜某科目项目
  kb-search --locate "关键词"                         只搜定位层：_索引/知识点索引/思维导图/标准索引/检索协议
  kb-search --view "教材原文/11-IT安全性…md" 120-140   看某文件某段
  kb-search --list                                   列出文件清单
  kb-search --info                                   知识库概览（路径/结构/规模）

选项：
  --max-files N   最多显示几个命中文件（默认 6）
  --max-hits N    每文件最多显示几处命中（默认 8；排序用真实总数，不受此限制）
  --context N     每处命中附带其后 N 行上下文（默认 0）
  --project NAME  只搜指定科目项目子目录
  --dir NAME      只搜指定子目录（教材原文/精讲笔记/错题库）

排序规则（2026-09-26 重写）：
  第一级 目录层级 —— 精讲笔记(检索加速层) > 教材原文 > 错题库 > 其他（保持"精讲优先"纪律）
  第二级 BM25 相关度 —— TF 归一化 + IDF 稀有权重，解决"高频词压过精准定位"
  位置参数中除选项外的所有词都进查询（旧版会把第二个词静默丢弃）
"""
import sys
import os
import re
import glob
import math

# 强制 UTF-8 输出，避免 Windows 下 GBK 乱码（不依赖 PYTHONIOENCODING）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

KB_ROOT = os.environ.get("KB_ROOT") or os.path.join(
    os.path.expanduser("~"), "Documents", "Obsidian Vault", "考试知识库"
)

# 定位类文件（先查它锁章节，对应检索协议）
LOCATE_NAMES = ("_索引.md", "_知识点索引.md", "思维导图.md", "IPPF索引.md", "检索协议.md")

# 搜索排除的目录/文件（避免搜到无关内容）
EXCLUDE_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
EXCLUDE_FILES = {"_README.md"}

# BM25 参数（标准取值，无需调；k1 控 TF 饱和速度，b 控长度归一化强度）
BM25_K1 = 1.2
BM25_B = 0.75

# 整串短语命中时的相对加权（多词查询才适用）：
# 旧版只能整串匹配，用户/AI 的表述习惯依赖它；这里降级为"额外加权"而非"硬门槛"，
# 既保住召回（多词 OR）又保住精度（含整串的排前）。
PHRASE_BOOST = 2.0


def kb_abspath(rel: str) -> str:
    """把知识库内相对路径转绝对路径（rel 用 / 分隔）"""
    return os.path.join(KB_ROOT, rel.replace("/", os.sep))


def kb_find(rel: str) -> str:
    """定位文件：先按相对路径精确找；找不到则递归模糊匹配文件名（唯一命中自动用）。
    兼容两种用法：--view "CISA/教材原文/xx.md" 与 --view "教材原文/xx.md"。"""
    abs_path = kb_abspath(rel)
    if os.path.isfile(abs_path):
        return abs_path
    base = os.path.basename(rel)
    if base:
        hits = glob.glob(os.path.join(KB_ROOT, "**", base), recursive=True)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            tail = rel.replace("/", os.sep)
            for h in hits:
                if h.replace("\\", "/").endswith(tail.replace("\\", "/")):
                    return h
    return abs_path


def collect_files(project=None, dirname=None, include_locate=False):
    """收集知识库内要搜的 .md 文件，返回 [(相对路径, 绝对路径)]"""
    results = []
    for abs_path in glob.glob(os.path.join(KB_ROOT, "**", "*.md"), recursive=True):
        rel = os.path.relpath(abs_path, KB_ROOT).replace(os.sep, "/")
        parts = rel.split("/")
        base = os.path.basename(abs_path)
        # 跳过排除目录
        if any(p in EXCLUDE_DIRS for p in parts[:-1]):
            continue
        if base in EXCLUDE_FILES:
            continue
        # 顶层文件（如 方法论.md）不属于任何项目
        if len(parts) < 2:
            continue
        proj = parts[0]
        if project and proj != project:
            continue
        if dirname:
            if not include_locate:
                if dirname not in parts:
                    continue
            else:
                # --locate + --dir 组合：仍只收定位文件，dir 过滤让位
                pass
        if include_locate and base not in LOCATE_NAMES:
            continue
        results.append((rel, abs_path))
    # 稳定排序：项目 → 目录 → 文件名（中文按 Unicode 码点，足够稳定）
    results.sort(key=lambda x: x[0])
    return results


def tier_of(rel: str) -> int:
    """目录层级（排序第一级）：精讲笔记是检索加速层，永远优先。
    对应《检索协议》第 3 条「精讲优先、原文兜底」。"""
    parts = rel.split("/")
    if "精讲笔记" in parts:
        return 0
    if "教材原文" in parts:
        return 1
    if "错题库" in parts:
        return 2
    return 3


def tokenize_query(query: str):
    """查询分词：按空白（含全角空格）切分，去重，小写化。
    中文不做词法分词——那是另一个量级的工程；这里只做「空格即多词」，够覆盖 AI/用户的实际输入。"""
    raw = [t for t in re.split(r"[\s\u3000]+", (query or "").strip()) if t]
    seen, out = set(), []
    for t in raw:
        tl = t.lower()
        if tl and tl not in seen:
            seen.add(tl)
            out.append(tl)
    return out


def build_pattern(tokens):
    """多词合成一个正则（OR）。长词优先，避免短词抢先匹配打断定位。"""
    ordered = sorted(tokens, key=len, reverse=True)
    return re.compile("|".join(re.escape(t) for t in ordered), re.IGNORECASE)


def bm25_term(tf: int, df: int, n_docs: int, dl: int, avgdl: float) -> float:
    """单个词的 BM25 贡献。IDF 用 +1 变体（Lucene 口径），保证 df>N/2 时不为负。"""
    if tf <= 0 or df <= 0 or n_docs <= 0 or avgdl <= 0:
        return 0.0
    idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
    denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
    return idf * (tf * (BM25_K1 + 1.0)) / denom


def snippet(line: str, start: int, width=70):
    """截取命中处附近的文本片段（start 必须是真实命中位置，调用方用 pattern.search().start()）"""
    line = line.rstrip("\n")
    if not line:
        return ""
    s = max(0, start - int(width * 0.35))
    e = min(len(line), start + width)
    head = "…" if s > 0 else ""
    tail = "…" if e < len(line) else ""
    return head + line[s:e].strip() + tail


def find_hit_lines(pattern, lines):
    """返回全部命中行号（1-based）。⚠️ 不设上限——旧版一到 max_hits 就 break，
    导致 len(hits) 恒 ≤ max_hits，所有文件在排序里被压平在同一个分数上。"""
    return [i for i, line in enumerate(lines, start=1) if pattern.search(line)]


def load_docs(files):
    """读入文件内容。返回 [{'rel','lines','text_lower','dl'}]，读失败的文件跳过并报 stderr。"""
    docs = []
    for rel, abs_path in files:
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            print(f"（读失败 {rel}: {e}）", file=sys.stderr)
            continue
        text = "".join(lines)
        docs.append({
            "rel": rel,
            "lines": lines,
            "text_lower": text.lower(),
            "dl": max(len(text), 1),
        })
    return docs


def cmd_search(query, project=None, dirname=None, max_files=6, max_hits=8, context=0):
    max_files = max(1, int(max_files))
    max_hits = max(1, int(max_hits))
    context = max(0, int(context))

    tokens = tokenize_query(query)
    if not tokens:
        print("（查询词为空）", file=sys.stderr)
        return 2
    pattern = build_pattern(tokens)
    phrase = re.sub(r"[\s\u3000]+", " ", query.strip()).lower() if len(tokens) > 1 else ""

    files = collect_files(project=project, dirname=dirname)
    if not files:
        print(f"（没有找到可搜索的文件：{KB_ROOT}）")
        return 1

    docs = load_docs(files)
    if not docs:
        print(f"（没有可读文件：{KB_ROOT}）")
        return 1

    n_docs = len(docs)
    avgdl = sum(d["dl"] for d in docs) / n_docs
    df = {t: sum(1 for d in docs if t in d["text_lower"]) for t in tokens}

    results = []
    for d in docs:
        hit_lines = find_hit_lines(pattern, d["lines"])
        if not hit_lines:
            continue
        score = 0.0
        for t in tokens:
            score += bm25_term(d["text_lower"].count(t), df.get(t, 0), n_docs, d["dl"], avgdl)
        # 含完整短语的相对加权（乘性，与 BM25 量纲无关）
        if phrase and phrase in d["text_lower"]:
            score *= PHRASE_BOOST
        results.append({
            "rel": d["rel"],
            "lines": d["lines"],
            "hit_lines": hit_lines,
            "score": score,
            "tier": tier_of(d["rel"]),
        })

    if not results:
        print(f"未命中「{query}」（已搜索 {len(docs)} 个文件）")
        return 1

    # 排序：目录层级优先（精讲>原文>错题库）→ BM25 相关度降序 → 真实命中数降序 → 路径稳定
    results.sort(key=lambda r: (r["tier"], -r["score"], -len(r["hit_lines"]), r["rel"]))

    total_hits = sum(len(r["hit_lines"]) for r in results)
    total_files = len(results)
    shown = results[:max_files]

    print(f"命中「{query}」：{total_hits} 处，分布在 {total_files} 个文件（共搜 {n_docs} 个）")
    if total_files > len(shown):
        print(f"（按相关度只显示前 {len(shown)} 个文件，用 --max-files N 调整）")
    print("=" * 72)
    for r in shown:
        print(f"\n## {r['rel']} — {len(r['hit_lines'])} 处命中 · 相关度 {r['score']:.1f}")
        for lineno in r["hit_lines"][:max_hits]:
            text = r["lines"][lineno - 1].rstrip("\n")
            m = pattern.search(text)
            print(f"  L{lineno}  {snippet(text, m.start() if m else 0)}")
            if context > 0:
                end = min(len(r["lines"]) + 1, lineno + 1 + context)
                for j in range(lineno + 1, end):
                    print(f"      | {j}: {r['lines'][j - 1].rstrip()[:90]}")
        if len(r["hit_lines"]) > max_hits:
            print(f"  …（本文件另有 {len(r['hit_lines']) - max_hits} 处未显示，用 --max-hits N 调整）")
    print("\n" + "=" * 72)
    if not project:
        projs = {r["rel"].split("/")[0] for r in results}
        if len(projs) > 1:
            print(f"⚠️ 命中多个科目项目（{', '.join(sorted(projs))}）——请先判定题目科目，用 --project 限定（如 --project CIA-Part3 或 --project CISA），避免跨科目引用！")
    print("提示：要看某文件某段原文，用 kb-search --view \"文件相对路径\" 起行-止行")
    return 0


def cmd_locate(query, project=None):
    tokens = tokenize_query(query)
    if not tokens:
        print("（查询词为空）", file=sys.stderr)
        return 2
    pattern = build_pattern(tokens)

    files = collect_files(project=project, include_locate=True)
    if not files:
        print("（没有找到定位文件 _索引/_知识点索引/思维导图/IPPF索引/检索协议）")
        return 1
    docs = load_docs(files)
    results = []
    for d in docs:
        hits = find_hit_lines(pattern, d["lines"])[:30]
        if hits:
            results.append((d["rel"], d["lines"], hits))
    if not results:
        print(f"定位文件未命中「{query}」——可能不在大纲内，或表述不同，改用全文 kb-search 再试")
        return 1
    # 命中行数多者在前（越像目录条目越该先看）
    results.sort(key=lambda x: (-len(x[2]), x[0]))
    print(f"定位「{query}」命中文件：{len(results)} 个")
    print("=" * 72)
    for rel, lines, hits in results:
        print(f"\n## {rel} — {len(hits)} 处")
        for lineno in hits:
            text = lines[lineno - 1].rstrip("\n")
            m = pattern.search(text)
            print(f"  L{lineno}  {snippet(text, m.start() if m else 0)}")
    print("\n" + "=" * 72)
    print("流程：_索引 锁定「板块→章→节」→ 打开对应章节文件（精讲优先）→ 原文兜底")
    return 0


def cmd_view(target, line_range):
    abs_path = kb_find(target)
    if not os.path.isfile(abs_path):
        print(f"（文件不存在：{target}）")
        print("可用 kb-search --list 查看知识库内文件路径；或只传文件名如 --view \"IPPF官方版2017_clean.md\"（自动模糊匹配）")
        return 1
    if line_range is None:
        print("用法：kb-search --view \"相对路径\" 起行-止行，例如：kb-search --view \"教材原文/16-财务.md\" 100-150")
        return 1
    m = re.match(r"^(\d+)-(\d+)$", line_range.strip())
    if not m:
        print(f"（行号格式错误：{line_range}，应为 起行-止行 如 100-150）")
        return 1
    a, b = int(m.group(1)), int(m.group(2))
    if a < 1 or b < a:
        print(f"（行号范围不合法：{a}-{b}）")
        return 1
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"（读失败：{e}）", file=sys.stderr)
        return 1
    if a > len(lines):
        print(f"（该文件共 {len(lines)} 行，起始行 {a} 超出范围）")
        return 1
    b = min(b, len(lines))
    pad = len(str(b))
    print(f"{target} 第 {a}-{b} 行（共 {len(lines)} 行）：")
    print("-" * 72)
    for i in range(a - 1, b):
        print(f"{i+1:>{pad}}| {lines[i].rstrip(chr(10))}")
    return 0


def cmd_list(project=None):
    files = collect_files(project=project)
    if not files:
        print("（知识库为空或路径不对）")
        print(f"KB_ROOT = {KB_ROOT}")
        return 1
    print(f"知识库文件清单（{len(files)} 个）：")
    cur_proj, cur_dir = None, None
    for rel, _ in files:
        parts = rel.split("/")
        proj = parts[0]
        if proj != cur_proj:
            cur_proj = proj
            print(f"\n[{proj}]")
        d = "/".join(parts[1:-1]) if len(parts) > 2 else "."
        if d != cur_dir:
            cur_dir = d
            print(f"  {d}/")
        print(f"    {parts[-1]}")
    return 0


def cmd_info():
    print(f"知识库根目录：{KB_ROOT}")
    if not os.path.isdir(KB_ROOT):
        print("（目录不存在！用 KB_ROOT 环境变量指向正确位置）")
        return 1
    files = collect_files()
    total_lines = 0
    print(f"可搜索 .md 文件：{len(files)} 个")
    projs = {}
    for rel, abs_path in files:
        proj = rel.split("/")[0]
        projs.setdefault(proj, []).append(abs_path)
    for proj in sorted(projs):
        n = 0
        for p in projs[proj]:
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    n += sum(1 for _ in f)
            except OSError:
                pass
        total_lines += n
        print(f"  {proj}：{len(projs[proj])} 个文件，约 {n:,} 行")
    print(f"合计约 {total_lines:,} 行")
    print("\n子目录示例：教材原文 / 精讲笔记 / 错题库；定位文件：_索引.md、IPPF索引.md、思维导图.md")
    return 0


def _to_int(s, default):
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 环境变量回退：kb-search.ps1 在 PowerShell 5.1 下中文 argv 会乱码，
    # 改走环境变量 KB_QUERY（Windows env 是 UTF-16 安全）。这里做极简分词。
    if not argv:
        env_cmd = os.environ.get("KB_QUERY")
        if env_cmd:
            tokens = re.findall(r'"(?:[^"\\]|\\.)*"|\S+', env_cmd)
            argv = [t.strip('"') for t in tokens]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    # 首参数为主命令的三种只读模式（保持原样）
    cmd0 = argv[0]
    if cmd0 == "--view":
        return cmd_view(argv[1] if len(argv) > 1 else None,
                        argv[2] if len(argv) > 2 else None)
    if cmd0 == "--list":
        project = None
        if "--project" in argv:
            i = argv.index("--project")
            project = argv[i + 1] if i + 1 < len(argv) else None
        return cmd_list(project)
    if cmd0 == "--info":
        return cmd_info()

    # 统一解析：⚠️ 非选项的参数**全部进查询词**。
    # 旧版只取 argv[0] 当查询，`kb-search "BCP 业务连续性"` 会静默丢掉第二个词、
    # 却照样输出「命中」——最危险的静默失效，已修。
    project = dirname = None
    max_files, max_hits, context = 6, 8, 0
    locate = False
    query_parts = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--project" and i + 1 < len(argv):
            project = argv[i + 1]; i += 2
        elif a == "--dir" and i + 1 < len(argv):
            dirname = argv[i + 1]; i += 2
        elif a == "--max-files" and i + 1 < len(argv):
            max_files = _to_int(argv[i + 1], 6); i += 2
        elif a == "--max-hits" and i + 1 < len(argv):
            max_hits = _to_int(argv[i + 1], 8); i += 2
        elif a == "--context" and i + 1 < len(argv):
            context = _to_int(argv[i + 1], 0); i += 2
        elif a == "--locate":
            locate = True; i += 1
        elif a == "--list":
            return cmd_list(project)
        elif a == "--info":
            return cmd_info()
        elif a.startswith("--"):
            i += 1  # 未知选项：宽容忽略（保持旧行为）
        else:
            query_parts.append(a); i += 1

    query = " ".join(query_parts)
    if not query:
        print("用法错误：缺少关键词。用 kb-search --help 查看。", file=sys.stderr)
        return 2
    if locate:
        return cmd_locate(query, project)
    return cmd_search(query, project, dirname, max_files, max_hits, context)


if __name__ == "__main__":
    sys.exit(main())
