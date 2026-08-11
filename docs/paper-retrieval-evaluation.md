# ResearchFlow 小规模论文检索评测

## 目的与边界

本评测验证的是 ResearchFlow 的**文档检索层**：面对一个与论文内容相关的问题，系统能否把人工标注的目标论文排在前面。它不复现、不评判，也不与语料中的 GraphRAG、结构化检索或多跳问答方法横向比较。

语料由 4 篇本地提供的公开科研论文构成，问题集由 16 条人工标注的文档检索问题组成。每条问题只标记一个相关源论文；检索结果先按文档去重，再计算目标论文的排序位置，避免同一篇论文的多个 chunk 虚高名次。

## 方法

| 项目 | 设置 |
| --- | --- |
| 语料 | 4 篇 PDF：CWA-GRAPH、SAR-UIE、ERES-FROG、TopoR |
| 查询 | 16 条英文科研问题；标签位于 `evals/paper_retrieval_queries.json` |
| 检索策略 | Lexical（BM25 风格）、Dense、Hybrid（RRF） |
| Embedding | 离线 Hash；CPU FastEmbed `BAAI/bge-small-zh-v1.5` |
| 指标 | Recall@1、Recall@2、MRR@4、平均检索延迟 |
| 相关性单位 | 源论文；同一论文的多个 chunks 只保留最高排名 |

运行：

```powershell
python scripts/run_eval.py --corpus-dir .. --embedding-provider hash
python scripts/run_eval.py --corpus-dir .. --embedding-provider fastembed
```

脚本会把完整结果分别写入 `evals/results/paper_retrieval.json` 与 `evals/results/paper_retrieval_fastembed.json`。PDF 不纳入 Git 仓库；要复现，需要把同名论文 PDF 放入 `--corpus-dir` 指定的目录。

## 实测结果

| 后端 / 策略 | Recall@1 | Recall@2 | MRR@4 | 平均检索延迟 |
| --- | ---: | ---: | ---: | ---: |
| Hash + Lexical | 1.0000 | 1.0000 | 1.0000 | 86.27 ms |
| Hash + Dense | 0.7500 | 0.8750 | 0.8490 | 92.10 ms |
| Hash + Hybrid RRF | 0.8750 | 0.8750 | 0.9167 | 86.96 ms |
| FastEmbed + Lexical | 1.0000 | 1.0000 | 1.0000 | 651.50 ms |
| FastEmbed + Dense | 0.6875 | 0.8750 | 0.8229 | 645.25 ms |
| FastEmbed + Hybrid RRF | 0.7500 | 0.8125 | 0.7812 | 644.24 ms |

## 如何解读

这是一个刻意不美化的结果：论文题目、方法名和问题之间存在强词法重合，Lexical 在 Recall@1 上因此最好；Hash Dense 不是语义模型，不应拿它代表真实 Dense Retrieval；FastEmbed 在这台 CPU 环境中带来约 1 秒级平均检索耗时，Hybrid 用 RRF 提升了 Recall@2，但没有击败 Lexical 的 Recall@1。

结论不是“Hybrid 一定更强”，而是：

1. 对强术语匹配的科研检索，先保留词法检索基线；
2. Hybrid 可作为提高候选覆盖率的策略，而不是默认的性能承诺；
3. 后续若接入 Reranker，应在新的、人工标注且术语重合更弱的语料上重新评测；
4. 当前样本量为 16，只适合作为项目级回归与取舍证据，不能外推为通用 RAG benchmark。

## 结果解读

> 我没有把 ResearchFlow 和语料论文中的 GraphRAG 方法直接比较，而是借鉴科研检索场景搭建了一个 4 篇论文、16 条人工标注问题的小规模文档检索评测。评测按文档去重后比较 Lexical、Dense 和 RRF 融合的 Recall@1/2、MRR@4 与延迟。结果显示强术语匹配下 BM25 风格词法检索最强；Hybrid 在 FastEmbed 条件下提升候选覆盖率，但有 CPU 延迟代价。这个结果帮助我把“默认使用 Hybrid”改成按语料分布和延迟预算选型，而不是宣称某种方法永远更好。
