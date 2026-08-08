# ResearchFlow 文件地图与精读优先级

两天冲刺不需要给每个代码文件再配一篇长文，也不需要给每一行补注释。那会造成文档和代码重复，代码修改后还容易失真。更有效的方式是：**核心链路逐文件精读，外围文件知道职责和验证入口，生成物与样板文件只在需要时查看。**

优先级：

- **P0 精读**：能够关掉文档解释输入、输出、关键分支和失败方式；
- **P1 理解**：知道职责、配置和如何验证，面试追问时能定位；
- **P2 浏览**：知道存在和用途，不要求两天内记住实现细节。

## 一条请求的最短阅读路径

```text
app/main.py
  -> app/schemas.py
  -> app/service.py
  -> app/graph.py
       -> app/retrieval.py / app/tools.py
       -> app/llm.py
       -> app/db.py
  -> ChatResponse
```

文档导入是另一条链路：

```text
app/main.py -> app/service.py -> app/ingestion.py -> app/retrieval.py -> app/db.py
```

## 应用代码

| 优先级 | 文件 | 职责 | 两天内掌握到什么程度 |
| --- | --- | --- | --- |
| P0 | `app/main.py` | FastAPI 生命周期、API 路由、上传限制、API Key、静态页面 | 能从 `/api/chat` 或上传接口讲到 service；能解释 413、404、API Key 和 `/health` 的边界。 |
| P0 | `app/service.py` | 组装数据库、检索器、模型和图；承接应用用例 | 能解释为什么 API、业务编排与基础设施没有全部写在一个文件中。 |
| P0 | `app/graph.py` | AgentState、节点、条件边、有限重试、运行事件 | 能白板画出完整状态流；能指出业务重试一次和 `recursion_limit=12` 的区别。 |
| P0 | `app/retrieval.py` | 分块、embedding provider、BM25 风格检索、向量得分、RRF | 能解释精确词匹配与语义匹配的互补性，手算一个简化 RRF，并指出 dot product/余弦归一化边界。 |
| P0 | `app/ingestion.py` | PDF/DOCX/Markdown/TXT 解析及页码/分节元数据 | 能解释为什么引用可回到 PDF 页码或 Markdown 分节，以及扫描 PDF 的限制。 |
| P0 | `app/llm.py` | 离线回答、DeepSeek 调用、受限 Prompt、错误边界 | 能解释无 Key 也可回归、文档为何作为不可信证据，以及模型失败如何降级/记录。 |
| P0 | `app/db.py` | SQLite schema、事务、WAL、外键、文档/会话/运行轨迹 | 能说清四类表、级联删除、提交/回滚，以及 SQLite 适用和不适用的规模。 |
| P1 | `app/schemas.py` | API 请求/响应的 Pydantic 模型 | 能解释边界校验、OpenAPI 与内部 dataclass 的差别。 |
| P1 | `app/config.py` | 环境变量、默认参数和路径配置 | 能解释配置优先级、密钥不入库以及测试如何覆盖环境变量。 |
| P1 | `app/tools.py` | 受限数学表达式识别和计算 | 能解释为什么不直接 `eval`，以及工具路由和 RAG 路由的区别。 |
| P2 | `app/static/index.html` | 无构建步骤的演示 Web UI | 会演示上传、提问、引用和运行轨迹；不要求两天内精通前端实现。 |
| P2 | `app/__init__.py` | Python 包标记 | 知道用途即可。 |

## 测试与评测

| 优先级 | 文件 | 重点 |
| --- | --- | --- |
| P0 | `tests/test_graph.py` | 三条路由、引用校验、一次重试、异常脱敏；用它反推图的行为承诺。 |
| P0 | `tests/test_retrieval.py` | 混合检索和分块；理解排序结果为什么命中。 |
| P0 | `tests/test_api.py` | 从 HTTP 到存储的端到端行为、上传/删除、API Key、UI。 |
| P1 | `tests/test_ingestion.py` | PDF 页码、Markdown 分节、DOCX 和环境变量边界。 |
| P0 | `scripts/run_eval.py` | 回归评测如何计算 retrieval/answer/citation/verified 命中；能说明它不是生产准确率。 |
| P1 | `evals/eval_set.json` | 8 条受控样例的结构与局限；至少亲手新增或修改一条临时样例观察结果。 |

## 运行、部署与仓库配置

| 优先级 | 文件 | 重点 |
| --- | --- | --- |
| P1 | `pyproject.toml` | Python 版本、运行依赖、dev 依赖、pytest 配置。 |
| P1 | `.env.example` | embedding、DeepSeek、API Key 等可配置边界；真实 Key 不提交。 |
| P1 | `Dockerfile` | 镜像构建、依赖安装、启动命令和健康检查。 |
| P1 | `compose.yaml` | 服务配置、端口、环境变量和数据卷持久化。 |
| P1 | `.github/workflows/ci.yml` | push/PR 自动测试与 Docker 构建；能解释 CI 为何不是部署。 |
| P2 | `.dockerignore` | 避免密钥、模型缓存、数据库和无关文件进入构建上下文。 |
| P2 | `.gitignore` | 避免本地运行物和密钥进入版本控制。 |
| P2 | `LICENSE` | MIT 许可证；知道开源使用边界即可。 |

## 文档怎么用

| 文档 | 作用 | 是否精读 |
| --- | --- | --- |
| `weekend-study-guide.md` | 两日主计划和验收标准 | P0，按时间执行 |
| `quickstarts/README.md` | FastAPI、LangGraph、SQLite 与配套栈入门索引 | P0，随两日计划学习 |
| `knowledge-maps.md` | 各知识面组件关系与流程图 | P0，每学完一块闭卷复画 |
| `framework-boundaries.md` | 相邻框架、协议和平台选型 | P1，理解边界即可 |
| `code-walkthrough.md` | 跟踪请求和导入链路 | P0 |
| `technical-stack-handbook.md` | 项目技术栈核心与高频追问 | 按优先级选读，不要从头背到尾 |
| `interview-questions.md` | 闭卷自测和表达训练 | P0，先答再看 |
| `failure-cases-and-debugging.md` | 制造故障并定位 | P0，至少完成前三例 |
| `hands-on-exercises.md` | 用一次代码修改证明掌握 | P0，至少完成一项 |
| `architecture-and-decisions.md` | 架构、取舍与生产演进 | P1 |
| `learning-path.md` | 总体学习路线 | P1，与两日主计划配合 |

## 是否需要继续补注释

只在以下情况补代码注释：

1. 业务约束无法从代码自然看出，例如“RAG 至多重试一次”；
2. 算法常数或安全取舍需要说明原因；
3. 外部库行为容易被误解；
4. 临时兼容或边界处理无法通过命名表达。

不要给明显赋值、简单函数调用或每一个条件分支写翻译式注释。判断标准是：注释应解释 **why** 和边界，函数名、类型和测试负责表达 **what**。
