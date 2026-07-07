# PDF2BOOK

> 让 AI 当你的电子书排版师 —— 把扫描版 PDF 一句话转成 Kindle 友好的 EPUB

[English](README_EN.md) | 中文

PDF2BOOK 是一个 AI 驱动的自动排版工具：AI 不是简单的 OCR 调用者，而是像一位编辑一样完成所有需要"理解内容"的决策——判断页面类型、提取元数据、校对 OCR 错字、推断章节结构，最终生成带目录、分章节、Kindle 优化的 EPUB。

## 核心亮点：AI 是决策者，不是工具调用者

传统转换工具只会"机械搬运"，PDF2BOOK 让 AI 承担 4 项编辑决策：

| AI 决策 | 做什么 | 为什么需要 AI |
|---|---|---|
| **页面类型识别** | 审查 OCR 结果，判断封面/版权/目录/正文/尾页 | 需要理解页面内容语义，规则无法穷举 |
| **元数据自动提取** | 从版权页提取书名、作者、ISBN、语言 | 扫描书 PDF 内嵌元数据通常缺失 |
| **OCR 智能校对** | 修正错别字、调整标题层级、清理噪声 | OCR 对中文标点/生僻字易出错 |
| **排版参数推断** | 统计标题分布，判断故事集 vs 长篇小说 | 不同书类型需要不同分章粒度 |

**Trae Skill 一句话触发**：在 Trae 中说「把 XX.pdf 转成 EPUB」，AI 自动完成 9 步决策链（OCR → 分析页面 → 提取元数据 → 校对 → 推断排版 → 生成 EPUB）。详见 [`.trae/skills/pdf2book/SKILL.md`](.trae/skills/pdf2book/SKILL.md)。

## 功能特性

- **OCR 识别** — 基于 PaddleOCR PP-StructureV3，识别文本、标题、图片、表格等版面元素
- **多 OCR 后端** — 支持 `paddle_pp`（CPU 默认）、`rapid_ocr`（轻量）、`paddle_vl`（GPU 高质量）、`cloud_ocr`（远程 API）
- **页眉页脚去除** — 自动检测并去除重复出现的页眉、页码、running head
- **跨页段落合并** — 正确处理 CJK 标点，避免段尾/段首错误的空格
- **标题层级推断** — 基于字号和章节模式（第X章 / Chapter N）推断 H1–H3 层级
- **图片裁剪提取** — 按 OCR bbox 从渲染页面裁剪插图，保存为独立 PNG 引用
- **自动页面分类** — 规则化识别封面/扉页/版权页/目录页/正文/尾页，装饰页直接用 PDF 渲染图，正文页 OCR
- **CIP 元数据提取** — 基于 GB/T 12451 标准从版权页 OCR 文本提取书名、作者、ISBN、出版社
- **置信度三级标记** — 基于 OCR 识别置信度，文本分为 normal / low-confidence / dropped 三级，低置信度文本保留并标记供校对
- **AI 审查流水线** — `--ai-review` 启用 LLM 校对低置信度文本、修正标题、提取元数据、验证书本结构
- **目录自动链接化** — 自动将"标题／页码"格式目录转为可点击的竖排链接列表，跳转到对应章节
- **批量处理** — `batch` 子命令并行转换目录下所有 PDF，每个 PDF 独立工作目录和缓存
- **断点续作** — SQLite 缓存 OCR 结果，`--resume` 跳过已完成的页面
- **Kindle 优化排版** — 内置 `kindle.css`，`chapter_level` 控制分章粒度，每个故事/章节独立成页

## 安装指南

### 系统要求

- Python ≥ 3.10
- Pandoc（由 `pypandoc_binary` 自动捆绑，无需单独安装）

### 安装步骤

```bash
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"
```

> **注意**：`paddlepaddle` 体积较大（约 1.5GB 模型 + 依赖）。国内用户可使用镜像源加速：
> ```bash
> pip install -e ".[ocr,dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

### 可选依赖

| Extras | 说明 | 安装命令 |
|---|---|---|
| `ocr` | PaddleOCR PP-StructureV3（默认 OCR 后端） | `pip install -e ".[ocr]"` |
| `rapid` | RapidOCR 轻量后端（约 50MB） | `pip install -e ".[rapid]"` |
| `cloud` | 远程 OCR API 后端 | `pip install -e ".[cloud]"` |
| `dev` | 测试与代码检查工具 | `pip install -e ".[dev]"` |

## 使用方法

### 准备工作

1. 准备一本扫描版 PDF 图书（如 `世界神话传说.pdf`）
2. （可选）创建配置文件 `config.yaml` 调整 OCR 和排版参数，参考 [配置](#配置) 一节
3. 确认已安装依赖：`pip install -e ".[ocr,dev]"`

### 两阶段工作流（推荐）

先生成可预览的 Markdown，编辑确认后再构建 EPUB。适合需要人工校对 OCR 结果的场景。

**阶段 1：PDF → OCR → Markdown**

```bash
pdf2book ocr 世界神话传说.pdf --config config.yaml
```

运行后在工作目录（默认 `.pdf2book/`）生成：

| 文件 | 说明 |
|---|---|
| `.pdf2book/book.md` | OCR 识别的全文 Markdown，可编辑修正 |
| `.pdf2book/meta.md` | 元数据 YAML（书名、作者、语言等） |
| `.pdf2book/pages/page_NNNN.png` | 每页渲染图（可用作封面） |
| `.pdf2book/images/pN_eM.png` | 裁剪出的插图 |
| `.pdf2book/cache.db` | SQLite 缓存，支持断点续作 |

**编辑中间产物（可选但推荐）**

OCR 结果可能有少量错误，建议人工校对后再构建 EPUB：

- 编辑 `book.md`：修正错字、调整标题层级（`#`/`##`/`###`）、删除无关内容
- 编辑 `meta.md`：填写正确的书名和作者。格式如下：

```yaml
---
title: 世界神话传说
author: 徐晨
lang: zh-CN
date: '2026-07-06'
---
```

**阶段 2：Markdown → EPUB**

```bash
pdf2book epub .pdf2book/book.md -o 世界神话传说.epub \
    --cover .pdf2book/pages/page_0000.png
```

`--cover` 指定封面图片，推荐用 PDF 第一页的渲染图（`page_0000.png`）。EPUB 会按 `chapter_level` 自动分章，目录按 `toc_depth` 生成。

### 一键模式

不需要预览中间结果，一次完成 PDF → EPUB 转换：

```bash
pdf2book convert 世界神话传说.pdf -o 世界神话传说.epub \
    --cover .pdf2book/pages/page_0000.png \
    --config config.yaml
```

### 批量处理

转换目录下所有 PDF：

```bash
pdf2book batch ./pdfs/ -o ./epubs/ --workers 2 --config config.yaml
```

每个 PDF 获得独立的 `work_dir/{stem}/` 子目录和 SQLite 缓存。RapidOCR 约 50MB/进程（可高并发）；PaddlePP 约 1.5GB/进程（建议 `--workers 1-2`）。

### 断点续作

OCR 是最耗时的阶段。如果中途中断，可用 `--resume` 从缓存恢复，跳过已完成的页面：

```bash
pdf2book ocr 世界神话传说.pdf --resume --config config.yaml
```

### 启用 AI 校对

配置 `config.yaml` 中的 `ai_review` 部分，然后使用 `--ai-review` 标志：

```yaml
ai_review:
  enabled: true
  api_url: "https://api.openai.com/v1/chat/completions"
  api_key: "your-api-key"
  model: "gpt-4o-mini"
```

```bash
pdf2book convert 世界神话传说.pdf -o out.epub --ai-review --config config.yaml
```

AI 审查会：校对低置信度 OCR 文本、修正乱码标题、补充元数据、验证章节结构、链接化目录。

### 常见场景

**场景 1：故事集 / 短篇合集**

每个故事是 H3 标题，希望每个故事独立成页、目录可跳转：

```yaml
epub:
  toc_depth: 3        # 目录显示到故事标题
  chapter_level: 3    # 每个 H3 故事独立分页
```

**场景 2：扫描书有封面/版权/目录页**

页面分类器自动识别封面、扉页、版权页、目录页等装饰页面，装饰页直接使用 PDF 渲染图（不 OCR），正文页才进行 OCR 和内容提取。无需手动配置 `skip_pages`。

**场景 3：长篇小说按章节分页**

章节是 H1（`第X章`），希望每章独立成页：

```yaml
epub:
  toc_depth: 2        # 目录显示章标题
  chapter_level: 1    # 每个 H1 章节分页
```

## CLI 参考

四个子命令，通过 `pdf2book <subcommand>` 调用（也支持 `python -m pdf2book`）：

### `pdf2book ocr` — 阶段 1：PDF → Markdown

```
pdf2book ocr PDF [OPTIONS]

Options:
  --resume         从缓存恢复，跳过已 OCR 的页面
  --config PATH    配置 YAML 路径（默认使用内置配置）
  --backend NAME   OCR 后端：paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --ai-review      启用 AI 审查（低置信度校对 + 元数据提取 + 标题修正）
  -v, --verbose    启用 DEBUG 日志
```

### `pdf2book epub` — 阶段 2：Markdown → EPUB

```
pdf2book epub MARKDOWN -o OUTPUT [OPTIONS]

Options:
  -o, --output PATH   输出 EPUB 路径（必填）
  --meta PATH         元数据 YAML 路径（默认读取同级 meta.md）
  --cover PATH        封面图片路径
  --css PATH          CSS 样式表路径（默认内置 kindle.css）
  --config PATH       配置 YAML 路径
  -v, --verbose       启用 DEBUG 日志
```

### `pdf2book convert` — 一键模式

```
pdf2book convert PDF -o OUTPUT [OPTIONS]

Options:
  -o, --output PATH   输出 EPUB 路径（必填）
  --resume            从缓存恢复
  --config PATH       配置 YAML 路径
  --backend NAME      OCR 后端：paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --cover PATH        封面图片路径
  --ai-review         启用 AI 审查
  -v, --verbose       启用 DEBUG 日志
```

### `pdf2book batch` — 批量转换

```
pdf2book batch INPUT_DIR -o OUTPUT_DIR [OPTIONS]

Options:
  -o, --output PATH   输出目录（必填）
  --workers N         并行工作进程数（内存随进程数线性增长）
  --resume            从缓存恢复
  --config PATH       配置 YAML 路径
  --backend NAME      OCR 后端
  --ai-review         启用 AI 审查
  -v, --verbose       启用 DEBUG 日志
```

## 配置

通过 `--config config.yaml` 指定配置文件。完整字段示例见 [`config.yaml`](config.yaml)：

```yaml
ocr:
  backend: paddle_pp        # paddle_pp (CPU) | rapid_ocr | paddle_vl (GPU) | cloud_ocr
  dpi: 300                  # 渲染 DPI，越高越清晰但越慢
  use_region_detection: true
  use_table_recognition: false
  use_formula_recognition: false

postprocess:
  drop_header_footer: true
  merge_cross_page: true
  infer_title_level: true
  chapter_patterns:         # 章节标题正则，用于层级推断
    - "第[一二三四五六七八九十百千0-9]+[章回节卷篇]"
    - "Chapter\\s+[IVX0-9]+"

epub:
  toc_depth: 2              # 目录显示到 H 几
  chapter_level: 1          # Pandoc --split-level，在此级别处分页
  css_path: null            # 自定义 CSS（默认 src/pdf2book/epub/templates/kindle.css）
  cover: null               # 封面图片（推荐用 --cover 命令行参数传入）

ai_review:
  enabled: false            # 默认关闭，用 --ai-review 标志启用
  api_url: ""               # OpenAI 兼容的 chat/completions 端点
  api_key: ""               # API 密钥
  model: "gpt-4o-mini"      # 模型名（约束验证循环保证质量，可用便宜模型）
  max_tokens: 8192          # 响应 token 上限（大书需 8192 避免截断）
```

**排版调优建议**：

| 书类型 | toc_depth | chapter_level | 说明 |
|---|---|---|---|
| 故事集 / 短篇合集 | 3 | 3 | 每个 H3 故事独立成页，目录可跳转 |
| 长篇小说 | 2 | 1 | 按 H1 章节分页 |
| 分章节书籍 | 2 | 2 | 按 H2 章节分页 |
| 无章节结构 | 1 | 1 | 整本书一页 |

**内置 CSS**：`src/pdf2book/epub/templates/kindle.css`，遵循 Kindle KDP 约束（不在 body/p 设 font-size、不用 flexbox/grid/@media），中文行距 1.75、首行缩进 2em、标题居中。可通过 `--css` 覆盖。

## 项目结构

```
src/pdf2book/
├── cli.py              # Typer CLI 入口（ocr/epub/convert/batch 子命令）
├── __main__.py         # 支持 python -m pdf2book
├── pipeline.py         # 两阶段流水线编排
├── batch.py            # 批量并行转换
├── config.py           # Pydantic 配置模型
├── ocr/                # OCR 后端（paddle_pp/rapid_ocr/paddle_vl/cloud_ocr + 抽象基类）
├── postprocess/        # 后处理
│   ├── processor.py        # 编排：页眉页脚/跨页合并/标题层级/图片裁剪
│   ├── header_footer.py    # 页眉页脚检测与去除
│   ├── merger.py           # 跨页段落合并（CJK 标点感知）
│   ├── structure.py        # 标题层级推断 + 页面分类调度
│   ├── page_classifier.py  # 规则化页面类型识别（封面/扉页/版权/目录/正文/尾页）
│   ├── cip_extractor.py    # CIP 元数据提取（GB/T 12451）
│   ├── confidence_filter.py # OCR 置信度过滤与三级标记
│   ├── typography.py       # 中文出版排版规则
│   └── images.py           # 插图裁剪
├── review/             # AI 审查流水线（--ai-review 启用）
│   ├── markdown_review.py  # Collector + Prompt + Applier（含 TOC 链接化）
│   ├── ai_client.py        # LLM 调用 + 重试 + JSON 修复
│   └── constraints.py      # 校正约束提取与验证
├── epub/               # EPUB 构建
│   ├── builder.py          # Pandoc 调用 + 后处理（移除自动标题页/修复 ncx）
│   ├── metadata.py         # 元数据 YAML 读写 + BookMetadata
│   ├── toc_links.py        # 目录链接化纯文本 fallback
│   └── templates/kindle.css # Kindle 优化 CSS
├── pdf/                # PDF 渲染与元数据提取
└── utils/              # SQLite 缓存、日志
```

## Trae Skill

项目内置一个 Trae Skill，让 AI agent 自动完成完整的转换流程：

- **位置**：[`.trae/skills/pdf2book/SKILL.md`](.trae/skills/pdf2book/SKILL.md)
- **触发**：在 Trae 中说「把 XX.pdf 转成 EPUB」
- **流程**：9 步决策链（OCR → 页面分析 → 元数据提取 → OCR 校对 → 排版推断 → EPUB 生成）

Skill 路径下 AI agent 自身承担所有"需要理解内容"的决策，无需外部 LLM API key。

## 贡献说明

欢迎贡献代码！请遵循以下流程：

1. **Fork 仓库** 并克隆到本地
2. **创建分支**：`git checkout -b feature/your-feature-name`
3. **安装开发依赖**：`pip install -e ".[ocr,dev]"`
4. **编写代码** 并确保通过检查：
   ```bash
   ruff check src/          # 代码风格检查
   ruff format src/         # 代码格式化
   pytest -v -m "not slow"  # 运行快速测试（无需 OCR 模型）
   ```
5. **提交更改**：使用规范的 commit message（如 `feat: add xxx` / `fix: resolve xxx`）
6. **创建 Pull Request**：描述改动内容和动机

### 开发规范

- **代码风格**：使用 [ruff](https://docs.astral.sh/ruff/) 检查和格式化，行宽 100
- **类型注解**：所有公共函数需添加类型注解
- **测试**：新功能需附带测试，标记 `slow` 的测试需要加载真实 OCR 模型
- **commit 规范**：遵循 [Conventional Commits](https://www.conventionalcommits.org/)

### 项目架构

PDF2BOOK 采用模块化设计，核心分层：

- **OCR 层**（`ocr/`）：可插拔的 OCR 后端，统一 `OCRBackend` 抽象基类
- **后处理层**（`postprocess/`）：规则化的文本处理，包括页面分类、CIP 提取、置信度过滤
- **AI 审查层**（`review/`）：可选的 LLM 校对流水线，带约束验证重试循环
- **EPUB 层**（`epub/`）：Pandoc 驱动的 EPUB 生成，含 Kindle 优化 CSS

详细的 PP-StructureV3 JSON 字段映射和开发笔记见 [Developer Notes](#developer-notes) 部分。

## 许可证

[MIT License](LICENSE) — Copyright (c) 2026 pdf2book

---

## Developer Notes

### PP-StructureV3 JSON → Element Mapping

Verified against `paddleocr==3.7.0` spike fixture
(`tests/fixtures/spike_output/sample_page1_res.json`).

#### `parsing_res_list[i]` block fields

| PP-StructureV3 field | `Element` field        | Notes |
|---|---|---|
| `block_label`        | `type`                 | e.g. `paragraph_title`, `text`, `table`, `image` |
| `block_content`      | `text`                 | Recognized text / table HTML / formula LaTeX |
| `block_bbox`         | `bbox` (`x1,y1,x2,y2`) | 4-element list, page pixel coords |
| `block_order`        | `order_index`          | XYCut reading order (starts at 1) |
| `block_id`           | — (dropped)            | Internal PP index, redundant with `block_order` |
| — (absent)           | `title_level`          | PP does not serialize level into JSON; filled `None`. `TitleLevelInferrer` owns level inference |
| — (absent)           | `confidence`           | Filled by `_lookup_rec_score` matching `block_bbox` against `overall_ocr_res.rec_scores`. Falls back to `layout_det_res.boxes.score` via `_lookup_score`, then `None` |

#### Top-level fields

| PP-StructureV3 field | `PageResult` field | Notes |
|---|---|---|
| `width` / `height`   | `width` / `height` | Top-level floats, page pixel dims |
| `parsing_res_list`   | `elements`         | Parsed via `_parse_elements` |
| — (PP's own markdown) | `markdown_ref`    | Best-effort extracted from `res.markdown` for debug comparison only; pipeline rebuilds markdown from `Element`s |

#### Ignored subtrees

- `doc_preprocessor_res` — orientation/unwarping metadata, unused (we disable both).
- `layout_det_res.boxes` — raw detection boxes with `cls_id`/`label`/`score`/`coordinate`. Already consumed by PP internally to produce `parsing_res_list`; `score` is used as a fallback for `Element.confidence` when `rec_scores` matching fails.
- `overall_ocr_res` — full-page OCR fallback (`dt_polys`, `rec_texts`, `rec_scores`). `rec_scores` is the primary source for `Element.confidence` (matched to `block_bbox`); `dt_polys`/`rec_texts` are unused since `parsing_res_list.block_content` already carries per-block text.

### Runtime vs Fixture JSON Shape

PP-StructureV3 exposes the JSON payload in two shapes that `_extract_json`
normalizes transparently:

| Source | Shape | Example |
|---|---|---|
| Runtime `res.json` attribute | Wrapped: `{"res": {input_path, width, height, parsing_res_list, ...}}` | What `PaddlePPBackend.recognize` consumes |
| `res.save_to_json(path)` file | Unwrapped: `{input_path, width, height, parsing_res_list, ...}` | What `tests/fixtures/spike_output/sample_page1_res.json` contains |

`_extract_json` peels the `res` wrapper when present, so both shapes yield the
same inner dict. Fixture-based unit tests pass the unwrapped dict directly;
runtime code passes the PP result object and lets `_extract_json` peel it.
