#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-links.py — CISA 知识库内部链接体检（可自动修一类死链）

查两类问题（都是「不报错但点了跳不过去」的静默毛病）：
  1. **同文件锚点死链**：`[[#考点 4.10a …]]` —— 标题写错 / 考点改名后没跟着改
  2. **跨文件链接死链**：`[[精讲笔记/D4-…md#考点 4.10a …]]` —— 文件不存在，或目标文件里没这个标题
     其中最常见的一类：**跨文件链接被写成了 `[[#考点 …]]`**——Obsidian 会当同文件锚点，必然跳空。

`--fix` 能自动修两类：
  A. 同文件锚点在本文件不存在、但在**另一个精讲笔记文件**里唯一存在 → 改写成跨文件形式
  B. `#补录 …` 锚点漏了标题尾部的 `⟨…⟩` 标记 → 用本文件真实标题补全
改不了的（真不存在/多处匹配）照旧列出，退出码 1。

用法：
  python kb-links.py --project CISA            # 只体检
  python kb-links.py --project CISA --fix      # 体检 + 自动修可修的
退出码：0 = 无死链；1 = 仍有死链（供 CI / 手工链路调用）
"""
import argparse
import os
import re
import sys

KB_ROOT = os.environ.get("KB_ROOT") or os.path.join(
    os.path.expanduser("~"), "Documents", "Obsidian Vault", "考试知识库"
)
JINGJIANG = "精讲笔记"

# Windows 控制台默认 GBK，输出 ✅/✖ 这类字符会直接抛 UnicodeEncodeError。
# 统一把 stdout 切到 UTF-8（不可用时降级为替换字符），否则工具会在「报告问题」这一步自己崩掉。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # pragma: no cover
    pass

HEAD_RE = re.compile(r"^##+\s+(.*?)\s*$")
LINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]*)?\]\]")


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def headings(path):
    out = []
    for line in read_text(path).split("\n"):
        m = HEAD_RE.match(line)
        if m:
            out.append(m.group(1).strip())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="CISA")
    ap.add_argument("--fix", action="store_true", help="自动修可修的（跨文件锚点写错 / 补录锚点缺标记）")
    args = ap.parse_args(argv)

    base = os.path.join(KB_ROOT, args.project)
    jj = os.path.join(base, JINGJIANG)
    if not os.path.isdir(jj):
        print(f"找不到精讲笔记目录：{jj}", file=sys.stderr)
        return 2

    files = sorted(os.path.join(jj, n) for n in os.listdir(jj)
                   if n.startswith("D") and n.endswith(".md"))
    heads = {f: headings(f) for f in files}
    # ⚠️ Windows 上 os.path.join(base, "精讲笔记/D5-x.md") 会产出混合分隔符
    # （`...\CISA\精讲笔记/D5-x.md`），与 os.listdir 拼出的 `...\CISA\精讲笔记\D5-x.md`
    # 字符串不相等 → 查表必然落空、把**所有**跨文件链接误报成死链。统一规范化。
    def norm(p):
        return os.path.normcase(os.path.normpath(p))
    headset = {norm(f): set(h) for f, h in heads.items()}
    others = {}                                     # 标题 -> 拥有它的文件（唯一才算）
    for f, hs in heads.items():
        for h in hs:
            others.setdefault(h, []).append(f)

    dead, fixed = [], 0
    for f in files:
        lines = read_text(f).split("\n")
        changed = False
        for idx, line in enumerate(lines):
            for m in LINK_RE.finditer(line):
                target = m.group(1).strip()
                if not target:
                    continue
                if not target.startswith("#"):
                    path, _, anchor = target.partition("#")
                    full = os.path.join(base, path if path.endswith(".md") else path + ".md")
                    if not os.path.isfile(full):
                        dead.append((f, idx + 1, target, "文件不存在"))
                    elif anchor.strip() and anchor.strip() not in headset.get(norm(full), set()):
                        dead.append((f, idx + 1, target, "目标文件无此标题"))
                    continue
                anchor = target[1:].strip()
                if anchor in headset[norm(f)]:
                    continue
                # ── 死链：先试试自动修 ────────────────────────────────
                if args.fix and anchor.startswith("补录 "):
                    cands = [h for h in heads[f] if h.startswith(anchor)]
                    if len(cands) == 1:
                        lines[idx] = lines[idx].replace(f"[[{target}]]", f"[[#{cands[0]}]]")
                        changed = True
                        fixed += 1
                        continue
                if args.fix:
                    owners = others.get(anchor, [])
                    if len(owners) == 1 and owners[0] != f:
                        rel = f"{JINGJIANG}/{os.path.basename(owners[0])}"
                        lines[idx] = lines[idx].replace(f"[[{target}]]", f"[[{rel}#{anchor}]]")
                        changed = True
                        fixed += 1
                        continue
                dead.append((f, idx + 1, target, "同文件无此标题"))
        if changed:
            tmp = f + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            os.replace(tmp, f)

    print(f"=== 链接体检：{args.project} ===")
    print(f"扫描 {len(files)} 个精讲笔记文件 · 死链 {len(dead)} 条" + (f" · 已自动修 {fixed} 条" if args.fix else ""))
    for f, lineno, target, why in dead:
        print(f"  ✖ {os.path.basename(f)}:{lineno}  [[{target}]]  ← {why}")
    if not dead:
        print("✅ 无死链")
    return 0 if not dead else 1


if __name__ == "__main__":
    sys.exit(main())
