# ResearchFlow 知识关系图全集

这份文档不是新的教材，而是把各手册中的知识点连接起来。图全部采用纵向或小规模布局，便于 GitHub 页面显示。每张图下面给出对应学习文档。

## 0. 知识面覆盖索引

| 知识面 | 关系图 | 主文档 |
| --- | --- | --- |
| Python、分层与类型 | 图 1、图 2 | [技术栈手册](technical-stack-handbook.md) |
| FastAPI、Pydantic、HTTP | 图 2、图 3 | [FastAPI 快速入门](quickstarts/fastapi.md) |
| LangGraph、Agent、工具 | 图 4、图 5 | [LangGraph 快速入门](quickstarts/langgraph.md) |
| RAG、BM25、Embedding、RRF | 图 6、图 7 | [技术栈手册](technical-stack-handbook.md) |
| 引用、评测、失败分析 | 图 8 | [失败案例](failure-cases-and-debugging.md) |
| SQLite、SQL、记忆、轨迹 | 图 9 | [SQLite 快速入门](quickstarts/sqlite.md) |
| LLM API、Prompt、上下文 | 图 10 | [配套栈速查](quickstarts/supporting-stack.md) |
| 测试、Docker、CI、可观测性 | 图 11 | [配套栈速查](quickstarts/supporting-stack.md) |
| 相邻 Agent 框架 | 图 12 | [框架边界](framework-boundaries.md) |
| PyTorch、Transformers、PEFT | 图 13 | [技术栈手册](technical-stack-handbook.md) |
| 两天学习依赖顺序 | 图 14 | [周末冲刺](weekend-study-guide.md) |

## 图 1：项目技术分层

```mermaid
flowchart TB
    USER[User]
    UI[Web UI or REST client]
    API[FastAPI API boundary]
    SERVICE[ResearchFlowService]
    ORCH[LangGraph orchestration]
    CAP[Retrieval tools and LLM]
    INFRA[SQLite files and external APIs]
    VERIFY[pytest eval and CI]

    USER --> UI --> API --> SERVICE --> ORCH --> CAP --> INFRA
    VERIFY -. validates .-> API
    VERIFY -. validates .-> ORCH
    VERIFY -. validates .-> CAP
```

关系：上层负责业务语义，下层提供能力；测试横跨各层。不要让 FastAPI 路由直接承担所有检索和数据库逻辑。

## 图 2：Python 类型与边界

```mermaid
flowchart TB
    RAW[Untrusted JSON headers and files]
    PYD[Pydantic models]
    SERVICE[Typed service calls]
    STATE[TypedDict AgentState]
    VALUE[Dataclass value objects]
    PORT[Protocol interfaces]
    IMPL[Hash FastEmbed and remote implementations]

    RAW --> PYD --> SERVICE --> STATE
    SERVICE --> VALUE
    SERVICE --> PORT --> IMPL
```

关系：Pydantic守外部边界，TypedDict描述图状态，dataclass承载内部值，Protocol隔离可替换实现。

## 图 3：FastAPI 请求生命周期

```mermaid
flowchart TB
    REQUEST[HTTP request]
    SERVER[Uvicorn ASGI]
    ROUTE[FastAPI route match]
    DEP[Depends authentication]
    VALIDATE[Pydantic validation]
    HANDLER[Path operation]
    BUSINESS[Service and graph]
    RESPONSE[Response model]
    CLIENT[HTTP response]

    REQUEST --> SERVER --> ROUTE --> DEP --> VALIDATE --> HANDLER --> BUSINESS
    BUSINESS --> HANDLER --> RESPONSE --> SERVER --> CLIENT
```

## 图 4：LangGraph 组件关系

```mermaid
flowchart TB
    SCHEMA[State schema]
    NODE[Nodes return state updates]
    FIXED[Fixed edges]
    CONDITIONAL[Conditional edges]
    BUILDER[StateGraph builder]
    COMPILE[Compile]
    RUN[Invoke or stream]
    CHECKPOINT[Optional checkpointer]

    SCHEMA --> BUILDER
    NODE --> BUILDER
    FIXED --> BUILDER
    CONDITIONAL --> BUILDER
    BUILDER --> COMPILE --> RUN
    CHECKPOINT -. optional .-> COMPILE
```

## 图 5：ResearchFlow Agent 状态流

```mermaid
flowchart TB
    PLAN[Plan and route]
    RAG[Retrieve]
    TOOL[Safe tool]
    DIRECT[Direct constrained answer]
    ANSWER[Answer]
    VERIFY[Verify]
    RETRY[Expand query once]
    PERSIST[Persist messages and trace]

    PLAN -->|knowledge| RAG --> ANSWER
    PLAN -->|math| TOOL --> ANSWER
    PLAN -->|empty corpus| DIRECT --> ANSWER
    ANSWER --> VERIFY
    VERIFY -->|missing citation and first failure| RETRY --> RAG
    VERIFY -->|verified or stop| PERSIST
```

## 图 6：文档进入知识库

```mermaid
flowchart TB
    UPLOAD[PDF DOCX Markdown or TXT]
    VALIDATE[Type size and content validation]
    PARSE[Format parser]
    META[Page section and source metadata]
    CHUNK[Chunk with overlap]
    EMBED[Embedding provider]
    DOCS[(documents table)]
    CHUNKS[(chunks table)]

    UPLOAD --> VALIDATE --> PARSE --> META --> CHUNK --> EMBED
    PARSE --> DOCS
    EMBED --> CHUNKS
```

## 图 7：混合检索与融合

```mermaid
flowchart TB
    QUERY[Question]
    TOKEN[Lexical tokenization]
    QEMB[Query embedding]
    BM25[BM25-style ranking]
    VECTOR[Vector ranking]
    RRF[Reciprocal Rank Fusion]
    CANDIDATES[Top-N candidate chunks]
    RERANK[Optional CUDA BGE chunk reranker]
    TOPK[Top-K evidence]

    QUERY --> TOKEN --> BM25
    QUERY --> QEMB --> VECTOR
    BM25 --> RRF
    VECTOR --> RRF
    RRF --> CANDIDATES
    CANDIDATES -->|"BGE 已关闭"| TOPK
    CANDIDATES -->|"BGE 已启用（CPU 或 CUDA）"| RERANK
    RERANK --> TOPK
```

关系：BM25擅长精确术语，embedding擅长语义近似，RRF融合排名；BGE reranker 只对 Top-N 分片进行可选第二阶段重排。默认 `auto`：CUDA 自动加载；CPU 默认保留 RRF，但可从网页顶部按钮手动加载。`bge` 可按配置强制加载，`none` 强制关闭。

## 图 8：回答、引用与评测

```mermaid
flowchart TB
    EVIDENCE[Retrieved evidence]
    PROMPT[Constrained prompt]
    ANSWER[Generated answer]
    MARKER[Citation markers]
    VERIFY[Structural verifier]
    RETRIEVAL[Recall at K]
    RANKING[MRR and nDCG]
    FAITH[Citation faithfulness]
    TASK[Answer quality]

    EVIDENCE --> PROMPT --> ANSWER --> MARKER --> VERIFY
    EVIDENCE --> RETRIEVAL
    EVIDENCE --> RANKING
    ANSWER --> FAITH
    ANSWER --> TASK
```

关系：当前 verifier 只检查证据存在和引用标记，不等于语义忠实度；真实评测还需要人工标注 evidence/answer。

## 图 9：SQLite 数据与事务

```mermaid
flowchart TB
    SERVICE[Service or graph node]
    CONNECT[Context-managed connection]
    TX[Transaction]
    DOC[Documents]
    CHUNK[Chunks]
    MSG[Messages]
    RUN[Runs and events]
    WAL[WAL journal]
    COMMIT[Commit or rollback]

    SERVICE --> CONNECT --> TX
    TX --> DOC --> CHUNK
    TX --> MSG
    TX --> RUN
    TX --> WAL --> COMMIT
```

关系：消息/运行轨迹是业务持久化，不是 LangGraph checkpoint。

## 图 10：LLM、Prompt 与上下文

```mermaid
flowchart TB
    SYSTEM[System constraints]
    QUESTION[User question]
    MEMORY[Recent messages]
    CONTEXT[Retrieved evidence]
    TOOL[Tool result]
    BUDGET[Context budget]
    REQUEST[HTTPX request]
    MODEL[LLM provider]
    OUTPUT[Answer or safe fallback]

    SYSTEM --> BUDGET
    QUESTION --> BUDGET
    MEMORY --> BUDGET
    CONTEXT --> BUDGET
    TOOL --> BUDGET
    BUDGET --> REQUEST --> MODEL --> OUTPUT
```

关系：上下文由多种来源竞争 token 预算；文档内容是证据，不应覆盖系统约束。

## 图 11：测试、部署与可观测闭环

```mermaid
flowchart TB
    CODE[Code change]
    UNIT[Unit tests]
    INTEGRATION[Graph and API tests]
    EVAL[Bounded regression eval]
    IMAGE[Docker image build]
    CI[GitHub Actions status]
    RUN[Running service]
    TRACE[Run events metrics and logs]
    DEBUG[Failure analysis]

    CODE --> UNIT --> INTEGRATION --> EVAL --> IMAGE --> CI --> RUN --> TRACE --> DEBUG --> CODE
```

关系：CI通过说明自动检查通过，不等于生产部署或真实业务质量达标。

## 图 12：相邻框架和协议

```mermaid
flowchart TB
    PRODUCT[Agent product]
    FASTAPI[FastAPI service]
    LANGGRAPH[LangGraph orchestration]
    LC[LangChain components]
    LI[LlamaIndex data and RAG]
    MCP[MCP remote tools]
    LOWCODE[Dify or Coze prototype]
    MODEL[Model providers]

    PRODUCT --> FASTAPI
    PRODUCT --> LANGGRAPH
    LANGGRAPH -. optional components .-> LC
    LANGGRAPH -. optional retriever .-> LI
    LANGGRAPH -. optional tool protocol .-> MCP
    LOWCODE -. alternative prototype surface .-> PRODUCT
    LC --> MODEL
    LI --> MODEL
    LANGGRAPH --> MODEL
```

## 图 13：PyTorch、Transformers 与 PEFT 扩展知识

```mermaid
flowchart TB
    DATA[Dataset and tokenizer]
    TENSOR[Tensor and DataLoader]
    MODEL[Transformer model]
    FORWARD[Forward and loss]
    AUTOGRAD[Autograd backward]
    OPT[Optimizer and scheduler]
    PEFT[LoRA QLoRA or other PEFT]
    CHECKPOINT[Checkpoint and evaluation]
    SERVE[Inference or serving]

    DATA --> TENSOR --> MODEL --> FORWARD --> AUTOGRAD --> OPT
    PEFT --> MODEL
    OPT --> CHECKPOINT --> SERVE
```

关系：这是量化/PEFT算法简历的基础，不是 ResearchFlow 运行依赖；两天项目冲刺只做概念复习。

## 图 14：两天学习依赖图

```mermaid
flowchart TB
    RUN[Run the project]
    MAP[File map and architecture]
    API[FastAPI boundary]
    GRAPH[LangGraph state flow]
    RAG[RAG and retrieval]
    DB[SQLite persistence]
    TEST[Test and evaluation]
    FAIL[Failure debugging]
    CHANGE[One code change]
    SPEAK[Demo and interview explanation]

    RUN --> MAP
    MAP --> API --> GRAPH
    GRAPH --> RAG
    GRAPH --> DB
    RAG --> TEST
    DB --> TEST
    TEST --> FAIL --> CHANGE --> SPEAK
```

学习顺序不是按框架官网从头读，而是沿着真实请求链路逐层展开，最后用测试、故障和修改证明掌握。
