# 本地真实文档端到端问答评测

## 目的与边界

这份评测验证的对象是完整问答链路：文档解析、混合检索、LLM 回答、引用回传与回答核验。

它不把“检索到正确文档”误称为“回答正确”，也不把一次模型裁判结果包装为通用 RAG 准确率。评测集、参考要点与判定逻辑独立于生产 Agent；生产代码不读取任何评测问题、论文名称、reviewer 名称或答案映射。

## 语料与协议

- 8 个用户提供的真实本地文件：MAC-KV、Holo 的论文 PDF、rebuttal Markdown、OpenReview Markdown，以及 Agent/DOCX 和量化/PDF 简历；
- 12 个端到端案例：10 个可由文档回答的问题，2 个必须拒答的未报告/隐私问题；
- 每个可回答案例有人工编写的 2--3 条参考事实要点；
- 每题真实调用 DeepSeek 生成答案，验证 `[n]` 是否指向实际返回的 citation；
- 再以“参考要点 + 实际回答 + 实际检索证据”进行受限的 LLM 辅助判定。裁判被要求只依赖证据，并给出每条要点的 0/1 覆盖、事实支撑判断和简短理由。

LLM 辅助判定可以识别明显遗漏和无依据结论，但与被测模型属于同一提供方，仍可能存在偏差。因此它是回归信号，不替代人工盲审。

## 当前本地基线（2026-08-08）

配置：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` + BM25/RRF，CPU 检索；`deepseek-v4-flash`，显式关闭 thinking mode。

| 指标 | 结果 |
| --- | ---: |
| 可回答案例的参考要点覆盖率 | 0.7667 |
| 可回答案例的回答支撑率（LLM 辅助） | 0.8000 |
| 可回答案例的有效引用编号率 | 1.0000 |
| 不可回答案例的正确拒答率 | 1.0000 |
| 端到端平均延迟 | 7956.40 ms |

这组结果的含义是：当前版本已经能稳定返回可解析的引用，也能在证据不足时拒答；但 10 个可回答问题中仍有明显失败案例，不能宣称高准确率。

## 已定位的失败类型

1. **证据未进入有限 Top-K。** `Reviewer jueW` 的具体评论、Holo 的 Qwen3-8B rebuttal 证据未被稳定召回，模型因此正确拒答或转向了无关结论。当前默认 Top-K 已提高到 6，仍需以固定评测集验证实际覆盖提升。
2. **证据块不够完整。** Holo 核心公式与机制解释落在相邻块时，回答只说“低秩谱滤波”，漏掉了乘性公式。
3. **生成不应超出引用。** 部分正确回答会补充当前引用块未直接展示的论文结果；这要求未来的 citation entailment 检查比“存在 `[n]` 标记”更严格。

这些是通用 RAG 的召回、分块、重排和 grounded generation 问题，不采用某篇论文、某个 reviewer 字段或中英关键词映射的专用规则修复。

## 复现

需要配置 `DEEPSEEK_API_KEY`（或兼容的 `LLM_*` 配置），并确保本地保留这 8 个私有评测文件。评测文件本身不提交到 Git 仓库。

```powershell
.\.venv\Scripts\python.exe scripts\run_portfolio_answer_eval.py --embedding-provider fastembed --output evals\results\portfolio_answer_eval_fastembed.json
```

原始逐题回答、引用、裁判理由和延迟保存在 `evals/results/portfolio_answer_eval_fastembed.json`。该结果文件不包含私有原文，但可能包含模型回答；在对外分享前仍应人工检查。

## 下一步

以这套固定评测集为基线，项目提供了 `auto` 的 Top-N multilingual cross-encoder reranker 接口：它只在检测到 CUDA 时加载，并只接收 `(query, passage)` 对进行重排，不依赖文件标题、格式、reviewer 或问题映射。QASPER 全文固定评测已证明 GPU BGE 可提升证据召回；对于这套私有问答集，仍应在不泄露原文的前提下单独验证要点覆盖、证据支撑和延迟，不能把 QASPER 结果直接当作其答案质量结果。
