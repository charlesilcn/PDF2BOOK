---
name: "pdf2book"
description: "Auto-converts scanned PDF to Kindle EPUB with AI metadata/layout inference. Invoke when user asks to convert PDF to EPUB, mentions pdf2book, or wants scanned books on Kindle."
---

# PDF2BOOK — 扫描版 PDF 自动转 EPUB

将扫描版 PDF 图书一键转换为 Kindle 友好的 EPUB。页面分类、CIP 元数据提取、置信度过滤由项目自动完成；AI agent 自身承担页面分析、元数据校对、OCR 错字修正、排版参数推断等"需要理解内容"的决策。用户只需提供 PDF 路径。

## 何时使用

用户出现以下意图时触发：
- 明确要求把 PDF 转 EPUB（"把 X.pdf 转成 EPUB"、"转换这本书到 Kindle"）
- 提及 pdf2book 工具
- 想让扫描版图书在 Kindle 上可读（字体太小需要重排）

## 前置条件

- 项目已安装在 `d:\Coding\PDF2BOOK`，依赖已安装（`pip install -e ".[ocr,dev]"`）
- `pdf2book` 命令可用（或通过 `python -m pdf2book`）
- PaddleOCR PP-StructureV3 模型已下载（首次运行自动下载约 1.5GB）

## 核心原则

**Skill 路径使用 `--no-ai-review` 标志强制关闭外部 LLM 调用**。项目内置的 AI 审查（`--ai-review` 路径，现已改为 config.yaml 配 api_key 后自动启用）需要外部 LLM API key；Skill 路径由 agent 自身推理能力完成所有 AI 相关工作（页面分析、元数据校对、OCR 错字修正、排版推断），通过 Read/Grep/Edit 工具直接操作 `book.md` 和 `meta.md`，无需外部 API。

**双保险确保不触发外部 LLM**：
1. 步骤5 生成的 `config.yaml` 显式写 `ai_review: { enabled: false }`（主保险，即使根目录 config 有 api_key 也不继承）
2. 所有 `pdf2book` 命令调用加 `--no-ai-review` 标志（文档自解释，防 config 被外部污染）

## 执行流程

严格按以下 9 步执行。OCR 是最耗时阶段（可能数十分钟），其余步骤很快。

### 步骤 1：接收 PDF 路径并确认环境

1. 从用户消息中提取 PDF 文件路径（绝对路径或相对项目根目录）
2. 用 `Test-Path` 确认文件存在
3. 确认 `pdf2book` 可用：`python -m pdf2book --help`
4. 告知用户即将开始转换，OCR 阶段可能较慢

### 步骤 2：完整 OCR（自动页面分类 + CIP 提取 + 置信度过滤）

**目的**：项目已内置页面分类器、CIP 提取器、置信度过滤，一次 OCR 即可得到结构化结果。

**⚠️ OCR 完成监控机制（关键）**：OCR 耗时长（10-60 分钟），而 Shell 工具单次调用最长 10 分钟。**必须在 OCR 运行期间持续监控，绝不结束对话、不等待用户发消息**。按以下流程执行：

1. **首次调用**：用 Shell 工具前台运行 OCR 命令，设置 `timeout=600000`（10 分钟上限）
2. **若命令在 10 分钟内完成**：直接继续步骤 3
3. **若超时被推到后台**：Shell 工具会返回一个 `job-xxxx` 任务 ID。立即用 `TaskOutput` 工具阻塞等待，参数 `task_id=job-xxxx`、`block=true`、`timeout=600000`
4. **若 TaskOutput 仍超时未完成**：再次调用 `TaskOutput`（相同 task_id），重复此过程直到任务返回完成状态
5. **检测到完成后**：立即自动继续步骤 3，不结束当前对话轮次

**禁止行为**：启动 OCR 后就结束对话、回复"OCR 正在运行，完成后告诉我"、或等待用户催促。必须主动用 `TaskOutput` 轮询等待，拿到完成信号后自动推进。

```bash
python -m pdf2book ocr "<PDF路径>" --no-ai-review -v
```

**无需配置 `skip_pages`**——页面分类器自动识别封面/扉页/版权页/目录页/正文/尾页：
- 装饰页（封面/扉页/版权/插图）直接渲染为 PNG 图片引用，不 OCR
- 正文页 OCR 后进入 `book.md`
- 封面页图片自动记录，后续 EPUB 构建时用作封面
- CIP 元数据自动从版权页提取（GB/T 12451 标准），写入 `meta.md`
- 低置信度 OCR 文本标记为 `>[low-confidence] {text}` 块，供步骤 7 校对

输出（`{stem}` 为 PDF 文件名去扩展名，如 `世界神话传说.pdf` → `世界神话传说`）：
- `workspace/{stem}/book.md` — 全文 Markdown（含低置信度标记）
- `workspace/{stem}/meta.md` — CIP 提取的元数据（可能不完整，步骤 4 校对）
- `workspace/{stem}/pages/page_NNNN.png` — 每页渲染图
- `workspace/{stem}/images/pN_eM.png` — 裁剪出的插图
- `workspace/{stem}/cache.db` — SQLite 缓存

此步耗时最长，告知用户耐心等待。**OCR 期间持续用 TaskOutput 等待完成信号，不结束对话。**

### 步骤 3：AI 审查页面结构

读取 `workspace/{stem}/book.md`，重点审查**开头部分**（前 100 行）和**结尾部分**（最后 50 行），验证页面分类是否正确：

**检查项**：
- 封面/扉页/版权页是否被正确识别为图片引用（`![](pages/page_NNNN.png)`）而非 OCR 文本
- 目录页是否作为文本内容保留（标题"目录"或"CONTENTS"，H3 级别）
- 正文起始是否是第一个实际章节/故事标题
- 结尾是否有尾页/封底被正确识别

**如发现分类错误**：用 Edit 工具直接修正 `book.md`：
- 误 OCR 的装饰页文本 → 替换为 `![](pages/page_NNNN.png)`（NNNN 为页码，从 0000 开始）
- 误跳过的正文页内容 → 补回 OCR 文本
- 装饰页之间不应有 OCR 文本——封面(page 0)→扉页(page 1)→版权页(page 2)应连续为图片引用

### 步骤 4：AI 校对元数据

读取 `workspace/{stem}/meta.md`，与 `book.md` 开头的封面/版权页内容对照，校对以下字段：

| 字段 | 来源 | 校对要点 |
|---|---|---|
| `title` | CIP 提取或封面 | 书名是否完整、有无 OCR 错字 |
| `author` | CIP 提取 | 作者名是否正确 |
| `lang` | 默认 zh-CN | 根据内容文字判断 |
| `date` | CIP 提取或当前日期 | 出版日期格式 `'YYYY-MM-DD'` |
| `publisher` | CIP 提取 | 出版社名称 |

如果 CIP 提取的字段不完整或有误，用 Edit 工具修正 `meta.md`。如某些字段无法从内容提取，使用合理默认值（title → "Untitled"，author → "Unknown"），并告知用户哪些字段需要手动确认。

### 步骤 5：生成 config.yaml

在项目根目录生成 `config.yaml`（覆盖现有），基于 AI 判断的排版参数：

```yaml
work_dir: workspace
cache_db: workspace/cache.db
input_dir: inbox
output_dir: library
ocr:
  dpi: 200
  use_table_recognition: false
  use_formula_recognition: false
  use_region_detection: true
postprocess:
  drop_header_footer: true
  merge_cross_page: true
  infer_title_level: true
epub:
  toc_depth: 2    # 步骤8会更新
  chapter_level: 1  # 步骤8会更新
ai_review:
  enabled: false  # Skill 路径显式关闭，由 agent 自身完成 AI 工作
```

**注意**：不再需要 `skip_first_pages`/`skip_last_pages`，页面分类器自动处理。`ai_review.enabled: false` 是 Skill 路径的关键保险——即使根目录或环境变量里配了 api_key，也不会触发外部 LLM 调用。

### 步骤 6：重新生成 book.md（从缓存，应用最新配置）

利用步骤 2 建立的缓存，快速重新生成 book.md（只需重新跑后处理，不重新 OCR）：

```bash
python -m pdf2book ocr "<PDF路径>" --resume --config config.yaml --no-ai-review -v
```

- `--resume` 从 SQLite 缓存加载已 OCR 的页面
- 此步骤很快（秒级），因为不重新 OCR

### 步骤 7：AI 校对 book.md

读取新生成的 `workspace/{stem}/book.md`，执行以下校对：

1. **低置信度文本**：搜索 `>[low-confidence]` 标记，根据上下文修正错字后移除标记（把 `>[low-confidence] {text}` 改为普通段落 `{text}`）
2. **OCR 错别字**：识别明显的 OCR 识别错误（如"己"vs"已"、"未"vs"末"、乱码字符），直接用 Edit 工具修正
3. **标题层级**：确认 H1 用于书名、H3 用于故事/章节标题，如有错误用 Edit 调整（`#`/`##`/`###`）
4. **残留噪声**：删除页码残留、页眉残留、空白图片引用（`![](images/...)` 指向空内容）
5. **段落合并**：检查跨页段落是否被错误断开或错误合并

**校对原则**：只修正明显错误，不重写内容。如发现大量错误告知用户建议人工校对。

### 步骤 8：AI 推断排版参数

分析 book.md 的标题结构，决定 EPUB 排版参数：

**统计标题分布**：
- 用 Grep 统计 `^# `（H1）、`^## `（H2）、`^### `（H3）的数量

**决策规则**：

| 标题结构 | 书类型 | toc_depth | chapter_level | 说明 |
|---|---|---|---|---|
| 1 个 H1 + 多个 H3 | 故事集/短篇合集 | 3 | 3 | 每个故事独立成页，目录可跳转 |
| 多个 H1（第X章） | 长篇小说 | 2 | 1 | 按章分页 |
| 1 个 H1 + 多个 H2 | 分章节书籍 | 2 | 2 | 按 H2 章节分页 |
| 只有 H1 | 无章节结构 | 1 | 1 | 整本书一页 |

更新 `config.yaml` 的 `epub` 部分为推断值。

### 步骤 9：生成最终 EPUB

```bash
python -m pdf2book epub "workspace/{stem}/book.md" -o "library/{stem}.epub" --cover "workspace/{stem}/pages/page_0000.png" --config config.yaml --no-ai-review -v
```

- 封面自动使用 PDF 第一页渲染图 `page_0000.png`
- 目录自动链接化：项目内置 fallback 路径（`toc_links.py`）自动将"标题／页码"格式目录转为可点击的竖排链接列表（`::: {.toc-list}`），跳转到对应章节
- 输出路径为 `library/{stem}.epub`（文件名与 PDF 源文件同名）

完成后向用户报告：
- EPUB 文件路径和大小
- 应用的关键配置（chapter_level、toc_depth）
- 提取的元数据（书名、作者）
- 如有需要人工确认的字段，明确提示

## 关键决策逻辑

### 页面类型判断要点（验证分类器结果时参考）

- **目录页特征**：包含"目录"/"目 录"/"Contents"字样，内容为"标题......页码"格式
- **版权页特征**：包含 ISBN（形如 978-7-xxxx-xxxx-x）、"图书在版编目"、"版权所有"、出版社地址
- **封面特征**：内容极少，通常只有书名，可能是图片块（`![](images/...)`）
- **正文特征**：连续段落文本，可能有章节标题

### 元数据提取 fallback 策略

1. **优先 CIP 规则提取**（项目自动完成，最准确）—— 从版权页 OCR 文本按 GB/T 12451 标准提取
2. **AI 校对**（步骤 4）—— agent 对照封面/版权页内容校对 CIP 提取结果
3. **PDF 内嵌元数据**（项目 fallback）—— `workspace/{stem}/meta.md` 默认值来自此处，常不准
4. 都失败则用默认值并提示用户手动填写

### OCR 校对边界

- **修**：低置信度标记文本、明显错别字、乱码、标题层级错误、残留页码/页眉
- **不修**：内容歧义（无法确定原文）、风格选择、段落分割（除非明显错误）
- **告知**：如单页超过 5 处错误，提示用户该区域扫描质量差

### 目录链接化机制

项目内置双路径目录链接化：
- **AI 路径**（config.yaml 配 api_key 自动启用）：AI 生成 `::: {.toc-list}` 链接化区域
- **Fallback 路径**（Skill 路径默认）：`toc_links.py` 纯文本规则匹配"标题／页码"格式，自动生成竖排可点击链接列表

两条路径通过 `::: {.toc-list}` 哨兵实现幂等性——已链接化的目录不会被重复处理。Skill 路径下 fallback 自动生效，agent 无需手动处理目录链接化。

## 输出

最终交付物：
1. **EPUB 文件**（主交付物）— Kindle 可读，分章节，带可点击竖排目录
2. `workspace/{stem}/book.md` — 中间产物，可保留供后续校对
3. `workspace/{stem}/meta.md` — 元数据
4. `config.yaml` — 配置文件（记录了本次转换的所有参数）

## 注意事项

- **OCR 期间绝不结束对话**：步骤 2 是长时任务，必须用 `TaskOutput`（`block=true`）持续轮询后台任务直到完成，拿到完成信号后自动推进到步骤 3。禁止回复"OCR 运行中，完成后通知你"然后结束对话
- OCR 阶段（步骤 2）可能耗时 10-60 分钟（取决于书厚和 DPI），务必提前告知用户
- 步骤 6 的 `--resume` 是关键优化，避免重复 OCR
- 如步骤 2 中断，可直接用 `--resume` 续作，不需要从头开始
- 生成的 EPUB 建议用户在 Kindle 或 EPUB 阅读器中检查效果
- **所有 `pdf2book` 命令必须加 `--no-ai-review` 标志**——Skill 路径由 agent 自身完成 AI 工作，无需外部 API。config.yaml 也显式写 `ai_review.enabled: false` 双保险
