# PDF2BOOK

> 让 AI 当你的电子书排版师 —— 把扫描版 PDF 一句话转成 Kindle 友好的 EPUB

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

## Features

- **OCR 识别** — 基于 PaddleOCR PP-StructureV3，识别文本、标题、图片、表格等版面元素
- **页眉页脚去除** — 自动检测并去除重复出现的页眉、页码、running head
- **跨页段落合并** — 正确处理 CJK 标点，避免段尾/段首错误的空格
- **标题层级推断** — 基于字号和章节模式（第X章 / Chapter N）推断 H1–H3 层级
- **图片裁剪提取** — 按 OCR bbox 从渲染页面裁剪插图，保存为独立 PNG 引用
- **两阶段工作流** — `ocr` 生成可预览的 Markdown + 元数据，`epub` 从 Markdown 构建 EPUB，中间可手动编辑
- **跳过无关页** — `skip_first_pages` / `skip_last_pages` 跳过封面、版权、目录、尾页的 OCR
- **断点续作** — SQLite 缓存 OCR 结果，`--resume` 跳过已完成的页面
- **Kindle 优化排版** — 内置 `kindle.css`，`chapter_level` 控制分章粒度，每个故事/章节独立成页

## 安装

```bash
pip install -e ".[ocr,dev]"
```

> **注意**：`paddlepaddle` 体积较大（约 1.5GB 模型 + 依赖）。国内用户可使用镜像源加速：
> ```bash
> pip install -e ".[ocr,dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

**系统依赖**：Pandoc（由 `pypandoc_binary` 自动捆绑，无需单独安装）。

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

### 断点续作

OCR 是最耗时的阶段。如果中途中断，可用 `--resume` 从缓存恢复，跳过已完成的页面：

```bash
pdf2book ocr 世界神话传说.pdf --resume --config config.yaml
```

### 常见场景

**场景 1：故事集 / 短篇合集**

每个故事是 H3 标题，希望每个故事独立成页、目录可跳转。在 `config.yaml` 中设置：

```yaml
epub:
  toc_depth: 3        # 目录显示到故事标题
  chapter_level: 3    # 每个 H3 故事独立分页
```

**场景 2：扫描书有封面/版权/目录页**

前几页是封面、副封面、版权信息、目录，不需要 OCR。在 `config.yaml` 中设置：

```yaml
postprocess:
  skip_first_pages: 5   # 跳过前 5 页（仍渲染，可用作封面）
  skip_last_pages: 1    # 跳过最后 1 页（尾页/广告）
```

跳过的页面仍会渲染为 PNG（所以 `--cover` 仍可用 `page_0000.png`），只是不进行 OCR 和内容提取。

**场景 3：长篇小说按章节分页**

章节是 H1（`第X章`），希望每章独立成页：

```yaml
epub:
  toc_depth: 2        # 目录显示章标题
  chapter_level: 1    # 每个 H1 章节分页
```

## CLI 参考

三个子命令，通过 `pdf2book <subcommand>` 调用（也支持 `python -m pdf2book`）：

### `pdf2book ocr` — 阶段 1：PDF → Markdown

```
pdf2book ocr PDF [OPTIONS]

Options:
  --resume       从缓存恢复，跳过已 OCR 的页面
  --config PATH  配置 YAML 路径（默认使用内置配置）
  -v, --verbose  启用 DEBUG 日志
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
  --cover PATH        封面图片路径
  -v, --verbose       启用 DEBUG 日志
```

## 配置

通过 `--config config.yaml` 指定配置文件。完整字段示例见 [`config.yaml`](config.yaml)：

```yaml
ocr:
  backend: paddle_pp        # paddle_pp (CPU) | paddle_vl (GPU)
  dpi: 300                  # 渲染 DPI，越高越清晰但越慢
  use_region_detection: true
  use_table_recognition: false
  use_formula_recognition: false

postprocess:
  drop_header_footer: true
  merge_cross_page: true
  infer_title_level: true
  skip_first_pages: 0       # 跳过前 N 页（封面/版权/目录），仍渲染但不 OCR
  skip_last_pages: 0        # 跳过后 M 页（尾页/广告），仍渲染但不 OCR
  chapter_patterns:         # 章节标题正则，用于层级推断
    - "第[一二三四五六七八九十百千0-9]+[章回节卷篇]"
    - "Chapter\\s+[IVX0-9]+"

epub:
  toc_depth: 2              # 目录显示到 H 几
  chapter_level: 1          # Pandoc --split-level，在此级别处分页
  css_path: null            # 自定义 CSS（默认 src/pdf2book/epub/templates/kindle.css）
  cover: null               # 封面图片（推荐用 --cover 命令行参数传入）
```

**排版调优建议**：
- 故事集 / 短篇合集：`toc_depth: 3` + `chapter_level: 3`（每个 H3 故事独立成页，目录可跳转）
- 长篇小说：`toc_depth: 2` + `chapter_level: 1`（按 H1 章节分页）
- 扫描书有封面/版权/目录页：`skip_first_pages: 5`（页数根据实际调整）

**内置 CSS**：`src/pdf2book/epub/templates/kindle.css`，遵循 Kindle KDP 约束（不在 body/p 设 font-size、不用 flexbox/grid/@media），中文行距 1.75、首行缩进 2em、标题居中。可通过 `--css` 覆盖。

## 项目结构

```
src/pdf2book/
├── cli.py              # Typer CLI 入口（ocr/epub/convert 子命令）
├── __main__.py         # 支持 python -m pdf2book
├── pipeline.py         # 两阶段流水线编排
├── config.py           # Pydantic 配置模型
├── ocr/                # OCR 后端（PaddlePP + 抽象基类）
├── postprocess/        # 后处理（页眉页脚/跨页合并/标题层级/图片裁剪）
├── epub/               # EPUB 构建（Pandoc + 元数据 + kindle.css）
├── pdf/                # PDF 渲染与元数据提取
└── utils/              # SQLite 缓存、日志
```

## 测试

```bash
pytest -v
```

OCR 相关测试（需加载 1.5GB PP-StructureV3 模型）标记为 `slow`，默认跳过：

```bash
pytest -v -m slow       # 运行所有测试（含 OCR）
pytest -v -m "not slow" # 仅运行快速测试
```

## License

MIT — 见 [LICENSE](LICENSE)。

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
| — (absent)           | `confidence`           | MVP does not parse; would require IoU-matching `block_bbox` against `layout_det_res.boxes.coordinate` |

#### Top-level fields

| PP-StructureV3 field | `PageResult` field | Notes |
|---|---|---|
| `width` / `height`   | `width` / `height` | Top-level floats, page pixel dims |
| `parsing_res_list`   | `elements`         | Parsed via `_parse_elements` |
| — (PP's own markdown) | `markdown_ref`    | Best-effort extracted from `res.markdown` for debug comparison only; pipeline rebuilds markdown from `Element`s |

#### Ignored subtrees

- `doc_preprocessor_res` — orientation/unwarping metadata, unused (we disable both).
- `layout_det_res.boxes` — raw detection boxes with `cls_id`/`label`/`score`/`coordinate`. Already consumed by PP internally to produce `parsing_res_list`; only useful if we want `confidence` (deferred).
- `overall_ocr_res` — full-page OCR fallback (`dt_polys`, `rec_texts`, `rec_scores`). Unused; `parsing_res_list.block_content` already carries per-block text.

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
