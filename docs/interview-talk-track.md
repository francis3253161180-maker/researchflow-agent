# ResearchFlow RAG Agent 面试话术与事实边界

本文面向 Agent / RAG / 大模型应用实习面试，所有表述以当前 `main` 分支的实现、测试和评测为准。目标是帮助候选人准确说明项目价值、技术取舍和局限，不把组件堆叠成空泛关键词。

## 30 秒版本：HR 或非技术面试官

> ResearchFlow RAG Agent 是一个面向科研文档的本地可部署问答系统。我用 FastAPI 提供服务，用 LangGraph 显式编排路由、查询改写、混合检索、工具调用、回答、引用校验和持久化；系统支持多格式文档导入、页码和章节级引用、运行轨迹、Docker 部署及 MCP 工具开放。项目还通过自动化测试和多层检索评测验证链路与效果，而不是只展示一个能对话的 Demo。

## 90 秒版本：技术面试开场

> 我做 ResearchFlow 是因为普通 RAG Demo 经常只有“上传文档然后问答”，缺少证据追溯、失败定位和可重复评测。系统入口是 FastAPI，业务能力集中在 `ResearchFlowService`，核心工作流由 LangGraph `StateGraph` 编排。知识问题走 `route → rewrite → retrieve → answer → verify → persist`，计算问题走工具分支；验证失败后不会无限循环，而是根据失败原因至多修复一次：没有证据或证据不相关时改写 Query 并重新检索，缺少引用或引用越界时基于同一批证据重新回答。
>
> 检索侧同时使用 BM25 和 Dense Retrieval，通过 RRF 融合候选；BGE Cross-Encoder 只作为可选的 Top-N 候选块重排器，不做全库检索，也不在 CPU 环境默认加载。文档解析支持 PDF、DOCX、XLSX、Markdown 和 TXT，并尽量保留页码、章节和标题层级。SQLite 保存文档块、会话和运行轨迹，独立 MCP Server 则把检索、引用回查和安全计算开放给外部 Host。
>
> 为了避免只讲功能，我还使用受控回归集、SciFact 和 QASPER 分层评测。QASPER 60 条全文证据查询中，可选 GPU BGE 重排将 evidence-recall proxy@4 从 45.0% 提升到 55.0%，暖查询平均延迟从约 267 ms 增至 561 ms，说明精排能提高候选覆盖，但必须权衡延迟，因此没有把它设为 CPU 本地默认配置。

## 架构与执行路径

```text
Web UI / REST API ──> FastAPI ──> ResearchFlowService
                                     │
MCP Host ──stdio──> MCP Server ──────┤
                                     ├─ SQLite
                                     ├─ HybridRetriever
                                     ├─ LLMClient
                                     └─ LangGraph StateGraph

RAG:    route → rewrite → retrieve → answer → verify ─┬→ persist
                                                       ├→ retrieve（召回修复，至多一次）
                                                       └→ answer（引用修复，至多一次）
Tool:   route → tool → answer → verify → persist
Direct: route → answer → verify → persist
```

### 每层职责

- `main.py`：HTTP API、SSE 节点状态、静态网页和可选 API key 防护。
- `service.py`：组装数据库、检索器、模型客户端和状态图，对 FastAPI 与 MCP 提供统一业务能力。
- `graph.py`：定义 Agent 状态、节点、条件边、按失败原因分流的受控修复与运行事件。
- `ingestion.py`：解析多格式文档并保留页码、章节、标题层级等 metadata。
- `retrieval.py`：分块、BM25、Dense Retrieval、RRF 及检索策略切换。
- `reranking.py`：对第一阶段 Top-N 候选文本块执行可选 BGE Cross-Encoder 精排。
- `llm.py`：离线确定性回答与 OpenAI-compatible / DeepSeek 模型调用、Query Rewrite 和引用约束 Prompt。
- `db.py`：保存文档、chunks、会话、逐轮消息和运行轨迹。
- `mcp_server.py`：通过标准 MCP `stdio` 暴露检索、引用精确回查、安全计算和文档清单 Resource。

## 高频设计问题

### 1. 为什么用 LangGraph，不直接写顺序 Chain？

当前流程存在任务分支和失败修复：知识问题、计算问题、空知识库回答的路径不同；Verify 后还要根据失败原因回到 Retrieve 或 Answer。LangGraph 的价值是把状态、条件边和最大重试次数显式化，并让前端能够展示节点轨迹。它不是为了把简单调用包装成“复杂 Planner”。

### 2. `route` 为什么不叫 `plan`？

当前节点主要根据知识库状态和问题中是否存在算术表达式选择 `rag / tool / direct`，本质是规则路由器，不是能够分解开放任务的复杂 Planner。改名为 `route` 是为了避免过度宣称规划能力。

### 3. 为什么同时用 BM25 和 Dense Retrieval？

BM25 适合方法名、模型名、数据集等强词法信号；Dense Retrieval 适合表达不同但语义接近的问题。两者召回结果经 RRF 按排名融合，避免直接比较量纲不同的原始分数。评测也表明 Hybrid 不是任何语料上都必然优于 Lexical，应按语料分布和延迟预算选型。

### 4. 为什么不用 BGE 对全库检索？

BGE Reranker 是 Cross-Encoder，需要联合编码 `(query, passage)`，单次判断比双塔向量相似度昂贵。系统先用 BM25 + Dense 召回有界候选，再对 Top-N 候选块精排，控制计算量。

### 5. 为什么 BGE 不是 CPU 默认配置？

QASPER 固定评测表明 GPU BGE 能改善全文证据候选覆盖，但暖查询延迟也从约 267 ms 增至 561 ms；在本地 CPU 上模型加载和单次重排更慢。因此 `auto` 策略只在 CUDA 可用时自动加载，CPU 由用户明确启动。

### 6. Query Rewrite 如何使用多轮历史？

只读取最近经过验证的用户问题和 Assistant 回答，并限制轮数与总字符数，用于消解“它”“这个方法”等指代。历史对话只是改写上下文，当前事实证据仍必须来自本轮检索结果；限定文档范围的请求不会继承其他文档范围下的历史事实。

### 7. Verify 实际保证了什么？

它检查候选证据是否存在、模型是否显式报告证据不相关、答案是否包含引用编号，以及引用编号是否越界。它能够驱动检索修复或引用修复，但还不能严格证明每项答案陈述都被对应证据语义蕴含。更严格的 citation entailment / faithfulness 检查属于下一阶段工作。

### 8. 为什么最多重试一次？

一次受控修复可以覆盖常见的召回失败和引用格式失败，同时给延迟、成本和状态图递归深度设置明确上限。生产系统可以进一步按错误类型、模型成本和 SLA 配置不同预算，但不能无界循环。

### 9. MCP 在项目里解决什么问题？

MCP 不替代 Agent 或 LangGraph。它是外部适配层，让 Claude Desktop、Cursor 或其他 MCP Host 可以通过标准协议发现并调用 ResearchFlow 的检索、引用回查和计算能力。当前实现使用本地 `stdio`；没有把 Streamable HTTP 说成已实现能力。

### 10. 为什么同时提供 `search_research_documents` 和 `get_citation_context`？

Search 用于发现候选并返回摘要和 `chunk_id`；Get Citation 按 ID 精确回查已经引用的证据，避免为了核验再次执行检索而产生结果漂移。两者内容存在部分重叠，但职责分别是“发现”和“稳定回查”。

### 11. 为什么使用 SQLite？

V1 面向本地单用户、小规模科研资料，SQLite 能降低部署成本，同时保存文档、chunks、会话和运行轨迹。它不是面向高并发生产环境的最终方案；规模扩大后应迁移到 PostgreSQL / 专用向量数据库，并引入异步任务队列。

### 12. 测试和评测有什么区别？

- 61 项自动化测试验证 API、解析、状态流、检索、SSE、MCP 等代码行为是否符合预期。
- 8 条受控回归样例验证项目链路和回归稳定性，不代表真实业务准确率。
- 论文小语料、SciFact 和 QASPER 评测用于观察不同检索策略在不同证据粒度上的召回、排序和延迟。
- 这些指标不能合并成一个笼统的“系统准确率”。

## QASPER 评测话术

> SciFact 主要是摘要级检索，不能证明系统能够处理论文全文，所以我又在 QASPER 上固定抽取 60 条全文证据查询。第一阶段 Hybrid RRF 的 evidence-recall proxy@4 是 45.0%；在同一协议下，用 NVIDIA RTX 4090D 对 Hybrid Top-20 候选块执行 `BAAI/bge-reranker-v2-m3` 重排后提升到 55.0%，MRR@4 从 0.3111 提升到 0.3722。暖查询平均延迟由 267.18 ms 增至 560.86 ms。这个实验支持“BGE 作为可选 GPU 二阶段精排”的设计，但样本量只有 60，指标也是证据召回代理，不能描述成企业问答准确率。

### 为什么使用 `evidence-recall proxy`？

QASPER 的证据标注与本项目的 chunk 边界不完全一致，因此通过归一化文本重叠判断返回 chunk 是否覆盖标注证据。它能够比较固定协议下的候选覆盖，但不是完整答案正确率，也不是官方排行榜指标。

## 项目局限与演进方向

- PDF 依赖文本层；扫描件、复杂双栏和图表文字仍需要 OCR 与版面分析。
- SQLite 与应用内向量扫描适合本地小规模语料，不适合高并发或海量文档。
- Verify 主要保证引用存在与编号有效，尚未完成严格的逐声明语义蕴含验证。
- 当前路由是可解释的规则路由，不是通用任务规划器。
- MCP 当前使用 `stdio`，没有实现远程 Streamable HTTP 鉴权和多租户隔离。
- 前端推送的是节点级 SSE 状态，不是模型 token 级流式输出。
- 项目支持可选真实 LLM，但离线回归测试使用确定性回答以保证可复现。

## 个人贡献的诚实表述

推荐说法：

> 这是我的个人开源学习与求职项目。我确定了科研文档可追溯问答的目标，并使用 AI 编程工具加速代码实现；随后围绕工作流边界、混合检索、Query Rewrite、验证分流、MCP、评测协议和文档持续审查、测试与迭代。我能够解释当前架构、关键实现、评测结果和已知局限，也会明确区分自己做出的设计判断与 AI 辅助生成的代码。

不推荐说法：

- “所有代码都是我逐行手写的。”
- “这是一个通用 Deep Research Agent。”
- “Verify 已经消除了幻觉。”
- “RRF 一定比 BM25 和 Dense 更好。”
- “QASPER 问答准确率提高了 10%。”
- “项目已经实现远程 MCP Streamable HTTP。”
- “BGE 默认对全库进行检索。”

## 投递前口头自测

不看文档完成以下任务：

1. 用 30 秒和 90 秒各介绍一次项目。
2. 画出三条路由和 Verify 后的两种修复路径。
3. 解释 BM25、Dense、RRF、BGE 的职责和成本差异。
4. 解释 QASPER 指标、实验结果、延迟代价和不能外推的边界。
5. 演示导入文档、提问、查看引用与运行轨迹，并运行测试。
6. 指出至少三个局限和对应的生产演进方案。

能独立完成其中五项，即可支撑第一轮 Agent/RAG 项目面试；剩余问题根据真实面试反馈继续补齐。
