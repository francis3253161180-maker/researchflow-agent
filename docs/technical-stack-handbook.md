# 技术栈核心与高频知识点

本文按“核心原理 -> 本项目映射 -> 高频问题 -> 掌握标准”整理。标记说明：

- **已实现**：仓库中存在代码和测试；
- **应掌握**：面试需要解释，但不代表项目已经实现；
- **下一步**：生产化或后续迭代方向，不能写成当前能力。

## 两天冲刺的阅读优先级

本手册是查询资料，不是必须线性读完的教材。结合 [两天主计划](weekend-study-guide.md) 和 [文件地图](file-map.md)：

如果某一节还没有建立心智模型，先看 [快速入门手册](quickstarts/README.md)；如果概念零散，使用 [知识关系图全集](knowledge-maps.md) 连接各组件。

- **周六 P0**：第 2–5 节（FastAPI、LangGraph、Agent、RAG）和第 14 节（LLM API/Prompt）；
- **周日 P0**：第 6–10 节（BM25、Embedding、RRF、引用评测、SQLite）和第 12 节中的测试部分；
- **P1 查缺**：第 1、11、13、15 节（Python、Docker、HTTP、配置/可观测性）；
- **投递后继续**：第 16 节 PyTorch/Transformers。它是算法岗位基础，但不是 ResearchFlow 当前运行依赖，不应挤占这两天掌握项目闭环的时间。

每节达到“能用自己的话解释核心原理、指出本项目代码位置、说出一个边界”即可，不需要背定义。

## 1. Python

### 核心

- 可变对象与不可变对象：list/dict 可变，str/tuple/int 通常不可变；默认参数不要使用可变对象。
- 引用语义：赋值绑定对象，浅拷贝只复制外层容器，深拷贝递归复制。
- 迭代器与生成器：迭代器实现 `__iter__`/`__next__`；生成器以 `yield` 延迟产生数据，适合流式或大数据处理。
- 装饰器：函数也是对象；装饰器用于横切逻辑，如鉴权、计时、重试，但应避免隐藏关键业务状态。
- 上下文管理器：`with` 保证资源释放；项目数据库连接以 `@contextmanager` 管理提交、回滚和关闭。
- 类型系统：`dataclass` 适合内部数据载体，Pydantic 适合外部输入输出验证，`Protocol` 适合定义可替换接口。
- 异常边界：在能处理问题的层捕获；不要裸 `except`，不要把密钥、请求体和上游响应直接暴露给客户端。
- 同步与异步：`async` 适合等待网络/文件 I/O，但调用同步阻塞库仍会阻塞 event loop。

### 项目映射

- `AgentState(TypedDict)`：图状态结构；
- `ParsedDocument`、`TextBlock`、`SearchResult`：dataclass 数据载体；
- `EmbeddingProvider(Protocol)`：Hash、FastEmbed、远程 embedding 的共同接口；
- `Database.connect()`：上下文管理器与事务；
- FastAPI 上传接口是 async，但 SQLite、检索和 httpx 当前使用同步调用。

### 高频问法

**为什么内部不用 Pydantic 代替全部 dataclass？** 业务内部对象不一定需要运行时校验；Pydantic 更适合不可信边界。内部 dataclass 更轻，边界处 Pydantic 更明确。

**async 接口里调用同步代码有什么问题？** CPU 计算或同步网络/数据库调用会占用事件循环线程。生产环境可使用异步客户端、线程池、任务队列或拆分后台任务。

## 2. FastAPI 与 Pydantic

### 核心

- FastAPI 根据类型注解和 Pydantic 模型完成解析、校验和 OpenAPI 生成。
- `Depends` 用于依赖注入；适合鉴权、数据库 session、公共参数。
- lifespan 负责应用级资源初始化/释放，避免每个请求重复加载模型。
- `response_model` 同时提供文档、序列化和字段约束。
- `UploadFile` 使用临时文件/流接口；仍需在服务端限制大小、类型并关闭句柄。
- HTTP 状态码：401 未认证，403 已认证但无权限，404 不存在，413 过大，422 参数或业务格式不可处理。
- 幂等性：GET 应只读；DELETE 通常幂等；POST 默认不幂等。

### 项目映射（已实现）

- lifespan 初始化 `ResearchFlowService`；
- `/api/*` 可选 `X-API-Key`；`/health` 保持开放；
- 上传大小默认 15 MiB；
- Pydantic 限制 query 1–4000 字符、title 1–200 字符；
- API 层只负责边界，业务逻辑进入 service 和 graph。

### 高频问法

**为什么 API Key 用 `compare_digest`？** 它避免普通字符串比较的明显时序差异，但 API Key 本身仍只适合简单服务；生产系统还需要 HTTPS、密钥轮换、用户身份、权限和审计。

**为什么 `/health` 不鉴权？** 容器编排和负载均衡器需要探针；健康接口不应返回敏感配置。

**如何处理耗时文档导入？** 当前小文件同步完成。大文件应进入任务队列，返回 job id，再查询进度和结果。

## 3. LangGraph

### 核心

- **State**：节点共享的数据；
- **Node**：读取 state 并返回状态增量的函数；
- **Edge**：固定状态转移；
- **Conditional Edge**：根据 state 选择下一节点；
- **Compile**：把图定义变成可执行 runnable；
- **Recursion limit**：防止图循环无限执行；
- **Checkpointer**：保存图执行状态以恢复、暂停或人工审批（当前未实现）。

### 项目映射（已实现）

```text
START -> plan
plan -> rewrite -> retrieve | tool | answer
retrieve -> answer
tool -> answer
answer -> verify
verify -> retrieve | persist
persist -> END
```

图有六个节点：`plan`、`retrieve`、`tool`、`answer`、`verify`、`persist`。路由为规则式，RAG 只允许一次业务重试，框架 recursion limit 为 12。

### 高频问法

**为什么不用普通 if/else？** 小系统当然可以。LangGraph 的价值在于显式状态、条件边、轨迹、可测试循环，以及以后接 checkpointer/人工审批。使用框架必须说明它解决了什么复杂度。

**工作流还是 Agent？** 当前是受控 Agent 工作流：系统会根据问题选择检索、工具或直接回答，并有状态和有限反馈循环；但不是开放式自主规划、多 Agent 或强化学习 Agent。

**LangGraph 与 LangChain 什么关系？** LangChain提供模型、prompt、retriever、tool等组件抽象；LangGraph聚焦有状态、可循环的编排。可以单独用 LangGraph，也可以组合 LangChain 组件。

## 4. Agent 与工具调用

### 核心（应掌握）

- ReAct：交替进行 reasoning 与 action，再观察工具结果；生产系统不应直接暴露隐藏推理文本。
- Tool Calling：模型输出结构化工具名和参数，应用负责校验、授权和执行。
- MCP：标准化模型客户端与工具/资源服务之间的发现和调用协议；不等于 Agent 框架。
- Planning：任务拆解；计划可以由规则、模型或混合方法生成。
- Memory：短期消息、摘要、长期语义记忆；必须考虑召回、隐私和过期。
- Guardrail：工具白名单、参数 schema、权限、超时、幂等、结果大小、循环上限。

### 项目映射

- 当前 `plan_node` 不是 LLM planner；
- calculator 是本地受限工具，不使用任意 `eval`；
- 记忆为 SQLite 最近 6 条消息；
- 上传文档作为不可信 evidence，不允许改变 system behavior；
- 工具异常进入 state，不让整个请求直接崩溃。

### 高频问法

**Function Calling 与 MCP 区别？** Function Calling 是模型输出工具调用结构的能力；MCP 解决工具和资源如何以统一协议暴露、发现和调用。二者可以组合。

**如何避免 Agent 无限循环？** 业务级重试次数、图 recursion limit、工具调用预算、总时限和幂等设计共同限制。

## 5. RAG 总流程

### 核心

```text
数据解析 -> 清洗 -> 分块 -> embedding/index
查询理解 -> 召回 -> 融合/精排 -> 上下文构造
生成 -> 引用/忠实度校验 -> 评测与监控
```

RAG 的质量瓶颈可能位于解析、切块、召回、排序、上下文组织或生成，不应只调 prompt。

### 分块

- chunk 太小：语义割裂、上下文不足；
- chunk 太大：噪声增加、embedding 被平均、token 成本变高；
- overlap：缓解边界信息丢失，但增加索引和重复证据；
- 结构化分块优先保留页码、章节、标题、表格等元数据。

当前项目默认最多 620 字符、重叠 80；按空行优先聚合，长段落再滑窗。

### 高频问法

**如何定位 RAG 问题？** 先检查解析文本，再测 Recall@K；召回正确再看排序；证据正确再看 prompt/回答；最后看引用忠实度。

**GraphRAG 是不是一定优于普通 RAG？** 不是。GraphRAG适合实体关系、多跳和全局主题，但构图、更新和查询成本更高；应由任务和评测驱动。

## 6. BM25 风格词法检索

### 核心

- TF 表示词在文档中的频率；
- IDF 降低常见词权重；
- 文档长度归一化避免长文因词多天然占优；
- `k1` 控制 TF 饱和，`b` 控制长度归一化程度。

当前实现 `k1=1.5`、`b=0.75`，tokenizer 将英文单词/数字保留为 token，将中文按单字切分。它是 BM25 风格实现，不是 Elasticsearch 完整分析链。

### 优势与局限

- 优势：专业术语、型号、缩写、精确字符串稳定，可解释；
- 局限：同义表达弱，中文单字切分缺少词级语义，全部 chunks 每次重算不适合大规模。

## 7. Embedding 与向量检索

### 核心

- Bi-encoder 分别编码 query/document，适合大规模召回；
- Cross-encoder 联合编码 query-document，效果通常更好但成本高，适合 rerank top-N；
- cosine 比较方向，点积还受长度影响；向量 L2 归一化后点积才等价于 cosine；项目的 `cosine` 函数当前实际计算点积；
- query 和 passage 可能使用不同前缀或编码接口；FastEmbed 当前分别使用 `query_embed` 和 `passage_embed`。

### 项目三种后端

| 后端 | 用途 | 边界 |
| --- | --- | --- |
| HashEmbedding | 离线确定性测试 | 不是真实语义模型 |
| FastEmbed | CPU 语义检索 | 首次需下载；用于第一阶段多语种向量召回 |
| BGE reranker | 分片重排 | `auto` 在 CUDA 自动启动、CPU 可手动启动；`bge` 可按设备配置强制启动；不参与整篇文档排序 |
| OpenAI-compatible embedding | 远程服务 | 依赖网络、成本和密钥 |

### 高频问法

**Embedding/Reranker需要GPU吗？** FastEmbed embedding 不需要 GPU，可在 CPU/ONNX 上运行。BGE reranker 同时兼容 CPU 与 CUDA：`auto` 会优先自动使用 CUDA；CPU 默认不预加载，但可从网页顶部按钮手动启动。若预先知道需要它，也可设置 `RERANKER_PROVIDER=bge` 与 `RERANKER_DEVICE=cpu/cuda`。CPU 延迟明显更高。

**为什么当前不用向量数据库？** V1 文档量小，应用内扫描更可理解和可测试；规模增长后再迁移 pgvector/Qdrant/Milvus 等。

## 8. RRF 与 Reranker

### RRF

RRF 使用排名而不是原始分数：

```text
score(d) = Σ 1 / (k + rank_i(d))
```

项目中 `k=60`，融合词法和向量两个排名。它避免校准异构分数，但忽略原始分数间隔。

### Reranker（已实现，可选）

典型流程：BM25/vector 召回几十条，再用 Cross-encoder 精排到 top-3/top-5。是否加入必须用 Recall@K、MRR、nDCG和端到端延迟验证。

### 高频问法

**为什么不是加权分数相加？** BM25 和 cosine 尺度不同且随语料变化，需要归一化/校准；RRF简单稳健，适合 V1。

## 9. 引用与评测

### 当前实现

- citation 保存 chunk、文档、页码/分节、分数；
- RAG verified 要求有 citation 且回答出现至少一个 `[n]`；
- 受控回归集测 retrieval hit@4、answer hit、citation rate、verified rate 和延迟。

### 当前没有实现

- 逐句 attribution；
- entailment/faithfulness 判断；
- 人工标注真实语料；
- Recall@K、MRR、nDCG 的正式数据集报告；
- LLM-as-a-Judge 的校准。

### 高频指标

- Recall@K：相关文档是否进入前 K；
- Precision@K：前 K 中相关比例；
- MRR：第一个相关结果排名的倒数；
- nDCG：考虑多级相关性和位置折损；
- Faithfulness：回答主张是否被证据支持；
- Answer relevance：回答是否解决问题。

## 10. SQLite 与 SQL

### 核心

- 主键保证唯一；外键维护关系；索引加速查询但增加写成本；
- 事务满足原子性，一组操作成功则提交，异常则回滚；
- WAL 允许读写更好并发，但 SQLite 仍是单机文件数据库；
- 参数化查询防 SQL 注入；不要拼接不可信值。

### 项目映射

- 每次连接启用 WAL、foreign keys；
- documents 删除级联 chunks；
- sessions 保存会话标题与时间，runs 通过 session_id 保存每轮回答、citations、thinking mode 与轨迹；
- messages 对 `(session_id, id)` 建索引；
- embedding 以 JSON 存储，每次检索加载全部 chunks；
- schema 使用 `_ensure_column` 做轻量兼容，不等于正式迁移系统。

### 高频问法

**SQLite 什么时候不够？** 多实例部署、持续高并发写、复杂权限/查询、大规模向量检索时，应迁移 PostgreSQL/专用向量库。

## 11. Docker 与 Compose

### 核心

- Image 是只读模板，Container 是运行实例；
- Layer cache 受 Dockerfile 指令顺序影响；
- Volume 持久化数据，Network 连接服务；
- `EXPOSE` 是元数据，真正映射由 `ports` 完成；
- 容器应通过环境变量注入配置，不把密钥写进镜像。

### 项目映射

- 基础镜像 `python:3.12-slim`；
- 数据库路径固定到 `/app/data/researchflow.db`；
- Compose 把 `researchflow-data` volume 挂到 `/app/data`；
- 模型缓存和数据库一起持久化；
- 容器监听 `0.0.0.0:8000`。

### 生产差距

当前未包含非 root 用户、反向代理、TLS、资源限制、日志采集、多副本共享数据库和任务队列。

## 12. Git、GitHub Actions 与测试

### Git 高频点

- working tree、staging area、commit history；
- merge 保留分支结构，rebase 重写提交基线；
- 不要提交 `.env`、密钥、数据库、模型缓存；
- 小而明确的 commit 便于 review 和回滚。

### CI（已实现）

- push main 和针对 main 的 PR 触发；
- Python 3.12 安装项目并运行 pytest；
- 独立 job 从 Dockerfile 构建镜像；
- `permissions: contents: read` 遵循最小权限。

### 测试类型

- 单元测试：函数/模块隔离，如解析、检索；
- 集成测试：数据库、retriever、graph组合；
- API端到端：TestClient走真实路由和业务链；
- 回归评测：固定输入和预期能力，防行为退化；
- Mock/Fake：`NoCitationLLM`、`FailingLLM`控制异常路径。

### 高频问法

**51项测试说明什么？** 说明当前明确行为有自动回归保护；其中包含会话恢复、per-run thinking mode、首轮模型标题、Query Rewrite、结构化引用重试、MCP 工具与 stdio 协议调用，不说明生产高并发、真实性能或全部边界已覆盖。

**8/8说明什么？** 说明受控样例的检索、答案、引用和校验链路跑通；不代表企业准确率。

## 13. HTTP、REST 与 Web UI

### 核心

- HTTP请求由方法、URL、headers和body组成；响应包含状态码、headers和body；
- GET用于读取，POST创建/触发操作，DELETE删除；是否幂等取决于语义；
- JSON API要区分传输成功和业务成功，错误应使用稳定状态码与结构；
- CORS限制浏览器跨源访问，不是服务端鉴权；
- 流式输出通常用SSE或WebSocket，需处理断开、背压和部分结果；
- 文件上传使用 `multipart/form-data`，要限制大小、类型、文件名和解析资源。

### 项目映射

- Web UI是无构建步骤的静态HTML/JavaScript；
- 通过fetch调用REST API；
- FastAPI同时提供OpenAPI；
- 当前不是前后端分离部署，也未实现流式token输出。

### 高频问法

**为什么不用React/Vue？** V1重点是Agent/RAG后端闭环，原生页面足以演示上传、问答、引用和轨迹；复杂交互和团队协作增加时再采用前端框架。

**SSE与WebSocket怎么选？** 仅服务端向客户端持续推送token时SSE简单；需要双向实时交互时WebSocket更合适。

## 14. LLM API、Prompt 与上下文

### 核心

- OpenAI-compatible通常通过 `/chat/completions` 接收model、messages、temperature和token上限；
- system约束角色和安全边界，user承载问题，历史消息提供短期记忆；
- temperature低只能减少随机性，不能保证事实正确；
- 上下文窗口不是越满越好，证据噪声会降低回答质量；
- 超时、限流、重试、幂等、成本和敏感信息是服务调用高频问题。

### 项目映射

- DeepSeek key存在时默认配置OpenAI-compatible地址和模型；
- temperature为0.1，max_tokens为1200，timeout为60秒；
- 只注入最近6条会话消息；
- 上下文使用当前 retrieval top-k 的检索结果，并返回同一批 citations，避免回答编号指向前端未展示的证据；
- 网页可为单轮选择 `thinking=enabled/disabled`，该值覆盖服务默认配置但不改变后续轮次；
- 模型未配置时走确定性offline fallback。

### 高频问法

**为什么不能对模型请求盲目自动重试？** 非幂等生成可能重复扣费或产生不同结果；应区分网络错误、429、5xx和业务失败，设置退避、总预算和request id。

**如何控制上下文污染？** 文档与系统指令分层、限制证据数量和长度、保留来源、拒绝执行文档内命令，并对高风险场景增加输出校验。

## 15. Linux、配置与可观测性

### 核心

- 进程、端口、文件权限、环境变量、日志和信号是部署排查基础；
- `.env`适合本地开发，但不应提交；生产密钥使用secret manager或部署平台注入；
- 容器内路径和宿主路径不同，volume决定数据是否持久化；
- 日志应结构化并关联request/run id；指标、日志、trace解决不同问题。

### 项目映射

- 操作系统/容器环境变量优先于项目 `.env`；
- `run_id`关联一次Agent执行；
- SQLite保存节点events和latency，但当前还不是OpenTelemetry分布式追踪；
- `/health`用于探针，`/api/metrics`提供基础汇总。

### 高频排查命令

```bash
ps aux
ss -lntp
env
tail -f <log>
docker compose ps
docker compose logs -f
```

## 16. PyTorch 与 Transformers（简历高频，非项目运行依赖）

ResearchFlow的本地FastEmbed路径使用ONNX Runtime，不依赖PyTorch训练代码。下面内容来自简历技能要求，面试时不要说成项目内部实现。

### PyTorch 核心

- Tensor的shape、dtype、device；
- autograd计算图，`requires_grad`、forward、backward、optimizer step、zero_grad；
- train/eval模式对Dropout和BatchNorm的影响；
- `no_grad`与`inference_mode`；
- Dataset/DataLoader、batch、shuffle、collate；
- mixed precision、gradient accumulation、gradient checkpointing；
- 显存由参数、梯度、优化器状态、激活和缓存组成。

### Transformers 核心

- tokenizer把文本映射为token ids和attention mask；
- self-attention、causal mask、position encoding；
- 训练时并行处理序列，生成时自回归并复用KV Cache；
- Hugging Face常见对象：Config、Tokenizer、Model、Trainer/TrainingArguments；
- `from_pretrained`、device map、dtype、保存/加载checkpoint；
- LoRA/QLoRA冻结基座，仅训练低秩adapter；QLoRA通常以低比特基座权重配合高精度adapter训练。

### 高频问法

**为什么推理要 `model.eval()` 和 `inference_mode()`？** eval切换层行为；inference_mode关闭autograd相关开销，二者解决不同问题。

**KV Cache为什么省计算但占显存？** 它保存历史token的K/V，避免每步重复计算历史attention投影，但显存随层数、序列长度、batch、head维度和dtype增长。

## 17. 一页速记

面试前能够不看文档回答：

1. FastAPI 与 LangGraph 各负责什么？
2. 当前图有哪些 state 和 node？
3. 规则路由为什么既是优点也是局限？
4. BM25 和向量检索如何互补？
5. 为什么 RRF 不需要直接比较两个原始分数？
6. HashEmbedding 为什么不能代表真实语义效果？
7. 引用校验为什么不等于事实校验？
8. WAL、事务、外键和索引各解决什么？
9. 业务重试上限与 recursion limit 有什么区别？
10. 如果文档增长到十万篇，哪些模块要先改？
11. 同步接口、async接口和后台任务有什么边界？
12. FastEmbed、PyTorch与ONNX Runtime在本项目中的关系是什么？
