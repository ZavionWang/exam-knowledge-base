#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-fix-glyphs.py — 修复 PDF 文本层的系统性错位字（字形映射错位）

背景：CRM28 与部分教材原文的 PDF 文本层存在系统性的「字形错位」——
一个汉字被稳定地映射成另一个（形近或同族）汉字，例如
  用户→用戵 · 规划→觃划 · 发现→収现 · 有效→有敁 · 政策→政答
后果是**静默的检索召回丢失**：搜「有效」搜不到这些文件里的内容。

处理策略分两层，因为有些错字在其他语境里是合法汉字：
  1) 字符级替换：只用于**永不合法**的生僻错字（斱/幵/陎/仸/甴/辒 …）
  2) 词组级替换：用于**本身合法**的字（违→连接/连续 · 答→政策 · 仍→从事/从而 · 儿→几个）

用法：
  python kb-fix-glyphs.py --dry-run          # 只统计，不改文件
  python kb-fix-glyphs.py --apply            # 备份后改写
  python kb-fix-glyphs.py --apply --scope crm28   # 只改 CRM28
退出码：0 = 正常；1 = 有异常
"""
import argparse
import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # pragma: no cover
    pass

KB_ROOT = os.environ.get("KB_ROOT") or os.path.join(
    os.path.expanduser("~"), "Documents", "Obsidian Vault", "考试知识库"
)
CISA = pathlib.Path(KB_ROOT) / "CISA"


def bk_dir():
    """备份目录按**使用时**的 CISA 计算——测试会替换模块级 CISA，写死在顶层会漏。"""
    return CISA / "教材原文" / ".kb-glyph-backup"

# ── 1) 字符级：错字本身在任何简体文本里都不合法 ─────────────────────────
CHAR_MAP = {
    "斱": "方", "幵": "并", "陎": "限", "仸": "任", "甴": "电", "辒": "输",
    "拪": "括", "乊": "之", "挃": "指", "冴": "况", "裃": "装", "栺": "格",
    "兇": "先", "兊": "免", "艱": "色", "阷": "阻", "帩": "帮", "隑": "隔",
    "枀": "极", "灱": "灵", "彽": "往", "剫": "副", "牉": "牌", "刜": "初",
    "彾": "性", "筓": "答", "刞": "判", "冎": "冒", "揑": "插", "庨": "庭",
    "並": "丧", "戵": "户", "觃": "规", "収": "发", "敁": "效", "亊": "事",
    "敀": "故", "仹": "份", "迆": "过", "迚": "进", "彔": "录",
    "仌": "仍",     # 仌 是「冰」的古字，现代文本不合法；实测全是「仌依赖→仍依赖」
    "帨": "席",     # 实测：首帨信息官→首席 · 主帨→主席 · 按帨位付费→按席位 · 替补帨→替补席
    "讱": "讲",     # 实测：讱座→讲座 · 讱授→讲授 · 演讱→演讲 · 讱师→讲师
}

# ── 2) 词组级：这些字本身合法，只在特定搭配里才是错位 ──────────────────
PHRASE_MAP = {
    "政答": "政策", "政答的": "政策的",
    "违接": "连接", "违续": "连续",
    "延连": "延迟",                   # 实测：「周转延连」「数据包丢失、延连或抖动」→ 延迟
    "光隆": "克隆",                   # 实测：Cloned app — 隆 本身正确，错的是「光→克」
    "仍而": "从而", "仍事": "从事", "仍业": "从业",
    "儿个": "几个", "儿乎": "几乎",
    "决答": "决策", "回筓": "回答",   # 筓→答 已在字符级，这条兜底「回筓」
    "改迚": "改进",                   # 迚→进 已覆盖，留着做校验哨兵
}

SCOPES = {
    "crm28":  [CISA / "教材原文" / "CRM28"],
    "old5":   [CISA / "教材原文"],
    "all":    [CISA / "教材原文"],
}


def iter_targets(scope):
    """返回待处理的 .md（crm28 只扫 CRM28 目录；old5/all 扫教材原文顶层 D*.md）。"""
    out = []
    if scope == "crm28":
        out += sorted((CISA / "教材原文" / "CRM28").glob("*.md"))
    else:
        out += sorted((CISA / "教材原文").glob("D*.md"))
        if scope == "all":
            out += sorted((CISA / "教材原文" / "CRM28").glob("*.md"))
    return [p for p in out if p.is_file()]


def fix_text(t):
    stats = {}
    for bad, good in PHRASE_MAP.items():
        n = t.count(bad)
        if n:
            stats[f"{bad}→{good}"] = n
            t = t.replace(bad, good)
    for bad, good in CHAR_MAP.items():
        n = t.count(bad)
        if n:
            stats[f"{bad}→{good}"] = stats.get(f"{bad}→{good}", 0) + n
            t = t.replace(bad, good)
    return t, stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scope", default="all", choices=sorted(SCOPES))
    a = ap.parse_args(argv)
    if not (a.apply or a.dry_run):
        print("请给 --dry-run 或 --apply")
        return 1

    targets = iter_targets(a.scope)
    if not targets:
        print("没找到目标文件")
        return 1
    print(f"=== 错位字修复（scope={a.scope}，{len(targets)} 个文件，{'APPLY' if a.apply else 'DRY-RUN'}）===")

    total = 0
    touched = 0
    if a.apply:
        bk_dir().mkdir(parents=True, exist_ok=True)
        import time
        ts = time.strftime("%Y%m%d-%H%M%S")
    for p in targets:
        t = p.read_text(encoding="utf-8", errors="replace")
        new, stats = fix_text(t)
        n = sum(stats.values())
        if not n:
            print(f"  [--] {p.name}：无需修复")
            continue
        touched += 1
        total += n
        top = sorted(stats.items(), key=lambda kv: -kv[1])[:6]
        print(f"  [{'FIX' if a.apply else 'DRY'}] {p.name}：{n} 处  " + " · ".join(f"{k}×{v}" for k, v in top))
        if a.apply:
            bak = bk_dir() / f"{p.name}.{ts}.bak"
            bak.write_text(t, encoding="utf-8")
            p.write_text(new, encoding="utf-8")

    print(f"\n合计修复 {total} 处，涉及 {touched} 个文件")
    if a.apply:
        print(f"备份目录：{bk_dir()}")
    else:
        print("（dry-run，未改文件；确认无误后加 --apply）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
