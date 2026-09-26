# tools/ · 配套命令行工具

> 本项目的**检索与治理工具**。协议（`检索协议-模板.md`）规定「怎么讲题」，这三个工具负责「怎么把知识点找出来、怎么让知识库越用越厚」。

纯 Python 3 标准库，**零依赖、零联网、离线可用**。Windows / macOS / Linux 通用。

## 三个工具

| 工具 | 一句话 | 解决的问题 |
|---|---|---|
| `kb-search.py` | 全库检索 + 定位 | 不靠 AI 猜，用命令把考点精确找出来 |
| `kb-backfill.py` | 外部检索结论**回写**进精讲笔记 | 查过的内容不再蒸发，下次直接命中本地 |
| `kb-id.py` | **知识点 ID 注册表**与校验 | 错题统计不再靠人手算，ID 可机器解析 |

### kb-search.py — 检索

```bash
python tools/kb-search.py "BCP" --project CIA-Part3          # 全文检索
python tools/kb-search.py "BCP 业务连续性" --project CIA-Part3  # 多词：空格分隔，OR 语义
python tools/kb-search.py --locate "重要性" --project CIA-Part3  # 只在索引层定位章节
python tools/kb-search.py --view "精讲笔记/01-治理.md" 1-20      # 看某文件某段
python tools/kb-search.py --info                              # 知识库概览
```

- **排序 = 目录层级（精讲笔记 > 教材原文 > 错题库）→ BM25 相关度**：先守「精讲优先」的协议纪律，再按 TF/IDF 排精
- **多词用空格分开**（OR 召回，含全部词的排前面）——不要把一整句话当查询词
- 表头给**真实命中总数**（不再被显示上限截断），超出显示上限会明说「另有 N 处未显示」
- 知识库根目录默认 `~/Documents/Obsidian Vault/考试知识库`，用环境变量 `KB_ROOT` 覆盖

### kb-backfill.py — 把外部检索的结论写回知识库

考纲新增内容（AI、区块链、远程办公风险…）本地教材没覆盖时，讲题会走外部检索。答完把结论落回精讲笔记，**下一次就直接命中本地**：

```bash
python tools/kb-backfill.py --from entry.json     # 写入/更新一条补录
python tools/kb-backfill.py --from entry.json --dry-run
python tools/kb-backfill.py --list
```

`entry.json` 字段（`project` / `domain` / `topic` / `conclusion` / `sources` 必填）：

```json
{
  "project": "CISA",
  "domain": "D4",
  "topic": "无人值守批处理的风险",
  "conclusion": "无人值守环境必须补充补偿性控制，否则失去职责分离。",
  "sources": ["https://example.com/..."],
  "position": "板块 D4 → A IS运营 → 无人值守",
  "exam": "给场景判断缺哪个补偿控制",
  "pitfall": "把物理安全当成补偿控制"
}
```

**三条硬约束**（都是为了让外部内容可复核、可撤销）：

1. **无来源不入库** —— `sources` 为空或不是 http(s) 链接直接拒绝，且不碰文件
2. **只追加不覆写正文** —— 补录只落在域文件末尾的「外部检索补录」节，正文一字不动；每次写入前自动备份到 `.kb-backfill/`
3. **条目自带状态与出处** —— 每条标 `⟨待官方教材确认⟩` + 来源链接 + 入库日期，台账 `_补录台账.md` 记一行摘要

> 待确认标记可自定义：`entry.json` 里加 `"pending_mark": "⟨待 CRM28 确认⟩"`（科目专用手册），
> 或设环境变量 `KB_PENDING_MARK`。默认是通用的 `⟨待官方教材确认⟩`。

同 `topic` 重复写入 = **更新**（幂等），不会追加出第二条。

### kb-id.py — 知识点 ID 规范化

```bash
python tools/kb-id.py --index --project CIA-Part3        # 生成 _知识点索引.md（ID 注册表）
python tools/kb-id.py --check --project CIA-Part3        # 校验错题库里的 ID（有 ❌ 时退出码 1）
python tools/kb-id.py --resolve 4.10a --project CISA     # ID 或裸考点号 → 精讲笔记锚点
```

**ID 语法：第一个空格前是机器键，其后是人读名称**（把「可解析的键」和「给人看的名字」分开）：

```
D4-B-4.10a BCP 与 DRP 的关系      # 业务知识类：域-任务组-考点号
D5-5.1 虚拟化与云安全              # 域笔记未声明 A/B 小节时省略组段
ISACA-1205 证据                    # 标准/准则类
D4-BF-1 无人值守补偿性控制          # 外部检索补录的未覆盖考点
```

考点号全部从 `精讲笔记/` 里抽取——**笔记是唯一权威来源**，不用手维护清单。

`--check` 会抓出四类问题：格式不合规、ID 未登记、**同一知识点的多种写法**（如 `V-9.5` 与 `V-09.5` 并存——这类会让统计直接劈叉）、同一 ID 挂多个名称。

## 测试

```bash
python tools/test_kb_search.py
python tools/test_kb_backfill.py
python tools/test_kb_id.py
```

共 **78 项断言**，覆盖：排序不被显示上限压平、片段定位大小写不敏感、多词不丢词、正文回写必须字节不变、无来源必须拒绝、幂等、注释里的模板不算记录、同义写法检测。

## 安装（可选）

工具不装也能用（`python tools/kb-search.py ...`）。想全局调用就把它放到 PATH 里，或做一个包装脚本：

```bash
# Windows PowerShell（存成 kb-search.ps1 放进 PATH）
#   PowerShell 5.1 会把中文参数传坏，所以走环境变量
$env:KB_QUERY = $args -join ' '
python <tools路径>\kb-search.py
```

## 说明

- 这份 `tools/` 是作者工作副本的**发布镜像**；改动请以你本机实际使用的副本为准
- 检索的默认根目录是本项目约定的 `~/Documents/Obsidian Vault/考试知识库`；套用别的目录时设 `KB_ROOT`
