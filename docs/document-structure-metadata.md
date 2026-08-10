# 文档结构元数据：页码、标题栈与分块

ResearchFlow 在导入阶段把内容转换为统一的 `TextBlock`：

```python
TextBlock(content="...", page=5, section="3 Experiments › 3.1 Setup")
```

`page` 与 `section` 是独立维度：section 可以跨页延续，页码始终定位当前证据实际出现的位置。

## 多级标题栈

所有能提供标题层级的格式都维护一个 `(level, title)` 栈。遇到同级或更高层标题时，弹出旧分支后加入新标题：

```text
1 Method
  1.1 Quantization
    1.1.1 Calibration

section = "1 Method › 1.1 Quantization › 1.1.1 Calibration"
```

完整路径会进入 `chunks.section` 元数据，并在索引内容前以 `[Section: ...]` 形式保留上下文。检索时，正文 BM25/语义相似度仍是主信号；标题、文件名与 section 是元数据导航信号，避免让空泛标题压过真正含有实验事实的正文块。

## PDF：页码可靠，标题为保守版式推断

PDF 使用 PyMuPDF 逐页读取文本行、字体大小和字体名称：

1. 每页始终保留 `page`；
2. 对短文本行，以编号形态、相对字号、粗体等通用版式信号识别候选标题；
3. 标题栈在整个 PDF 生命周期内持续，不会在翻页时重置；
4. 每页正文块继承当前 section，但不跨页合并，确保引用页码准确；
5. 位于页边缘且跨多页重复的文本被视为 running header/footer，避免错误更新标题栈。

例如 `3.1 Setup` 出现在第 4 页，正文延续到第 5 页时，第 5 页块仍保存：

```text
page=5
section="3 Experiments › 3.1 Setup"
```

PDF 版式差异很大。扫描件、复杂双栏、图表、公式或未编号标题可能无法可靠得到 section；系统会保留页码，并宁可不给 section，也不会编造标题层级。若 PyMuPDF 对异常文件解析失败，导入会回退到 pypdf 的逐页文本提取，仍保留页码。

## DOCX：Heading 可靠，页码不是原生属性

DOCX 使用 Word 内置 Heading/标题样式及其继承链识别 Heading 1/2/3…，并按 WordprocessingML 中段落和表格的真实顺序遍历。普通段落与表格都会继承当前完整 section 路径。

`.docx` 是流式排版格式，`python-docx` 不提供可靠的段落页码。因此当前 DOCX 引用展示 section，不伪造 page。若未来确有页码需求，应作为可选、显式配置的渲染管线：

```text
DOCX → LibreOffice/Word 渲染为 PDF → PDF 解析 → 页码与结构对齐
```

这会增加导入延迟、外部依赖与段落对齐复杂度，不能作为默认导入路径。

## Markdown、XLSX 与通用分块

- Markdown 用 `#` 到 `######` 维护同一标题栈；
- XLSX 使用 `工作表：名称｜行 start-end` 作为可追溯 section；
- TXT 没有天然结构时保持空 section。

格式解析后的每个 `TextBlock` 再按自然边界优先切块：空段 → 换行/句末 → 最后才回退到约 620 字符、80 字符重叠的滑窗。很短的前导标题会和紧随其后的正文合并，避免产生只有标题、没有事实的低质量证据块。

## 已验证边界

单元测试覆盖：Markdown 多级路径、DOCX Heading + 表格的顺序继承、PDF 标题栈跨页继承、以及已有的 PDF 页码/XLSX 行范围元数据。它们验证解析链路，不代表所有扫描 PDF、复杂排版或任意 Word 模板都能得到完美 section。
