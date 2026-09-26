#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kb_search.py — kb-search.py 回归测试

跑法：
  python "C:\\Users\\Public\\kb-search\\test_kb_search.py"

覆盖 2026-09-26 那次改造的全部 5 个 P0 修复，每条都能在旧版上失败：
  1. 命中数饱和    —— 旧版一到 --max-hits 就 break，len(hits) 恒 ≤ max_hits，排序被压平
  2. snippet 失效  —— 旧版用 text.find(query) 大小写敏感，搜小写词时片段不回中
  3. 多词 OR       —— 旧版整串匹配，换种说法就搜不到
  4. 位置参数丢失  —— 旧版把第二个词静默丢弃，却照样输出「命中」（最危险的静默失效）
  5. 真实命中总数  —— 旧版表头统计的是截断后的数，不是真实数
"""
import contextlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("kb-search.py")


def _load():
    spec = importlib.util.spec_from_file_location("kb_search", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kb = _load()


# ── 测试用知识库（全 ASCII 路径，内容按需） ──────────────────────────────
FIXTURE = {
    # 定位层
    "projA/_索引.md": "索引：板块 D4 → BCP 业务连续性计划\nalpha 主题条目\n",
    "projA/思维导图.md": "导图：BCP 分支\n",
    "projA/检索协议.md": "# 检索协议（projA）\n本协议只服务 projA\n",
    # 知识点注册表（kb-id --index 生成的定位层）
    "projA/_知识点索引.md": "| `D4-B-4.10a` | 4.10a | 甲 | `x.md:1` |\n",
    # 同一层级内的饱和 / BM25 排序测试（都在 教材原文 层）
    "projA/教材原文/D9-many.md": "many\n" * 50,
    "projA/教材原文/D9-few.md": "many\n" * 3,
    # 命中分布 / 层级优先
    "projA/精讲笔记/D4-运营.md": "BCP 精讲结论\n内容\n内容\n内容\n内容\nalpha 精讲\n",
    "projA/教材原文/D4-运营.md": "BCP " * 40 + "\n",
    "projA/错题库/错题记录.md": "错题：BCP 概念混淆\n",
    # 多词 OR
    "projA/教材原文/multi-both.md": "alpha beta 同时出现\n",
    "projA/教材原文/multi-alpha.md": "alpha 单词文件\n",
    "projA/教材原文/multi-beta.md": "beta 单词文件\n",
    # 只含中文第二词（验证多词 OR 的召回）
    "projA/教材原文/only-cn.md": "业务连续性管理\n",
    # 大小写片段定位：命中点必须离行首 >70 字符，旧版从行首截才会漏掉它
    "projA/教材原文/case.md": ("前" * 100) + "BCP" + ("后" * 100) + "\n",
    # 另一个科目（验证 --project 隔离 / 跨科目告警）
    "projB/教材原文/D1.md": "beta 别的科目\ngamma\n",
}


def run(*argv):
    """以给定 argv 调用 main()，返回 (退出码, stdout)"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = kb.main(list(argv))
    return rc, buf.getvalue()


def section(out, name):
    """取出输出里某个文件的段落（以 '## 路径' 开头到下一个 '## ' 为止）"""
    for part in re.split(r"\n## ", out)[1:]:
        if name in part.split("\n")[0]:
            return part
    return ""


class KbSearchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        for rel, content in FIXTURE.items():
            p = Path(self.root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        self._old_root = kb.KB_ROOT
        kb.KB_ROOT = self.root

    def tearDown(self):
        kb.KB_ROOT = self._old_root
        self._tmp.cleanup()

    # ── P0-1 命中数不再饱和、真实总数正确 ───────────────────────────────
    def test_hit_count_not_saturated_by_max_hits(self):
        rc, out = run("many", "--project", "projA", "--max-hits", "3", "--max-files", "2")
        self.assertEqual(rc, 0)
        # 50 + 3 = 53 处真实命中；旧版会报 3+3=6
        self.assertIn("53 处", out)
        self.assertNotIn("命中「many」：6 处", out)

    def test_ranking_uses_real_count_within_tier(self):
        rc, out = run("many", "--project", "projA", "--max-hits", "3", "--max-files", "2")
        self.assertEqual(rc, 0)
        self.assertLess(out.index("D9-many.md"), out.index("D9-few.md"))

    def test_truncation_is_disclosed(self):
        rc, out = run("many", "--project", "projA", "--max-hits", "3")
        self.assertIn("另有 47 处未显示", out)

    # ── P0-2 snippet 按真实命中位置回中（大小写不敏感） ─────────────────
    def test_snippet_centers_on_case_insensitive_match(self):
        rc, out = run("bcp", "--project", "projA", "--dir", "教材原文", "--max-files", "9")
        self.assertEqual(rc, 0)
        sec = section(out, "case.md")
        self.assertTrue(sec, "未找到 case.md 的命中段落")
        # 该行 60 个「前」+ BCP + 60 个「后」；旧版会从行首截，片段里根本没 BCP
        self.assertIn("BCP", sec)

    def test_uppercase_query_also_centers(self):
        rc, out = run("BCP", "--project", "projA", "--dir", "教材原文", "--max-files", "9")
        sec = section(out, "case.md")
        self.assertIn("BCP", sec)

    # ── P0-3/P0-4 多词 OR：召回 + 排序 + 不丢词 ─────────────────────────
    def test_multitoken_recall_or_semantics(self):
        rc, out = run("alpha", "beta", "--project", "projA", "--dir", "教材原文", "--max-files", "9")
        self.assertEqual(rc, 0)
        # OR 召回：只含其中一个词的文件也要被找到
        self.assertIn("multi-alpha.md", out)
        self.assertIn("multi-beta.md", out)
        self.assertIn("multi-both.md", out)

    def test_multitoken_ranks_full_match_first(self):
        rc, out = run("alpha", "beta", "--project", "projA", "--dir", "教材原文", "--max-files", "9")
        self.assertLess(out.index("multi-both.md"), out.index("multi-alpha.md"))
        self.assertLess(out.index("multi-both.md"), out.index("multi-beta.md"))

    def test_positional_words_not_silently_dropped(self):
        """旧版最危险的 bug：argv[0] 之外的非选项词被静默丢弃，查询被截断却照样输出「命中」"""
        rc, out = run("BCP", "业务连续性", "--project", "projA")
        self.assertEqual(rc, 0)
        self.assertIn("命中「BCP 业务连续性」", out)
        # 第二词必须真的参与检索（等值 / 与 --project 组合都必须保留）
        rc2, out2 = run("业务连续性", "--project", "projA")
        self.assertEqual(rc2, 0)
        self.assertIn("only-cn.md", out2)
        self.assertIn("业务连续性", out2)

    def test_query_with_option_in_the_middle(self):
        rc, out = run("alpha", "--project", "projA", "beta", "--dir", "教材原文")
        self.assertEqual(rc, 0)
        self.assertIn("命中「alpha beta」", out)
        self.assertIn("multi-beta.md", out)

    # ── 层级优先（检索协议纪律）不被 BM25 冲掉 ─────────────────────────
    def test_jingjiang_tier_still_wins_over_hit_count(self):
        rc, out = run("BCP", "--project", "projA")
        self.assertEqual(rc, 0)
        self.assertLess(out.index("精讲笔记/D4-运营.md"), out.index("教材原文/D4-运营.md"))

    # ── 既有行为回归 ───────────────────────────────────────────────────
    def test_locate_only_scans_locate_files(self):
        rc, out = run("--locate", "BCP", "--project", "projA")
        self.assertEqual(rc, 0)
        self.assertIn("_索引.md", out)
        self.assertNotIn("教材原文", out)
        self.assertNotIn("错题库", out)

    def test_locate_scans_knowledge_point_registry(self):
        rc, out = run("--locate", "4.10a", "--project", "projA")
        self.assertEqual(rc, 0)
        self.assertIn("_知识点索引.md", out)

    def test_view_line_range(self):
        rc, out = run("--view", "projA/精讲笔记/D4-运营.md", "1-2")
        self.assertEqual(rc, 0)
        self.assertIn("BCP 精讲结论", out)
        self.assertIn("第 1-2 行", out)

    def test_project_isolation(self):
        rc, out = run("gamma", "--project", "projA")
        self.assertEqual(rc, 1)
        self.assertIn("未命中", out)
        rc2, out2 = run("gamma", "--project", "projB")
        self.assertEqual(rc2, 0)
        self.assertIn("projB/教材原文/D1.md", out2)

    def test_cross_project_warning(self):
        rc, out = run("beta")
        self.assertIn("命中多个科目项目", out)

    def test_dir_filter(self):
        rc, out = run("many", "--project", "projA", "--dir", "精讲笔记")
        self.assertEqual(rc, 1)
        rc2, out2 = run("many", "--project", "projA", "--dir", "教材原文")
        self.assertEqual(rc2, 0)
        self.assertIn("D9-many.md", out2)

    def test_context_option(self):
        rc, out = run("BCP", "--project", "projA", "--dir", "精讲笔记", "--context", "2")
        self.assertEqual(rc, 0)
        self.assertIn("| 2:", out)

    def test_missing_query_returns_2(self):
        rc, out = run("--project", "projA")
        self.assertEqual(rc, 2)

    def test_unknown_option_is_ignored(self):
        rc, out = run("many", "--bogus-flag", "--project", "projA")
        self.assertEqual(rc, 0)
        self.assertIn("53 处", out)

    def test_help_returns_0(self):
        rc, out = run("--help")
        self.assertEqual(rc, 0)
        self.assertIn("kb-search.py", out)

    def test_list_and_info(self):
        rc, out = run("--list")
        self.assertEqual(rc, 0)
        self.assertIn("教材原文", out)
        rc2, out2 = run("--info")
        self.assertEqual(rc2, 0)
        self.assertIn("projA", out2)


class UnitTest(unittest.TestCase):
    """打分与分词的纯函数行为"""

    def test_tokenize_splits_and_dedups(self):
        self.assertEqual(kb.tokenize_query("BCP  业务连续性\u3000bcp"), ["bcp", "业务连续性"])

    def test_tokenize_on_empty(self):
        self.assertEqual(kb.tokenize_query("   "), [])

    def test_bm25_idf_never_negative_for_common_term(self):
        # df > N/2 时旧式 IDF 会变负，+1 变体必须仍然为正
        self.assertGreater(kb.bm25_term(100, 90, 100, 500, 500), 0.0)

    def test_bm25_tf_saturates(self):
        a = kb.bm25_term(10, 3, 100, 1000, 1000)
        b = kb.bm25_term(100, 3, 100, 1000, 1000)
        self.assertGreater(b, a)
        self.assertLess(b / a, 10.0)  # TF 饱和：10 倍词频远小于 10 倍得分

    def test_bm25_length_normalization(self):
        short = kb.bm25_term(1, 3, 100, 100, 1000)
        long_ = kb.bm25_term(1, 3, 100, 5000, 1000)
        self.assertGreater(short, long_)

    def test_tier_of(self):
        self.assertEqual(kb.tier_of("projA/精讲笔记/x.md"), 0)
        self.assertEqual(kb.tier_of("projA/教材原文/x.md"), 1)
        self.assertEqual(kb.tier_of("projA/错题库/x.md"), 2)
        self.assertEqual(kb.tier_of("projA/_索引.md"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
