#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kb_id.py — kb-id.py 回归测试

跑法：
  python "C:\\Users\\Public\\kb-search\\test_kb_id.py"

重点看住四件事：
  1. **注释里的模板不算记录** —— 错题库顶部有一块 <!-- 模板 -->，不剥掉会永远报假问题（实测踩过）
  2. **注册表以精讲笔记为唯一权威** —— 域 / A-B 分组 / 考点号全部从笔记里抽，不靠人填
  3. **未声明 A/B 的域要能工作** —— 实测 D5 的笔记就没有 `# A`/`# B` 小节，ID 得允许省略组段
  4. **同义异写要被抓出来** —— `V-9.5` 与 `V-09.5` 这类零填充不一致，是统计漂移的根源
"""
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).with_name("kb-id.py")


def _load():
    spec = importlib.util.spec_from_file_location("kb_id", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kbid = _load()

D1 = """# D1 测试域 精讲笔记

# A 规划

## 考点 1.1a 甲考点
- **核心结论**：甲

## 考点 1.1b 乙考点
- **核心结论**：乙

# B 执行

## 考点 1.2 丙考点
- **核心结论**：丙
"""

D5 = """# D5 测试域 精讲笔记

## 考点 5.1 丁考点
- **核心结论**：丁
"""

# 域里带一条「外部检索补录」条目，且条目自带 kb-backfill 盖的 ID
D2 = """# D2 测试域 精讲笔记

# A 治理

## 考点 2.1 戊考点
- **核心结论**：戊

---

# 外部检索补录（待 CRM28 确认）

> 说明

**补录条目（1）**

- [[#补录 某未覆盖考点]]（2026-09-26） `D2-BF-1`

## 补录 某未覆盖考点  ⟨待 CRM28 确认⟩

- **大纲位置**：板块 D2 → A 治理
- **来源**（web 检索于 2026-09-26）：
  - https://example.com/x
- **核心结论**：某结论
- **知识点ID**：D2-BF-1
- **入库**：kb-backfill 2026-09-26
"""

RECORDS = """# 错题记录

> 说明

<!-- 模板（新题复制此块）：
## 错题 YYYY-MM-DD-NNN — 主题
- **知识点ID**：D1-A 甲考点
-->

## 错题 2026-09-26-001 — 甲
- **知识点ID**：D1-A-1.1a 甲考点

## 错题 2026-09-26-002 — 未登记考点号
- **知识点ID**：D1-A-9.9 不存在

## 错题 2026-09-26-003 — 格式不合规
- **知识点ID**：D1-A 甲考点

## 错题 2026-09-26-004 — 标准类
- **知识点ID**：ISACA-1205 证据

## 错题 2026-09-26-005 — 零填充 A
- **知识点ID**：V-9.5 群体发展

## 错题 2026-09-26-006 — 零填充 B
- **知识点ID**：V-09.5 群体发展

## 错题 2026-09-26-007 — 混入标点
- **知识点ID**：D1-A-1.1b（乙考点）

## 错题 2026-09-26-008 — 无 ID 字段
- **复盘**：忘了写 ID
"""


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = kbid.main(list(argv))
    return rc, buf.getvalue()


class KbIdTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        jj = self.root / "projX" / "精讲笔记"
        ct = self.root / "projX" / "错题库"
        jj.mkdir(parents=True)
        ct.mkdir(parents=True)
        (jj / "D1-测试域.md").write_text(D1, encoding="utf-8")
        (jj / "D2-补录域.md").write_text(D2, encoding="utf-8")
        (jj / "D5-测试域.md").write_text(D5, encoding="utf-8")
        self.records = ct / "错题记录.md"
        self.records.write_text(RECORDS, encoding="utf-8")
        self._old = kbid.KB_ROOT
        kbid.KB_ROOT = str(self.root)

    def tearDown(self):
        kbid.KB_ROOT = self._old
        self._tmp.cleanup()

    # ── 1. 注册表来自精讲笔记 ─────────────────────────────────────────
    def test_registry_scans_points_and_groups(self):
        reg = kbid.build_registry("projX")
        ids = sorted(p["id"] for p in reg["points"])
        self.assertEqual(ids, ["D1-A-1.1a", "D1-A-1.1b", "D1-B-1.2", "D2-A-2.1", "D5-5.1"])

    def test_domain_without_ab_sections_omits_group(self):
        reg = kbid.build_registry("projX")
        self.assertIn("D5-5.1", reg["by_id"])
        self.assertTrue(any("D5" in n for n in reg["notes"]), "应提示 D5 未声明 A/B 小节")

    def test_backfill_entry_uses_stamped_id(self):
        reg = kbid.build_registry("projX")
        self.assertEqual([b["id"] for b in reg["backfills"]], ["D2-BF-1"])
        self.assertIn("D2-BF-1", reg["by_id"])

    def test_point_anchor_has_real_line_number(self):
        reg = kbid.build_registry("projX")
        p = reg["by_id"]["D1-A-1.1a"]
        self.assertEqual(p["file"], "精讲笔记/D1-测试域.md")
        self.assertEqual(p["line"], 5)
        self.assertEqual(p["title"], "甲考点")

    # ── 2. 注释必须被剥离 ─────────────────────────────────────────────
    def test_commented_template_is_not_counted(self):
        recs = kbid.parse_records(str(self.records))
        self.assertEqual(len(recs), 8)                      # 不是 9；注释里的模板不算
        self.assertNotIn("YYYY-MM-DD-NNN", [r["no"] for r in recs])

    def test_strip_comments_keeps_line_numbers(self):
        text = "a\n<!--\nx\ny\n-->\nb\n"
        out = kbid.strip_comments(text)
        self.assertEqual(len(out.split("\n")), len(text.split("\n")))
        self.assertEqual(out.split("\n")[5], "b")

    # ── 3. 分类 ───────────────────────────────────────────────────────
    def test_classify_matrix(self):
        reg = kbid.build_registry("projX")
        cases = {
            "D1-A-1.1a": ("ok", ""),
            "D5-5.1": ("ok", ""),
            "D2-BF-1": ("ok", ""),
            "ISACA-1205": ("std", ""),
            "IPPF-2010": ("std", ""),
            "D1-A-9.9": ("warn", "未登记"),
            "D1-A": ("bad", "格式不合规"),
            "D3-B-1.1a": ("warn", None),      # 格式对但 D3 未登记
            "V-9.5": ("legacy", ""),
            "V-8.*": ("bad", "通配符"),
            "VI-13/11": ("legacy", "多个章节"),
            "III": ("legacy", "缺章节号"),
            "D1-A-1.1b（乙）": ("bad", "含标注"),
            "III/风险管理（保险部门有效管理）": ("legacy", "名称与键未用空格分隔"),
            "乱写的": ("bad", "格式不合规"),
        }
        for token, (level, frag) in cases.items():
            got_level, got_why = kbid.classify(token, reg)
            self.assertEqual(got_level, level, f"{token} 等级错：{got_level}/{got_why}")
            if frag:
                self.assertIn(frag, got_why, f"{token} 说明不含「{frag}」：{got_why}")

    def test_split_id(self):
        self.assertEqual(kbid.split_id("D1-A-1.1a 甲考点"), ("D1-A-1.1a", "甲考点"))
        self.assertEqual(kbid.split_id("ISACA-1205"), ("ISACA-1205", ""))
        self.assertEqual(kbid.split_id("  "), ("", ""))

    # ── 4. check 报告 ─────────────────────────────────────────────────
    def test_check_counts_and_exit_code(self):
        rc, out = run("--check", "--project", "projX")
        self.assertEqual(rc, 1)
        self.assertIn("错题 8 条", out)
        self.assertIn("规范 1", out)
        self.assertIn("标准类 1", out)
        self.assertIn("归档旧格式 2", out)
        self.assertIn("不合规 3", out)          # D1-A / 混入标点 / 整条没写 ID
        self.assertIn("整条错题没有「知识点ID」字段", out)

    def test_check_detects_zero_padding_synonyms(self):
        rc, out = run("--check", "--project", "projX")
        self.assertIn("同一知识点的多种写法", out)
        self.assertIn("V-09.5", out)
        self.assertIn("写法不一致（实为同一考点）", out)

    def test_check_reports_unregistered_and_bad_lines(self):
        rc, out = run("--check", "--project", "projX")
        self.assertIn("D1-A-9.9", out)
        self.assertIn("未登记", out)
        recs = kbid.parse_records(str(self.records))
        target = [r for r in recs if r["no"] == "2026-09-26-003"][0]
        self.assertIn(f"L{target['raw_lineno']}", out, "报的行号必须是 ID 那一行")

    def test_check_clean_kb_exits_0(self):
        self.records.write_text(
            "# 错题记录\n\n## 错题 2026-09-26-001 — 甲\n- **知识点ID**：D1-A-1.1a 甲考点\n",
            encoding="utf-8")
        rc, out = run("--check", "--project", "projX")
        self.assertEqual(rc, 0)
        self.assertIn("✅ 全部合规", out)

    def test_check_missing_file_returns_1(self):
        rc, _ = run("--check", "--project", "无此科目")
        self.assertEqual(rc, 1)

    # ── 5. index / resolve / list ─────────────────────────────────────
    def test_index_writes_registry(self):
        dest = self.root / "out" / "_知识点索引.md"
        dest.parent.mkdir()
        rc, out = run("--index", "--project", "projX", "--out", str(dest))
        self.assertEqual(rc, 0)
        t = dest.read_text(encoding="utf-8")
        self.assertIn("由 `kb-id --index` 自动生成", t)
        self.assertIn("| `D1-A-1.1a` | 1.1a | 甲考点 | `精讲笔记/D1-测试域.md:5` |", t)
        self.assertIn("| `D5-5.1` | 5.1 | 丁考点 |", t)
        self.assertIn("| `D2-BF-1` | 某未覆盖考点 |", t)
        self.assertIn("考点 5 个", out)

    def test_index_is_idempotent(self):
        dest = self.root / "_知识点索引.md"
        run("--index", "--project", "projX", "--out", str(dest))
        first = dest.read_text(encoding="utf-8").split("生成时间")[0]
        run("--index", "--project", "projX", "--out", str(dest))
        second = dest.read_text(encoding="utf-8").split("生成时间")[0]
        self.assertEqual(first, second)

    def test_resolve_full_id(self):
        rc, out = run("--resolve", "D1-A-1.1a", "--project", "projX")
        self.assertEqual(rc, 0)
        self.assertIn("甲考点", out)
        self.assertIn("精讲笔记/D1-测试域.md:5", out)

    def test_resolve_bare_key(self):
        rc, out = run("--resolve", "1.1a", "--project", "projX")
        self.assertEqual(rc, 0)
        self.assertIn("D1-A-1.1a", out)

    def test_resolve_unknown_returns_1(self):
        rc, _ = run("--resolve", "D9-Z-9.9", "--project", "projX")
        self.assertEqual(rc, 1)

    def test_list_prints_all_ids(self):
        rc, out = run("--list", "--project", "projX")
        self.assertEqual(rc, 0)
        for i in ("D1-A-1.1a", "D5-5.1", "D2-BF-1"):
            self.assertIn(i, out)

    def test_no_args_prints_help(self):
        rc, out = run()
        self.assertEqual(rc, 2)
        self.assertIn("kb-id", out)

    # ── 6. 归一化纯函数 ───────────────────────────────────────────────
    def test_norm_name(self):
        self.assertEqual(kbid.norm_name("群体发展的阶段（Tuckman）"), "群体发展的阶段")
        self.assertEqual(kbid.norm_name("IT 控制框架"), kbid.norm_name("IT控制框架"))

    def test_norm_token_strips_leading_zeros(self):
        self.assertEqual(kbid.norm_token("V-09.5"), kbid.norm_token("V-9.5"))
        self.assertNotEqual(kbid.norm_token("V-9.5"), kbid.norm_token("V-10.5"))

    def test_natural_key_sorts_numbers_not_strings(self):
        keys = ["1.10a", "1.2", "1.1a", "1.11"]
        self.assertEqual(sorted(keys, key=kbid.natural_key), ["1.1a", "1.2", "1.10a", "1.11"])

    # ── 7. ECO 式键（2026-10-16 起新增考点用 `D{域}-{A|B}-{知识点序号}`）─────
    def test_natural_key_mixes_eco_and_legacy_keys(self):
        """ECO 式键与教材式键同场排序不得抛 TypeError（旧实现首元素 str vs int 会炸）。"""
        keys = ["A-4", "4.1a", "1.10a", "7", "BF-2"]
        try:
            out = sorted(keys, key=kbid.natural_key)
        except TypeError as e:            # pragma: no cover
            self.fail(f"natural_key 混合键比较抛异常：{e}")
        self.assertEqual(len(out), len(keys))
        # 数字开头的排在文本开头的前面（(0,n) < (1,str)），且顺序稳定
        self.assertEqual(out[0], "1.10a")

    def test_eco_form_id_is_not_double_prefixed(self):
        """`## 考点 D1-A-E2 xx` 的 ID 必须是 `D1-A-E2`，不能自我叠加成 `D1-A-D1-A-E2`。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td, "CISA", "精讲笔记")
            base.mkdir(parents=True)
            (base / "D1-信息系统审计流程.md").write_text(
                "# A 规划\n\n## 考点 D1-A-E2 审计类型、评估和审查\n- **核心结论**：x\n"
                "\n# B 执行\n\n## 考点 1.5a 审计项目管理\n- **核心结论**：y\n",
                encoding="utf-8")
            old_root = kbid.KB_ROOT
            try:
                kbid.KB_ROOT = td
                points, _backfills, _notes = kbid.scan_notes("CISA")
            finally:
                kbid.KB_ROOT = old_root
        ids = [p["id"] for p in points]
        self.assertIn("D1-A-E2", ids)
        self.assertIn("D1-B-1.5a", ids)          # 既有教材式键仍走原路径
        self.assertNotIn("D1-A-D1-A-E2", ids)
        # token 保留标题原样，供 Obsidian 锚点拼 `#考点 D1-A-E2 …`
        self.assertEqual([p["token"] for p in points if p["id"] == "D1-A-E2"], ["D1-A-E2"])

    def test_classify_accepts_eco_form(self):
        reg = {"by_id": {"D1-A-E2": {"id": "D1-A-E2"}}, "by_key": {}}
        level, msg = kbid.classify("D1-A-E2", reg)
        self.assertEqual(level, "ok", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
