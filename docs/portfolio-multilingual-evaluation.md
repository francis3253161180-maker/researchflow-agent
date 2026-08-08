# 本地多语种真实文档检索评测

## 目的与边界

本机回归评测验证中文问题能否从英文论文、英文 rebuttal、OpenReview 记录，以及中文 DOCX/PDF 简历中检索到正确文档和直接证据。它不是通用企业语料评测，也不衡量最终回答忠实度。

## 语料与问题

- 8 份用户提供的本地文件：MAC-KV 与 Holo 的论文 PDF、rebuttal Markdown、OpenReview Markdown，以及 Agent 简历 DOCX、量化简历 PDF；
- 16 条人工编写的中文问题，覆盖方法、数值结果、实验边界、reviewer 观点、rebuttal 结论、DOCX 简历项目与 PDF 简历科研信息；
- 每题标注目标文档，并用一个证据短语检查前 4 个 chunk 是否包含直接支撑；问题与标签见 `evals/portfolio_multilingual_queries.json`；
- PDF 使用页级抽取后进行段落/自然换行/句末优先分块；Markdown 继承标题上下文；DOCX 同时抽取普通段落和表格单元格。

## 本机结果（2026-08-08）

| 后端 / 策略 | Recall@1 | Recall@3 | MRR@6 | 前 4 chunk 证据提示命中 | 平均检索延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hash / Hybrid | 0.3125 | 0.8750 | 0.5833 | 0.5000 | 82.56 ms |
| FastEmbed multilingual / Lexical | 0.2500 | 0.6875 | 0.4531 | 0.2500 | 206.75 ms |
| FastEmbed multilingual / Dense | 0.3750 | 0.7500 | 0.5417 | 0.1250 | 210.96 ms |
| FastEmbed multilingual / Hybrid | **0.7500** | **0.9375** | **0.8333** | **0.5625** | 211.88 ms |

模型为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，由 FastEmbed 在 CPU 上执行，首次下载后缓存于 `data/models`。Hash 是确定性测试基线，不具备跨语言语义能力。

## 结论与下一步

1. 多语种 embedding + BM25/RRF 对中文问题检索英文科研材料有明确收益；这里没有使用面向特定文档字段、评审角色或关键词的排序规则。
2. 正确文档被召回不等于前四个 chunk 已是最佳作答证据；当前 0.5625 的证据提示命中说明候选重排仍有空间。
3. 下一步以这份固定问题集评估可选多语种 cross-encoder reranker；只有证据命中增益显著且 CPU 延迟可接受时，才作为默认选项。
4. 切换 embedding provider/model 后必须删除旧文档并重新导入。

## 复现

```powershell
.\.venv\Scripts\python.exe scripts\run_portfolio_multilingual_eval.py --embedding-provider fastembed --output evals\results\portfolio_multilingual_fastembed.json
.\.venv\Scripts\python.exe scripts\run_portfolio_multilingual_eval.py --embedding-provider hash --output evals\results\portfolio_multilingual_hash.json
```

本地评测语料未提交到 Git 仓库；脚本默认从项目上一级目录按清单路径读取文件。
