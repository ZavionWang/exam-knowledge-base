#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kb_tools.py — 覆盖率体检 / 链接体检 / 错位字修复 三件套的测试

被测：
  kb-gaps.py        覆盖率体检（🔴 待核无权威源 / 🟡 待核有权威源 / 无引证 / 自述未覆盖）
  kb-links.py       内部链接体检（同文件锚点 / 跨文件锚点 / 文件缺失）+ --fix
  kb-fix-glyphs.py  PDF 字形错位修复（字符级 + 词组级）

两个刻意的「反例」断言（都是实测踩过坑才加上的）：
  · kb-gaps：`[[#考点 X（待核）]]` 这种**双链里**的「待核」不能串味到本考点
  · kb-fix-glyphs：`婴儿 / 儿童 / 儿子 / 六西格玛 / 冲刺` 里的
    儿 / 玛 / 刺 都是合法字，绝不能被字符级映射误伤
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # pragma: no cover
    pass


def load(name, filename):
    """文件名带连字符，不能普通 import。"""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kbg = load("kbg", "kb-gaps.py")
kbl = load("kbl", "kb-links.py")
kfg = load("kfg", "kb-fix-glyphs.py")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ─────────────────────────── kb-gaps ───────────────────────────
GAPS_MD = """# D1 测试域

## 考点 1.1 有权威源但待核
正文甲
- **外部依据（权威源）**：[ISACA 标准](https://www.isaca.org/standards) 待CRM28确认
- 本地锚点：[L10-L20](教材原文/D1-测试.md#L10)

## 考点 1.2 待核但无权威源
正文乙
- 待核
- 本地锚点：[L1](教材原文/D1-测试.md#L1)

## 考点 1.3 完全没有引证
正文丙

## 考点 1.4 自述未覆盖
正文丁
- 本地教材未覆盖此点
- 本地锚点：[L2](教材原文/D1-测试.md#L2)

## 考点 1.5 双链里带待核标记
正文戊
- 关联考点：[[#考点 1.1 有权威源但待核（待核）]]
- 本地锚点：[L3](教材原文/D1-测试.md#L3)
"""


class GapsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kbt-gaps-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.proj = "P"
        self.path = pathlib.Path(self.root) / self.proj / "精讲笔记" / "D1-测试域.md"
        write(self.path, GAPS_MD)
        self._old = kbg.KB_ROOT
        kbg.KB_ROOT = self.root
        self.addCleanup(setattr, kbg, "KB_ROOT", self._old)

    def test_parse_points_splits_on_point_heading(self):
        pts = kbg.parse_points(str(self.path))
        self.assertEqual(len(pts), 5)
        self.assertEqual([p["key"] for p in pts], ["1.1", "1.2", "1.3", "1.4", "1.5"])
        self.assertEqual(pts[0]["title"], "有权威源但待核")
        self.assertEqual(pts[0]["line"], 3)

    def test_scan_grades_red_yellow_and_counts(self):
        totals, rows = kbg.scan(self.proj)
        d = totals["D1"]
        self.assertEqual(d["n"], 5)
        self.assertEqual(d["yellow"], 1, "1.1 待核 + 有权威链接 → 🟡")
        self.assertEqual(d["red"], 1, "1.2 待核 + 无权威链接 → 🔴")
        self.assertEqual(d["nocite"], 1, "只有 1.3 没有本地行号锚点")
        self.assertEqual(d["gap"], 1, "只有 1.4 自述未覆盖")

    def test_wikilink_pending_does_not_leak(self):
        """双链里的「待核」属于被引考点，不能算到本考点头上（实测踩过的假阳性）。"""
        _totals, rows = kbg.scan(self.proj)
        keys = [r["key"] for r in rows]
        self.assertNotIn("1.5", keys, "1.5 的「待核」只在 [[…]] 里，剥掉后不该被判待核")
        level_by_key = {r["key"]: r["level"] for r in rows}
        self.assertEqual(level_by_key["1.1"], "yellow")
        self.assertEqual(level_by_key["1.2"], "red")
        self.assertEqual(level_by_key["1.3"], "info", "无引证单独成行，级别 info")

    def test_main_returns_nonzero_when_red_exists(self):
        """契约：有 🔴（待核无权威源）就返回非 0，方便链路/CI 拦。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kbg.main(["--project", self.proj])
        self.assertEqual(rc, 1)
        self.assertIn("覆盖率体检", buf.getvalue())

    def test_main_returns_zero_on_clean_project(self):
        write(self.path, "# D1\n\n## 考点 1.1 干净\n- 本地锚点：[L1](教材原文/D1.md#L1)\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kbg.main(["--project", self.proj])
        self.assertEqual(rc, 0, "没有 🔴 时应返回 0")


# ─────────────────────────── kb-links ───────────────────────────
LINKS_A = """# D1 甲

## 考点 1.1 甲
- 自链：[[#考点 1.1 甲]]
- 跨文件但写成了同文件锚点：[[#考点 1.2 乙]]
- 指向不存在的文件：[[精讲笔记/D9-不存在.md#考点 9.9]]
- 补录锚点漏了标题尾部的标记：[[#补录 某某 标题]]

## 补录 某某 标题  ⟨待确认⟩
正文
"""

LINKS_B = """# D2 乙

## 考点 1.2 乙
正文
"""


class LinksTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kbt-links-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.proj = "P"
        self.a = pathlib.Path(self.root) / self.proj / "精讲笔记" / "D1-甲.md"
        self.b = pathlib.Path(self.root) / self.proj / "精讲笔记" / "D2-乙.md"
        write(self.a, LINKS_A)
        write(self.b, LINKS_B)
        self._old = kbl.KB_ROOT
        kbl.KB_ROOT = self.root
        self.addCleanup(setattr, kbl, "KB_ROOT", self._old)

    def _run(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kbl.main(["--project", self.proj, *extra])
        return rc, buf.getvalue()

    def test_detects_three_dead_links(self):
        rc, out = self._run()
        self.assertEqual(rc, 1, "有死链应返回 1")
        self.assertIn("死链 3 条", out)
        self.assertIn("文件不存在", out)
        self.assertIn("同文件无此标题", out)

    def test_fix_rewrites_cross_file_and_backfill_marker(self):
        rc, out = self._run("--fix")
        text = self.a.read_text(encoding="utf-8")
        self.assertIn("[[精讲笔记/D2-乙.md#考点 1.2 乙]]", text, "跨文件锚点应被改写成跨文件形式")
        self.assertIn("[[#补录 某某 标题  ⟨待确认⟩]]", text, "补录锚点应补上标题尾部的 ⟨…⟩")
        self.assertEqual(rc, 1, "剩下「文件不存在」仍修不了，退出码 1")

    def test_fix_leaves_legal_same_file_anchor_alone(self):
        self._run("--fix")
        text = self.a.read_text(encoding="utf-8")
        self.assertIn("[[#考点 1.1 甲]]", text, "本来就合法的同文件锚点不该被动")

    def test_clean_fixture_returns_zero(self):
        write(self.a, "# D1\n\n## 考点 1.1 甲\n- 自链：[[#考点 1.1 甲]]\n")
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("无死链", out)


# ──────────────────────── kb-fix-glyphs ────────────────────────
class GlyphFixTextTest(unittest.TestCase):
    def test_char_level_common_corruptions(self):
        self.assertEqual(kfg.fix_text("用戵觃划収现有敁亊敀备仹通迆迚行登彔")[0],
                         "用户规划发现有效事故备份通过进行登录")

    def test_char_level_batch_two(self):
        self.assertEqual(kfg.fix_text("斱案幵且陎制仸务甴话辒入包拪乊一挃向情冴")[0],
                         "方案并且限制任务电话输入包括之一指向情况")

    def test_char_level_batch_three(self):
        self.assertEqual(kfg.fix_text("安裃资栺优兇兊受角艱阷止帩助隑离枀端灱活")[0],
                         "安装资格优先免受角色阻止帮助隔离极端灵活")

    def test_phrase_level_policy_and_connect(self):
        t = kfg.fix_text("政答目标 违接网络 违续运行")[0]
        self.assertEqual(t, "政策目标 连接网络 连续运行")

    def test_phrase_level_delay_clone(self):
        t = kfg.fix_text("周转延连 数据包丢失、延连或抖动 光隆应用程序")[0]
        self.assertEqual(t, "周转延迟 数据包丢失、延迟或抖动 克隆应用程序")

    def test_phrase_level_xishi_jiangshi(self):
        t = kfg.fix_text("首帨信息官 主帨 讱师 演讱")[0]
        self.assertEqual(t, "首席信息官 主席 讲师 演讲")

    def test_legal_characters_must_not_be_touched(self):
        """反例：这些字本身合法，字符级映射一旦误伤就是写坏原文。"""
        for s in ("婴儿在需要营养", "儿童在线隐私保护法案", "儿子父亲祖父",
                  "六西格玛和精益六西格玛", "敏捷冲刺回顾", "双绞线电缆", "沙盒"):
            self.assertEqual(kfg.fix_text(s)[0], s, f"不该改动：{s}")

    def test_returns_counts(self):
        _t, stats = kfg.fix_text("用戵用戵觃")
        self.assertEqual(stats["戵→户"], 2)
        self.assertEqual(stats["觃→规"], 1)

    def test_idempotent(self):
        once = kfg.fix_text("用戵觃划有敁")[0]
        twice, stats2 = kfg.fix_text(once)
        self.assertEqual(once, twice)
        self.assertEqual(stats2, {})


class GlyphFixApplyTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kbt-glyph-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cisa = pathlib.Path(self.root) / "CISA"
        self.f = self.cisa / "教材原文" / "CRM28" / "第1章-测试.md"
        write(self.f, "用戵应当改迚政答，幵且首帨信息官要讱清楚。")
        self._old = kfg.CISA
        kfg.CISA = self.cisa
        self.addCleanup(setattr, kfg, "CISA", self._old)

    def test_dry_run_does_not_touch_file(self):
        before = self.f.read_text(encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kfg.main(["--dry-run", "--scope", "crm28"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.f.read_text(encoding="utf-8"), before)
        self.assertIn("dry-run", buf.getvalue())

    def test_apply_fixes_and_writes_backup(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kfg.main(["--apply", "--scope", "crm28"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.f.read_text(encoding="utf-8"),
                         "用户应当改进政策，并且首席信息官要讲清楚。")
        baks = list(kfg.bk_dir().glob("第1章-测试.md.*.bak"))
        self.assertEqual(len(baks), 1, "改前必须落一份备份")
        self.assertIn("用戵", baks[0].read_text(encoding="utf-8"), "备份应是修复前的原文")

    def test_apply_is_idempotent_on_second_run(self):
        with contextlib.redirect_stdout(io.StringIO()):
            kfg.main(["--apply", "--scope", "crm28"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kfg.main(["--apply", "--scope", "crm28"])
        self.assertIn("合计修复 0 处", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
