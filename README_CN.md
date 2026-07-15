# PDF2BOOK

> 让 AI 当你的电子书排版师 —— 把扫描版 PDF 一句话转成 Kindle 友好的 EPUB

[English](README.md) | 中文

PDF2BOOK 是一个 AI 驱动的自动排版工具：AI 不是简单的 OCR 调用者，而是像一位编辑一样完成所有需要"理解内容"的决策——判断页面类型、提取元数据、校对 OCR 错字、推断章节结构，最终生成带目录、分章节、Kindle 优化的 EPUB。

## 快速开始

**三步完成第一本 EPUB：**

```bash
# 1. 安装
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"

# 2. 放入 PDF
cp 你的书.pdf inbox/

# 3. 转换
pdf2book
# → library/你的书.epub
```

项目采用标准三文件夹结构，零配置即可使用：

```
PDF2BOOK/
├── inbox/       # 放入待转换的 PDF
├── library/     # 生成的 EPUB（文件名与 PDF 同名）
└── workspace/   # 中间产物（每本书独立子目录 workspace/{stem}/）
```

## 三种使用方式

三种模式都能让 AI 全面接管校对/排版/元数据工作，用户无需手动 review。

**推荐优先级：CLI > Skill > WebUI（可选）。** CLI 与 Skill 是核心路径；WebUI 是非必要的可视化扩展，构建在 CLI 引擎之上。

| 优先级 | 模式 | 适用场景 | 是否需要 API key | AI 工作由谁做 | 安装方式 |
|---|---|---|---|---|---|
| **1（推荐）** | **CLI 模式** | 命令行批量、脚本集成、自动化 | 需要（config.yaml） | 外部 LLM（如 GPT-4o-mini） | `pip install -e ".[ocr]"` |
| **2** | **Skill 模式** | 在任意 AI agent 中自然语言触发 | 不需要 | Agent 自身推理 | 通过 Skill 文件一键安装 |
| **3（可选）** | **WebUI 模式** | 浏览器可视化预览 + 分模块排版 | 可选 | 外部 LLM 或 agent | `pip install -e ".[ocr,web]"` |

> **说明**：WebUI 是可选扩展模块。核心 CLI（`ocr`/`epub`/`convert`/`batch`）在未安装 `web` extra 的情况下也能完整运行。仅在需要可视化预览和分模块编辑时才安装 WebUI。

### CLI 模式（推荐）

在 `config.yaml` 填入 `api_key` — AI 审查自动启用，无需额外标志：

```yaml
ai_review:
  api_key: "your-api-key"    # 填入后自动启用
  model: "gpt-4o-mini"
```

```bash
pdf2book convert inbox/你的书.pdf          # 一键 PDF → EPUB
pdf2book ocr inbox/你的书.pdf              # PDF → Markdown（分步走）
pdf2book epub workspace/你的书/book.md -o library/你的书.epub  # Markdown → EPUB
pdf2book batch                             # 批量：inbox/ → library/
pdf2book ocr inbox/你的书.pdf --resume      # 断点续作
```

### Skill 模式（无需 API key）

把这行粘贴到任意 AI agent 对话框（Claude Code、Cursor、Codex、Trae 等）— 它会自动读取技能文件并完成安装：

```bash
curl -fsSL https://raw.githubusercontent.com/charlesilcn/PDF2BOOK/main/skills/pdf2book/SKILL.md
```

然后只需说「把 XX.pdf 转成 EPUB」。Agent 读取 9 步工作流后，自动检查环境 → 克隆仓库 → 安装依赖 → 执行完整转换，无需外部 API key。

- **技能文件**：[`skills/pdf2book/SKILL.md`](skills/pdf2book/SKILL.md)
- **Agent 入口**：[`AGENTS.md`](AGENTS.md)（通用入口，Claude Code/Cursor/Codex 等自动读取）

### WebUI 模式（可选扩展）

基于 CLI 引擎构建的浏览器界面，提供分屏预览和分模块排版控制，适合偏好可视化编辑的用户。

```bash
# 1. 安装可选 'web' 依赖
pip install -e ".[ocr,web]"

# 2. 启动服务器
pdf2book web                          # http://127.0.0.1:8000
pdf2book web --port 9000 --host 0.0.0.0   # 自定义 host/port
```

当未安装 FastAPI 时，`web` 子命令会打印安装提示并优雅退出，核心 CLI 命令完全不受影响。

- **UI 资源**：[`pdf2book-ui/`](pdf2book-ui/)（HTML/CSS/JS，无构建步骤）
- **后端**：[`src/pdf2book/web/`](src/pdf2book/web/)（FastAPI 路由 + 转换管理器）

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

| Extras | 说明 | 用于 | 安装命令 |
|---|---|---|---|
| `ocr` | PaddleOCR PP-StructureV3（默认 OCR 后端） | 核心 OCR | `pip install -e ".[ocr]"` |
| `rapid` | RapidOCR 轻量后端（约 50MB） | 备选 OCR | `pip install -e ".[rapid]"` |
| `cloud` | 远程 OCR API 后端 | 备选 OCR | `pip install -e ".[cloud]"` |
| `web` | FastAPI + Uvicorn | **仅 WebUI**（可选） | `pip install -e ".[web]"` |
| `dev` | 测试与代码检查工具 | 开发 | `pip install -e ".[dev]"` |

## 核心亮点：AI 是决策者，不是工具调用者

传统转换工具只会"机械搬运"，PDF2BOOK 让 AI 承担 4 项编辑决策：

| AI 决策 | 做什么 | 为什么需要 AI |
|---|---|---|
| **页面类型识别** | 审查 OCR 结果，判断封面/版权/目录/正文/尾页 | 需要理解页面内容语义，规则无法穷举 |
| **元数据自动提取** | 从版权页提取书名、作者、ISBN、语言 | 扫描书 PDF 内嵌元数据通常缺失 |
| **OCR 智能校对** | 修正错别字、调整标题层级、清理噪声 | OCR 对中文标点/生僻字易出错 |
| **排版参数推断** | 统计标题分布，判断故事集 vs 长篇小说 | 不同书类型需要不同分章粒度 |

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
- **AI 审查流水线** — 配置 `api_key` 后自动启用 LLM 校对低置信度文本、修正标题、提取元数据、验证书本结构；`epub` 阶段支持补做（幂等）
- **目录自动链接化** — 自动将"标题／页码"格式目录转为可点击的竖排链接列表，跳转到对应章节
- **装饰图片自动剥离** — 基于感知哈希（pHash）聚类检测重复出现的装饰图（章节分隔符、花饰），自动从 EPUB 中剥离；保护功能图（二维码/条形码）不被误删
- **多模态视觉审查** — AI 审查可选发送页面图片辅助校对低置信度文本和标题（需视觉模型如 gpt-4o-mini）
- **批量处理** — `batch` 子命令并行转换目录下所有 PDF，每个 PDF 独立工作目录和缓存
- **断点续作** — SQLite 缓存 OCR 结果，`--resume` 跳过已完成的页面
- **Kindle 优化排版** — 内置 `kindle.css`，`chapter_level` 控制分章粒度，每个故事/章节独立成页

## CLI 参考

运行 `pdf2book`（无参数）等价于 `pdf2book batch inbox -o library`，自动扫描 inbox/ 并输出到 library/。四个子命令：

### `pdf2book` — 零参数默认行为

```
pdf2book
# 扫描 inbox/ → library/{stem}.epub
```

### `pdf2book ocr` — 阶段 1：PDF → Markdown

```
pdf2book ocr PDF [OPTIONS]

Options:
  --resume          从缓存恢复，跳过已 OCR 的页面
  --config PATH     配置 YAML 路径（默认自动查找 cwd 的 config.yaml）
  --backend NAME    OCR 后端：paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --no-ai-review    强制关闭 AI 审查
  -v, --verbose     启用 DEBUG 日志
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
  --no-ai-review      强制关闭 AI 审查
  -v, --verbose       启用 DEBUG 日志
```

### `pdf2book convert` — 一键模式

```
pdf2book convert [PDF] [-o OUTPUT] [OPTIONS]

# 无 PDF 参数：处理 inbox/ → library/
# 有 PDF 无 -o：默认输出 library/{stem}.epub

Options:
  -o, --output PATH   输出 EPUB 路径（默认：library/{stem}.epub）
  --resume            从缓存恢复
  --config PATH       配置 YAML 路径
  --backend NAME      OCR 后端
  --cover PATH        封面图片路径
  --no-ai-review      强制关闭 AI 审查
  -v, --verbose       启用 DEBUG 日志
```

### `pdf2book batch` — 批量转换

```
pdf2book batch [INPUT_DIR] [-o OUTPUT_DIR] [OPTIONS]

# 默认：inbox/ → library/

Options:
  -o, --output PATH   输出目录（默认：library/）
  --workers N         并行工作进程数（内存随进程数线性增长）
  --resume            从缓存恢复
  --config PATH       配置 YAML 路径
  --backend NAME      OCR 后端
  --no-ai-review      强制关闭 AI 审查
  -v, --verbose       启用 DEBUG 日志
```

### 运行后产物

| 文件 | 说明 |
|---|---|
| `workspace/{stem}/book.md` | OCR + AI 校对后的全文 Markdown |
| `workspace/{stem}/meta.md` | CIP/AI 提取的元数据 YAML（书名、作者、语言等） |
| `workspace/{stem}/pages/page_NNNN.png` | 每页渲染图（可用作封面） |
| `workspace/{stem}/images/pN_eM.png` | 裁剪出的插图 |
| `workspace/{stem}/cache.db` | SQLite 缓存，支持断点续作 |

### 常见场景

**故事集 / 短篇合集** — 每个故事独立成页：
```yaml
epub:
  toc_depth: 3
  chapter_level: 3
```

**长篇小说** — 按 H1 章节分页：
```yaml
epub:
  toc_depth: 2
  chapter_level: 1
```

**扫描书有封面/版权/目录页** — 页面分类器自动识别装饰页，无需手动配置 `skip_pages`。

## 配置

`pdf2book` 会自动查找当前目录的 `config.yaml`（无需 `--config` 显式指定）。完整字段示例见 [`config.yaml`](config.yaml)：

```yaml
work_dir: workspace          # 中间产物根目录
cache_db: workspace/cache.db # SQLite 缓存
input_dir: inbox             # 待转换 PDF 目录
output_dir: library          # EPUB 输出目录

ocr:
  backend: paddle_pp        # paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  dpi: 300
  use_region_detection: true

postprocess:
  drop_header_footer: true
  merge_cross_page: true
  infer_title_level: true
  chapter_patterns:
    - "第[一二三四五六七八九十百千0-9]+[章回节卷篇]"
    - "Chapter\\s+[IVX0-9]+"

epub:
  toc_depth: 2              # 目录显示到 H 几
  chapter_level: 1          # Pandoc --split-level
  css_path: null            # 自定义 CSS（默认内置 kindle.css）

ai_review:
  enabled: false            # 显式 false 时即使有 api_key 也不启用（Skill 路径用）
  api_url: ""               # OpenAI 兼容端点
  api_key: ""               # 填入后自动启用
  model: "gpt-4o-mini"
  max_tokens: 8192
  multimodal: false         # 多模态视觉审查
  max_images: 8
```

**环境变量配置** — 通过 `.env` 文件管理 API 密钥，避免硬编码：

```bash
cp .env.example .env
# 编辑 .env：PDF2BOOK_API_KEY=sk-your-key
```

`config.yaml` 中使用 `${VAR:-default}` 语法引用：
```yaml
ai_review:
  api_key: ${PDF2BOOK_API_KEY:-}
  api_url: ${PDF2BOOK_API_URL:-https://api.openai.com/v1/chat/completions}
```

> `.env` 文件已被 `.gitignore` 排除，不会上传到 GitHub。`.env.example` 是模板，可以安全提交。

**排版调优建议**：

| 书类型 | toc_depth | chapter_level | 说明 |
|---|---|---|---|
| 故事集 / 短篇合集 | 3 | 3 | 每个 H3 故事独立成页 |
| 长篇小说 | 2 | 1 | 按 H1 章节分页 |
| 分章节书籍 | 2 | 2 | 按 H2 章节分页 |
| 无章节结构 | 1 | 1 | 整本书一页 |

## 项目结构

```
PDF2BOOK/
├── inbox/                 # 放入待转换的 PDF（零配置入口）
├── library/               # 生成的 EPUB
├── workspace/             # 中间产物（workspace/{stem}/）
├── config.yaml            # 配置文件（自动加载）
├── AGENTS.md              # 通用 AI agent 入口
├── skills/pdf2book/SKILL.md  # 可移植 AI Skill 文件
├── pdf2book-ui/           # 可选 WebUI 资源（HTML/CSS/JS，无构建步骤）
└── src/pdf2book/
    ├── cli.py              # Typer CLI 入口（核心）
    ├── __main__.py         # 支持 python -m pdf2book
    ├── pipeline.py         # 两阶段流水线编排
    ├── batch.py            # 批量并行转换
    ├── config.py           # Pydantic 配置模型
    ├── ocr/                # OCR 后端（paddle_pp/rapid_ocr/cloud_ocr）
    ├── postprocess/        # 后处理
    │   ├── processor.py        # 编排
    │   ├── header_footer.py    # 页眉页脚检测与去除
    │   ├── merger.py           # 跨页段落合并（CJK 标点感知）
    │   ├── structure.py        # 标题层级推断 + 页面分类调度
    │   ├── page_classifier.py  # 规则化页面类型识别
    │   ├── cip_extractor.py    # CIP 元数据提取（GB/T 12451）
    │   ├── confidence_filter.py # 置信度过滤与三级标记
    │   ├── typography.py       # 中文出版排版规则
    │   ├── decorations.py      # 装饰图片剥离（pHash）
    │   └── images.py           # 插图裁剪
    ├── review/             # AI 审查流水线（配 api_key 自动启用）
    │   ├── markdown_review.py  # Collector + Prompt + Applier
    │   ├── ai_client.py        # LLM 调用 + 重试 + JSON 修复
    │   └── constraints.py      # 校正约束提取与验证
    ├── epub/               # EPUB 构建
    │   ├── builder.py          # Pandoc 调用 + 后处理
    │   ├── metadata.py         # 元数据 YAML 读写
    │   ├── toc_links.py        # 目录链接化
    │   └── templates/kindle.css # Kindle 优化 CSS
    ├── web/                # 可选 FastAPI WebUI（需安装 '[web]' extra）
    │   ├── server.py           # App 工厂
    │   ├── routes.py           # REST API + 页面路由
    │   ├── convert_manager.py  # Web 触发的转换编排
    │   ├── module_parser.py    # book.md ↔ 结构化模块
    │   └── models.py           # API Pydantic 模型
    ├── progress.py         # 进度报告抽象
    ├── pdf/                # PDF 渲染与元数据提取
    └── utils/              # SQLite 缓存、日志、.env 写入
```

## AI Skill（跨平台）

项目内置一个可移植的 Skill 文件，让任何 AI agent（Claude Code、Cursor、Codex、Trae 等）自动完成完整的转换流程，无需外部 LLM API key。

- **位置**：[`skills/pdf2book/SKILL.md`](skills/pdf2book/SKILL.md)
- **AGENTS.md**：[`AGENTS.md`](AGENTS.md)（通用 AI agent 入口，所有主流工具自动读取）
- **流程**：9 步决策链（OCR → 页面分析 → 元数据提取 → OCR 校对 → 排版推断 → EPUB 生成）

### 一键安装 Skill

把这行粘贴到你的 AI 智能体对话框 — 它会自动读取技能文件并完成安装：

```bash
curl -fsSL https://raw.githubusercontent.com/charlesilcn/PDF2BOOK/main/skills/pdf2book/SKILL.md
```

就这一步。技能文件会教智能体如何安装项目、检查依赖、并使用所有命令。智能体读取后会自动执行环境检查 → 克隆仓库 → 安装依赖，然后你只需说「把 XX.pdf 转成 EPUB」即可开始转换。

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

PDF2BOOK 采用模块化设计，严格区分**核心层**与**可选扩展层**：

**核心层**（始终可用，不依赖可选 extras）：

- **OCR 层**（`ocr/`）：可插拔的 OCR 后端，统一 `OCRBackend` 抽象基类
- **后处理层**（`postprocess/`）：规则化的文本处理，包括页面分类、CIP 提取、置信度过滤
- **AI 审查层**（`review/`）：可选的 LLM 校对流水线，带约束验证重试循环
- **EPUB 层**（`epub/`）：Pandoc 驱动的 EPUB 生成，含 Kindle 优化 CSS

**可选扩展层**（需额外安装，缺失时不影响核心 CLI）：

- **WebUI 层**（`web/` + `pdf2book-ui/`）：FastAPI 服务器 + 静态 HTML/CSS/JS，提供浏览器编辑界面。通过 `pdf2book web` 子命令懒加载；缺失 `fastapi`/`uvicorn` 时会优雅退出并打印安装提示，绝不影响 `ocr`/`epub`/`convert`/`batch` 命令。

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
