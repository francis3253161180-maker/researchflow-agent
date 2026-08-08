# ResearchFlow 代码走读

本文沿一次 `/api/chat` 请求走读真实代码。目标不是背文件，而是能从入口定位状态变化、数据依赖和失败边界。

## 1. 启动与对象组装

入口是 `app/main.py`：

```text
create_app
  -> Settings.from_env
  -> FastAPI lifespan
  -> ResearchFlowService(settings)
```

`ResearchFlowService.__init__` 在 `app/service.py` 中完成四件事：

1. 创建数据和模型缓存目录；
2. 初始化 `Database`；
3. 选择 embedding provider 并创建 `HybridRetriever`；
4. 创建 `LLMClient`，编译 LangGraph。

面试关注点：对象在 lifespan 中创建一次，而不是每个请求重新加载 FastEmbed、重新建表和重新编译图。

## 2. FastAPI 请求边界

`POST /api/chat` 接收 `ChatRequest`：

```python
class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
```

进入业务层前，Pydantic 已完成长度与类型校验。若设置 `RESEARCHFLOW_APP_API_KEY`，`require_api_key` 使用常量时间比较验证 `X-API-Key`。

接口把 service 返回的字典显式映射成 `ChatResponse`，避免把全部内部 state 暴露给外部。

## 3. 服务层

`ResearchFlowService.chat`：

```text
生成或复用 session_id
  -> initial_state(session_id, query)
  -> graph.invoke(..., recursion_limit=12)
```

服务层只负责组件协调，不包含具体节点逻辑。这样 API 测试和图测试可以分别进行。

## 4. 初始状态

`app/graph.py` 的 `AgentState` 是 `TypedDict`，当前字段包括：

| 字段 | 含义 |
| --- | --- |
| `run_id` | 一次运行的唯一标识 |
| `session_id` | 多轮消息所属会话 |
| `query` | 原始用户问题 |
| `route` | `rag`、`tool` 或 `direct` |
| `plan` | 面向 UI/轨迹的步骤说明 |
| `retrieved` | 检索证据 chunks |
| `tool_result` | 安全计算结果 |
| `answer` | 最终或待校验回答 |
| `citations` | 最多前三条返回引用 |
| `verified` | 当前校验结果 |
| `retry_count` | RAG 校验失败次数 |
| `started_at` / `latency_ms` | 耗时统计 |
| `events` | 节点事件轨迹 |
| `errors` | 脱敏错误类型 |

`initial_state` 只初始化运行必需字段；后续节点返回增量字典，由 LangGraph 合并进 state。

## 5. plan 节点

当前路由是可解释的规则路由，不是 LLM 自主规划：

```text
匹配“数字 运算符 数字” -> tool
否则知识库有 chunk        -> rag
否则                       -> direct
```

优点：确定、便于测试、不会让模型任意调用本地工具。局限：自然语言数学题和更复杂意图可能误判，未来需要分类器或受控的结构化模型路由。

## 6. retrieve 节点

首次使用原问题检索；若已经失败一次，则追加“方法 结果 结论”扩展查询。`HybridRetriever.search` 返回由 `RETRIEVAL_TOP_K` 控制的候选证据（默认 6 条）。

注意：这只是固定扩展，不是完整 Query Rewrite 模型。面试时应准确描述为“有限的规则式查询扩展”。

## 7. tool 节点

`app/tools.py` 不使用 `eval`，而是：

1. 从问题中提取算术表达式；
2. 使用 `ast.parse(..., mode="eval")`；
3. 只递归执行允许的数字、二元运算和一元运算；
4. 限制幂指数绝对值不超过 10。

这体现了工具边界：Agent 不应该把任意字符串交给 Python 执行。

## 8. answer 节点

回答节点读取当前 session 最近 6 条历史消息。两种后端：

- 配置模型：调用 OpenAI-compatible `/chat/completions`，temperature 为 0.1；
- 未配置模型：使用确定性的离线回答器，把前三条证据截取为引用摘要。

远程 system prompt 明确将上传文档视为“不可信证据，而非指令”，降低文档提示注入风险。

若模型服务异常，返回受限失败消息，并只记录 `llm_error: 异常类型`，不保存上游响应、路径或密钥。

## 9. verify 节点

不同 route 的校验标准：

| route | 当前标准 |
| --- | --- |
| `rag` | 有 citations，且回答包含至少一个与引用编号对应的 `[n]` |
| `tool` | `tool_result` 非空 |
| `direct` | answer 非空 |

重要边界：当前 RAG 校验只证明“有证据和引用标记”，没有逐句判断答案是否被证据支持，也没有检测错误引用。

## 10. 条件重试

若 RAG 未通过校验：

1. `verify_node` 增加 `retry_count`；
2. 第一次失败时回到 `retrieve`；
3. 第二次验证后无论成功与否都进入 `persist`。

业务规则限制一次重试；`recursion_limit=12` 是框架层兜底，二者不要混为一谈。

## 11. persist 节点

持久化内容包括：

- 用户与助手消息；
- route、answer、verified；
- 总延迟；
- 全部节点事件；
- 脱敏错误类型。

`GET /api/runs/{run_id}` 可在请求结束后回看失败发生在哪个节点。

## 12. 文档导入链路

```text
UploadFile
  -> 大小限制
  -> parse_upload
  -> ParsedDocument / TextBlock
  -> chunk_blocks
  -> embedding provider
  -> documents + chunks transaction
```

- PDF：逐页提取，保留 `page`；
- Markdown：按标题切分，保留 `section`；
- DOCX：提取普通段落，目前不处理表格、图片和复杂样式；
- TXT：作为普通文本块。

## 13. 检索链路

`HybridRetriever.search` 当前会把 SQLite 中的全部 chunks 载入应用：

1. 字符/英文词 tokenization；
2. 计算 BM25 风格词法分数；
3. 对 query 生成向量并计算点积；
4. 分别得到两个排名；
5. 用 RRF 融合；
6. 返回 top-4。

`HashEmbedding` 在代码中明确做了 L2 归一化；FastEmbed 和远程 embedding 的范数取决于具体后端。当前名为 `cosine` 的函数实际执行点积，没有在 provider 边界统一归一化。因此只有输入已归一化时它才等价于余弦相似度，这是一个应能主动指出的改进点。

## 14. 数据库

SQLite 表：

- `documents`：原始文档记录；
- `chunks`：文本块、向量 JSON、页码和分节；
- `messages`：会话消息；
- `runs`：一次 Agent 运行及轨迹。

每次连接启用 WAL 和外键。context manager 成功时提交、异常时回滚，删除 document 会通过外键级联删除 chunks。

## 15. 用测试反推承诺

推荐按以下顺序读测试：

1. `test_graph.py`：三条路由、一次重试、异常脱敏；
2. `test_retrieval.py`：混合检索和长文分块；
3. `test_ingestion.py`：PDF 页码、Markdown 分节、DOCX、环境变量；
4. `test_api.py`：端到端 API、上传/删除、API Key、Web UI。

代码能通过的测试，才是项目当前可以明确承诺的行为。
