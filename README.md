# PDF2BOOK

将扫描版 PDF 图书转换为 Kindle 友好的 EPUB 的自动排版全流程工具。

PDF2BOOK 通过 PaddleOCR PP-StructureV3 对扫描页面进行 OCR，自动识别标题层级、去除页眉页脚、合并跨页段落、裁剪插图，最终生成带目录、分章节、Kindle 优化的 EPUB。支持两阶段工作流：先生成可预览编辑的 Markdown，确认无误后再构建 EPUB。

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

## 快速开始

### 两阶段工作流（推荐）

先生成 Markdown 预览，编辑确认后再构建 EPUB：

```bash
# 阶段 1：PDF → OCR → Markdown
pdf2book ocr input.pdf --config config.yaml

# 输出：.pdf2book/book.md（可预览编辑） + .pdf2book/meta.md（元数据）

# （可选）编辑 book.md 修正 OCR 错误，编辑 meta.md 填写书名/作者

# 阶段 2：Markdown → EPUB
pdf2book epub .pdf2book/book.md -o output.epub \
    --cover .pdf2book/pages/page_0000.png
```

### 一键模式

不预览中间结果，一次完成转换：

```bash
pdf2book convert input.pdf -o output.epub \
    --cover .pdf2book/pages/page_0000.png \
    --config config.yaml
```

### 断点续作

OCR 阶段中断后可从缓存恢复，跳过已完成的页面：

```bash
pdf2book ocr input.pdf --resume --config config.yaml
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
