#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kb_backfill.py — kb-backfill.py 回归测试

跑法：
  python "C:\\Users\\Public\\kb-search\\test_kb_backfill.py"

重点看住三件事：
  1. **正文一个字符都不能变** —— 回写只允许在文件末尾追加「外部检索补录」节
  2. **无来源不入库** —— sources 为空/非 http(s) 必须拒绝，且不许碰文件
  3. **幂等** —— 同一知识点重复回写是「更新」，不是追加出第二条
另覆盖：换行符/BOM 保留、备份、台账、dry-run、--list。
"""
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).with_name("kb-backfill.py")


def _load():
    spec = importlib.util.spec_from_file_location("kb_backfill", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kbf = _load()

BODY = """# D4 信息系统运营与业务韧性 精讲笔记

> **大纲位置**：2024 考纲 D4

---

## 考点索引

- 4.1 IS 运营管理：[[#考点 4.1a IS 运营管理]]

---

# A 运营

## 考点 4.1a IS 运营管理
- **大纲位置**：板块 D4 → A IS运营 → IS 运营管理
- **核心结论**：管理层负全部责任。
"""

ENTRY = {
    "project": "projA",
    "domain": "D4",
    "topic": "无人值守批处理的风险",
    "conclusion": "无人值守环境必须补充补偿性控制，否则失去职责分离。",
    "sources": ["https://example.com/cisa-d4", "https://example.org/x"],
    "position": "板块 D4 → A IS运营 → 无人值守",
    "exam": "给场景判断缺哪个补偿控制",
    "pitfall": "把物理安全当成补偿控制",
    "related": ["考点 4.2a 无人值守与输入/输出控制"],
}


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = kbf.main(list(argv))
    return rc, buf.getvalue()


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.jj = self.root / "projA" / "精讲笔记"
        self.jj.mkdir(parents=True)
        self.domain_file = self.jj / "D4-信息系统运营与业务韧性.md"
        self.domain_file.write_text(BODY, encoding="utf-8")
        (self.jj / "D1-信息系统审计流程.md").write_text("# D1 笔记\n", encoding="utf-8")
        self._old = kbf.KB_ROOT
        kbf.KB_ROOT = str(self.root)
        self.entry_file = self.root / "entry.json"

    def tearDown(self):
        kbf.KB_ROOT = self._old
        self._tmp.cleanup()

    # ── 工具 ──────────────────────────────────────────────────────────
    def write_entry(self, **over):
        data = dict(ENTRY)
        data.update(over)
        self.entry_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(self.entry_file)

    def text(self):
        return self.domain_file.read_text(encoding="utf-8")

    def head(self, text=None):
        return kbf.split_file(text if text is not None else self.text())[0]

    # ── 1. 首次写入 ───────────────────────────────────────────────────
    def test_first_write_creates_section_and_entry(self):
        rc, out = run("--from", self.write_entry())
        self.assertEqual(rc, 0)
        t = self.text()
        self.assertIn(kbf.SECTION_TITLE, t)
        self.assertIn("## 补录 无人值守批处理的风险", t)
        self.assertIn("https://example.com/cisa-d4", t)
        self.assertIn(kbf.PENDING_MARK, t)
        self.assertIn("✅ 新增", out)

    def test_body_is_byte_identical(self):
        """核心断言：只允许追加，正文必须一字不差"""
        before = self.head()
        run("--from", self.write_entry())
        self.assertEqual(self.head(), before)

    def test_entry_field_rendering(self):
        run("--from", self.write_entry())
        t = self.text()
        for frag in ("- **大纲位置**：板块 D4 → A IS运营 → 无人值守",
                     "- **考法**：给场景判断缺哪个补偿控制",
                     "- **易错点**：把物理安全当成补偿控制",
                     "- **关联考点**：考点 4.2a 无人值守与输入/输出控制",
                     "- **来源**（web 检索于"):
            self.assertIn(frag, t)

    def test_section_index_lists_entry(self):
        run("--from", self.write_entry())
        # 补录节自带小目录（给人和 AI 都用）
        self.assertIn("- [[#补录 无人值守批处理的风险]]", self.text())

    # ── 2. 幂等 / 更新 ────────────────────────────────────────────────
    def test_second_write_same_topic_updates_not_duplicates(self):
        run("--from", self.write_entry())
        rc, out = run("--from", self.write_entry(conclusion="改过的结论"))
        self.assertEqual(rc, 0)
        self.assertIn("✅ 更新", out)
        t = self.text()
        self.assertEqual(t.count("## 补录 无人值守批处理的风险"), 1)
        self.assertIn("改过的结论", t)
        self.assertNotIn("否则失去职责分离", t)      # 旧结论必须被替换掉

    def test_other_topics_are_appended_and_kept_sorted(self):
        run("--from", self.write_entry(topic="B 主题"))
        run("--from", self.write_entry(topic="A 主题"))
        t = self.text()
        self.assertEqual(t.count("## 补录 "), 2)
        self.assertLess(t.index("## 补录 A 主题"), t.index("## 补录 B 主题"))

    def test_hand_edited_entry_raw_is_preserved(self):
        """已有条目（哪怕是手改过的）原样保留，不被重渲染抹掉"""
        run("--from", self.write_entry())
        t = self.text().replace("- **入库**：kb-backfill", "- 手工补了一句：\n- **入库**：kb-backfill")
        self.domain_file.write_text(t, encoding="utf-8")
        run("--from", self.write_entry(topic="另一条", conclusion="x", sources=["https://a.com"]))
        self.assertIn("手工补了一句：", self.text())

    # ── 3. 无来源不入库 ───────────────────────────────────────────────
    def test_reject_without_sources(self):
        before = self.text()
        rc, _ = run("--from", self.write_entry(sources=[]))
        self.assertEqual(rc, 2)
        self.assertEqual(self.text(), before)          # 一个字节都不能动
        self.assertFalse((self.root / "projA" / kbf.LEDGER_NAME).exists())

    def test_reject_non_http_source(self):
        before = self.text()
        rc, _ = run("--from", self.write_entry(sources=["我记得教材上写过"]))
        self.assertEqual(rc, 2)
        self.assertEqual(self.text(), before)

    def test_reject_missing_required_fields(self):
        for field in ("project", "domain", "topic", "conclusion"):
            rc, _ = run("--from", self.write_entry(**{field: ""}))
            self.assertEqual(rc, 2, f"{field} 为空时应当拒绝")

    def test_reject_bad_domain_format(self):
        rc, _ = run("--from", self.write_entry(domain="第四章"))
        self.assertEqual(rc, 2)

    # ── 4. dry-run / 备份 / 台账 ──────────────────────────────────────
    def test_dry_run_touches_nothing(self):
        before = self.text()
        rc, out = run("--from", self.write_entry(), "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run]", out)
        self.assertIn("未写入任何文件", out)
        self.assertEqual(self.text(), before)
        self.assertFalse((self.jj / kbf.BACKUP_DIRNAME).exists())
        self.assertFalse((self.root / "projA" / kbf.LEDGER_NAME).exists())

    def test_backup_written_before_modify(self):
        run("--from", self.write_entry())
        baks = list((self.jj / kbf.BACKUP_DIRNAME).glob("*.bak"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_text(encoding="utf-8"), BODY)

    def _entry_block(self, topic):
        """取某个补录条目的正文块（从它的 ## 标题到下一个 ## 为止），
        避免跟补录节顶部的索引行混在一起比。"""
        t = self.text()
        start = t.index(f"## 补录 {topic}")
        nxt = t.find("\n## ", start + 1)
        return t[start: nxt if nxt != -1 else len(t)]

    def test_backfill_entries_get_stable_ids(self):
        run("--from", self.write_entry(topic="B 主题"))
        run("--from", self.write_entry(topic="A 主题"))
        self.assertIn("D4-BF-1", self._entry_block("A 主题"))
        self.assertIn("D4-BF-2", self._entry_block("B 主题"))
        self.assertIn("`D4-BF-1`", self.text())      # 补录节索引行也带上 ID

    def test_backfill_id_is_placed_before_first_field(self):
        run("--from", self.write_entry())
        block = self._entry_block("无人值守批处理的风险")
        self.assertLess(block.index("知识点ID"), block.index("大纲位置"))

    def test_backfill_renumbers_when_entry_inserted(self):
        run("--from", self.write_entry(topic="B 主题"))     # 先来，暂时是 BF-1
        run("--from", self.write_entry(topic="A 主题"))     # 排到前面 → B 必须变 BF-2
        block = self._entry_block("B 主题")
        self.assertIn("D4-BF-2", block)
        self.assertNotIn("D4-BF-1", block)

    def test_ledger_created_and_appended(self):
        run("--from", self.write_entry())
        run("--from", self.write_entry(topic="第二条", conclusion="y", sources=["https://b.com"]))
        ledger = (self.root / "projA" / kbf.LEDGER_NAME).read_text(encoding="utf-8")
        self.assertIn("| 日期 | 科目/域 | 知识点 | 动作 | 来源数 | 结论摘要 |", ledger)
        self.assertEqual(len(re.findall(r"^\| 20\d\d-", ledger, re.M)), 2)
        self.assertIn("projA/D4", ledger)

    def test_ledger_summary_escapes_pipe(self):
        run("--from", self.write_entry(conclusion="含|竖线|的结论"))
        ledger = (self.root / "projA" / kbf.LEDGER_NAME).read_text(encoding="utf-8")
        row = [l for l in ledger.split("\n") if l.startswith("| 20")][0]
        self.assertEqual(row.count("|"), 7)   # 6 列 + 首尾，竖线不得破坏表格

    # ── 5. 编码 / 换行符 ──────────────────────────────────────────────
    def test_crlf_is_preserved(self):
        raw = BODY.replace("\n", "\r\n").encode("utf-8")
        self.domain_file.write_bytes(raw)
        run("--from", self.write_entry())
        data = self.domain_file.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_bom_is_preserved(self):
        import codecs as _c
        self.domain_file.write_bytes(_c.BOM_UTF8 + BODY.encode("utf-8"))
        run("--from", self.write_entry())
        self.assertTrue(self.domain_file.read_bytes().startswith(_c.BOM_UTF8))

    # ── 6. 定位 / 列表 ────────────────────────────────────────────────
    def test_missing_domain_file_returns_1(self):
        rc, _ = run("--from", self.write_entry(domain="D9"))
        self.assertEqual(rc, 1)

    def test_entry_json_with_bom_is_accepted(self):
        """PowerShell 的 Out-File 默认加 BOM；用 utf-8 读会 JSONDecodeError（实测坑）"""
        import codecs as _c
        self.entry_file.write_bytes(_c.BOM_UTF8 + json.dumps(ENTRY, ensure_ascii=False).encode("utf-8"))
        rc, _ = run("--from", str(self.entry_file))
        self.assertEqual(rc, 0)
        self.assertIn("## 补录 ", self.text())

    def test_missing_json_file_returns_2(self):
        rc, _ = run("--from", str(self.root / "nope.json"))
        self.assertEqual(rc, 2)

    def test_list_reports_entries(self):
        run("--from", self.write_entry())
        rc, out = run("--list")
        self.assertEqual(rc, 0)
        self.assertIn("无人值守批处理的风险", out)
        self.assertIn("[待确认]", out)
        self.assertIn("合计 1 条补录", out)

    def test_list_empty(self):
        rc, out = run("--list")
        self.assertEqual(rc, 0)
        self.assertIn("暂无补录条目", out)

    def test_no_args_prints_help(self):
        rc, out = run()
        self.assertEqual(rc, 2)
        self.assertIn("kb-backfill", out)

    # ── 7. 纯函数 ─────────────────────────────────────────────────────
    def test_split_file_without_section(self):
        head, entries = kbf.split_file(BODY)
        self.assertEqual(head, BODY.rstrip("\n"))
        self.assertEqual(entries, [])

    def test_parse_entries_strips_status_mark(self):
        t = BODY + "\n\n---\n\n" + kbf.SECTION_TITLE + "\n\n" + \
            kbf.render_entry(ENTRY, "2026-09-26") + "\n"
        _, entries = kbf.split_file(t)
        self.assertEqual(entries[0]["topic"], "无人值守批处理的风险")
        self.assertEqual(entries[0]["date"], "2026-09-26")


if __name__ == "__main__":
    unittest.main(verbosity=2)
