# Agent 相关框架与组件边界

目标：避免把框架、协议、模型、检索算法和低代码平台混为一谈。两天内只要求会比较和选型，不要求把相邻框架全部写一遍 demo。

## 1. 先按层分类

```mermaid
flowchart TB
    APP[Agent application]
    API[Service layer: FastAPI]
    ORCH[Orchestration: LangGraph]
    COMPONENT[Components: LangChain or custom code]
    DATA[RAG data framework: LlamaIndex or custom pipeline]
    PROTOCOL[Tool protocol: MCP]
    PLATFORM[Low-code platform: Dify or Coze]
    MODEL[LLM and embedding models]
    STORE[SQLite or vector database]

    APP --> API
    APP --> ORCH
    ORCH --> COMPONENT
    ORCH --> DATA
    ORCH --> PROTOCOL
    PLATFORM --> ORCH
    PLATFORM --> COMPONENT
    COMPONENT --> MODEL
    DATA --> MODEL
    DATA --> STORE
```

一个产品可以同时使用多个层，不能简单问“LangGraph 和 MCP 谁更好”。它们解决的问题不同。

## 2. 核心对比

| 名称 | 类别 | 主要解决的问题 | ResearchFlow 当前使用 |
| --- | --- | --- | --- |
| FastAPI | Web/API 框架 | HTTP 边界、校验、依赖、OpenAPI | 是，直接依赖 |
| LangGraph | 状态工作流/Agent 编排 | state、node、edge、循环、持久化/HITL 能力 | 是，直接依赖 |
| LangChain | LLM 应用组件库 | model、prompt、tool、retriever、chain 等抽象 | 否，主链路使用自定义组件 |
| LlamaIndex | 数据/RAG 框架 | 文档 ingestion、index、retriever、query engine | 否，当前自研轻量链路 |
| MCP | 工具与上下文协议 | client/server 间发现和调用工具/资源 | 否，当前工具是本地函数 |
| Dify/Coze | 低代码应用平台 | 快速编排、运营、发布和集成 | 否，ResearchFlow 是代码工程 |
| FastEmbed | 推理库 | CPU embedding/reranker 推理 | 是，可选 embedding backend |
| vLLM | LLM 推理服务/引擎 | 高吞吐模型 serving | 否，调用外部 LLM API |

## 3. LangGraph 与 LangChain

```mermaid
flowchart TB
    LANGGRAPH[LangGraph]
    CONTROL[State and control flow]
    LANGCHAIN[LangChain]
    COMPONENTS[Model tool retriever abstractions]
    CUSTOM[Custom Python components]
    APP[Agent application]

    LANGGRAPH --> CONTROL --> APP
    LANGCHAIN --> COMPONENTS --> APP
    CUSTOM --> APP
    COMPONENTS -. optional inside nodes .-> CONTROL
    CUSTOM -. current ResearchFlow choice .-> CONTROL
```

结论：LangGraph 可以在节点里使用 LangChain 组件，也可以直接调用自定义 Python。ResearchFlow 选择后者，因此无需为了“主流”强行加入 LangChain。

学习要求：

- LangGraph：能写 state、node、conditional edge、有限循环和测试；
- LangChain：知道 model/tool/retriever/runnable 等常见抽象，能阅读岗位代码；
- 不需要两天内同时重写成两套框架。

## 4. LangGraph 与 LlamaIndex

LangGraph关注控制流；LlamaIndex更关注数据导入、索引、retrieval/query engine。复杂 Agent 可以用 LangGraph 编排，把 LlamaIndex retriever 放进 retrieve 节点。

ResearchFlow 已经有解析、分块、embedding、BM25、RRF 和引用元数据，自研实现便于学习和解释；如果要快速接入多数据源、成熟索引或复杂 query engine，才评估 LlamaIndex。

## 5. LangGraph 与 MCP

MCP 是通信协议，不是 Agent planner。

```mermaid
sequenceDiagram
    participant G as LangGraph node
    participant C as MCP client
    participant S as MCP server
    participant T as External tool or data
    G->>C: request a tool call
    C->>S: protocol message
    S->>T: execute or read
    T-->>S: result
    S-->>C: structured response
    C-->>G: tool result
```

当前 `calculate()` 是进程内本地工具，不需要 MCP。只有当工具跨进程/跨语言、需要标准发现和权限边界时，MCP 才有明显价值。

## 6. 代码框架与 Dify/Coze

| 维度 | 代码框架 | Dify/Coze |
| --- | --- | --- |
| 开发速度 | 初期较慢 | 原型快 |
| 控制力 | 高 | 受平台节点限制 |
| 可测试性 | 可做单元/集成/CI | 依平台导出和测试能力 |
| 可观测性 | 自己设计，灵活 | 平台内置，方便但有边界 |
| 定制与性能 | 高 | 深度定制受限 |
| 适合场景 | 核心系统、复杂逻辑、工程岗位作品 | 需求验证、运营工作流、快速客户 Demo |

你已有 Dify/Coze 原型经验，不需要否定它；ResearchFlow 的作用是补充代码级 Agent 工程证据。

## 7. 是否需要 vLLM

当前项目调用 DeepSeek-compatible API，没有在本地部署基座模型，因此 vLLM 不在运行链路中。只有自托管开源 LLM、需要 batching/KV cache/吞吐优化时才引入。Agent 工程面试知道“API provider 与 self-host serving 的边界”即可。

## 8. 选型问题树

```mermaid
flowchart TB
    NEED[What problem must be solved?]
    HTTP{Expose HTTP service?}
    FLOW{Need explicit state branches or loops?}
    DATA{Need mature data and index abstractions?}
    REMOTE{Need standard remote tools?}
    PROTO{Need a fast business prototype?}

    NEED --> HTTP
    HTTP -->|yes| FASTAPI[FastAPI]
    HTTP -->|no| FLOW
    FASTAPI --> FLOW
    FLOW -->|yes| LG[LangGraph]
    FLOW -->|no| PY[Plain Python may be enough]
    LG --> DATA
    PY --> DATA
    DATA -->|yes| LI[LlamaIndex or LangChain components]
    DATA -->|custom or small| CUSTOM[Custom RAG]
    LI --> REMOTE
    CUSTOM --> REMOTE
    REMOTE -->|yes| MCP[MCP]
    REMOTE -->|no| PROTO
    MCP --> PROTO
    PROTO -->|yes| LOW[Dify or Coze for validation]
    PROTO -->|no| CODE[Continue code engineering]
```

## 9. 两天内学到什么程度

- 深入：FastAPI、LangGraph、SQLite、自研 RAG；
- 能使用和排错：Pydantic、pytest、Docker、FastEmbed、HTTPX；
- 能比较和阅读：LangChain、LlamaIndex、MCP、Dify/Coze、vLLM；
- 暂不做：为了简历标签把同一项目重写到多个框架。

## 10. 面试表达

> ResearchFlow 使用 FastAPI 提供 API，以 LangGraph 显式编排状态和条件重试；模型、检索和工具组件采用轻量自定义实现，没有为了堆框架强依赖 LangChain。MCP 属于远程工具协议，当前本地计算工具不需要它；如果后续把文献搜索、实验平台或企业数据源拆成独立服务，再把 MCP client 放进工具节点。Dify/Coze 适合快速原型，而这个项目重点证明代码级可测试、可部署和可观测能力。

## 官方入口

- [LangGraph documentation](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangChain documentation](https://docs.langchain.com/oss/python/langchain/overview)
- [LlamaIndex documentation](https://docs.llamaindex.ai/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [FastAPI documentation](https://fastapi.tiangolo.com/)
