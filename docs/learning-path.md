# ResearchFlow 学习与面试路径

目标不是背下所有文件，而是在 2–3 天内能够独立运行、修改并解释这个项目。完成每一步后，至少自己回答一次“为什么这样设计，而不是更复杂的方案？”。

如果只安排周末两天，先使用 [周末学习冲刺](weekend-study-guide.md)。学习过程中配合：

- [文件地图](file-map.md)：决定哪些文件精读、哪些只需理解职责；
- [快速入门手册](quickstarts/README.md)：FastAPI、LangGraph、SQLite 和配套栈的最小完整知识；
- [知识关系图](knowledge-maps.md)：每学完一块后闭卷复画组件关系；
- [框架边界](framework-boundaries.md)：理解 LangChain、LlamaIndex、MCP、Dify/Coze 等相邻技术，不盲目堆框架；
- [代码走读](code-walkthrough.md)：跟踪一次请求；
- [技术栈手册](technical-stack-handbook.md)：补核心与高频知识；
- [面试问题集](interview-questions.md)：检验表达；
- [失败案例](failure-cases-and-debugging.md)：建立排错能力；
- [动手练习](hands-on-exercises.md)：完成至少一项修改。

## 第 0 步：先跑通，再读代码（30 分钟）

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest
uvicorn app.main:app --reload
```

在网页粘贴一段关于论文的方法笔记，然后问一个能从笔记直接回答的问题。观察：

1. `POST /api/documents` 写入了什么；
2. `POST /api/chat` 返回的 `route`、`citations`、`verified` 和 `run_id`；
3. `GET /api/runs/{run_id}` 中的节点事件。

如果配置了 `DEEPSEEK_API_KEY`，复制 `.env.example` 为 `.env` 后再启动服务；不配置时，项目仍可用离线回答验证整条 RAG 链路。

## 第 1 天：理解一条请求如何流动

按顺序阅读，**不要跳到 LangGraph 文档泛读**：

| 文件 | 需要掌握的点 | 面试能说什么 |
| --- | --- | --- |
| `app/main.py` | FastAPI 路由、上传限制、可选 API Key | 如何把 Agent 包成可部署服务，并限制上传与接口访问。 |
| `app/service.py` | 服务组装与一次 `chat` 调用 | Web/API 层和业务编排层为什么分开。 |
| `app/graph.py` | State、node、conditional edge、retry once | 我把 Agent 拆成显式状态流；引用不合格时只重试一次，避免无界循环。 |
| `app/llm.py` | 离线 fallback、DeepSeek 调用、文档不可信提示 | 为什么系统不依赖某一个云模型，也不会把文档内容当作指令。 |

完成后画出自己的版本：

```text
question → route → rewrite → retrieve/tool → answer → verify → SQLite trace
                         ↑                 │
                         └── retry once ────┘
```

## 第 2 天：理解 RAG 质量与证据

阅读 `app/ingestion.py` 与 `app/retrieval.py`，并用以下问题自测：

1. 为什么 PDF 要保留页码与可跨页继承的标题层级，而 DOCX 要优先保留 Heading 路径？
2. 为什么同时做词法检索和向量检索？它们各自容易漏掉什么？
3. RRF 解决了什么排序问题？为什么这里不直接手写一个复杂加权公式？
4. `hash` 和 `fastembed` 后端分别适合什么阶段？
5. 为什么 V1 不默认放入 Reranker 和向量数据库？

动手练习：把 `evals/eval_set.json` 中任意一条问题改写为同义问法，运行：

```powershell
python scripts/run_eval.py --embedding-provider fastembed
```

如果失败，先判断是切块、召回还是答案引用失败，再改代码；不要直接调高 `top_k` 作为唯一答案。

## 第 3 天：让它成为“你的项目”

选择一项小而真实的改动，完成后写入 GitHub：

- 为网页添加“查看本次节点轨迹”的折叠面板；或
- 在 `/api/documents` 列表展示来源和创建时间；或
- 为 PDF 跨页标题栈或 DOCX 多级 Heading 导入写一项单元测试；或
- 改进 `route_node` 的路由规则，并新增一个回归用例。

改完后依次执行：

```powershell
pytest
python scripts/run_eval.py --embedding-provider fastembed
git status
```

这一步比继续往简历里增加框架名更有价值：你能展示自己如何理解、修改并验证一个 Agent 服务。

## 面试的 90 秒版本

> 我做了一个面向科研文档的本地 Agent/RAG 服务。它支持 PDF、DOCX、Markdown、TXT 导入，检索端采用词法检索和向量检索的 RRF 融合，并将 PDF 页码与可跨页继承的标题路径、DOCX/Markdown 标题路径作为引用元数据返回；DOCX 没有可靠原生页码，不会伪造。Agent 用 LangGraph 显式编排计划、可信短期记忆 Query Rewrite、检索/工具、回答、结构化引用校验和持久化；仅用最近已验证的用户 + 助手 turn 解决指代，历史回答不是证据。Verify 固定先检查无候选、再检查候选是否不相关、最后才检查缺引/错引；无候选或候选不相关时最多做一次中性扩展改写并重检索，引用缺失或越界时分别只基于同一证据重答一次，避免无界循环与证据漂移。服务通过 FastAPI 暴露，SQLite 保存会话、逐轮 citations 与运行轨迹；网页通过 SSE 反馈节点级执行状态，并只在校验后提交最终回答。首轮结束后会在模型可用时生成简短会话标题，离线时保留首问标题。同时用独立 MCP Server 向外部 Agent Host 暴露混合检索、精确引用回查和安全计算。项目有 61 项测试、8 条受控回归样例，并在 GitHub Actions 中持续测试和构建 Docker 镜像。

接着准备两个追问：

- **为什么不用向量数据库？** V1 的目标是单机小规模科研资料和可理解性。检索接口已抽象，语料和并发增加后再迁移到专用向量库。
- **引用校验能保证事实正确吗？** 不能完全保证。它只保证回答有检索证据和引用格式；下一步要在标注数据上测 Recall@K、引用忠实度，并引入更细的内容级校验。

## 不应在面试中夸大的内容

- 8/8 是受控项目回归集，不是企业场景准确率；
- 61 项测试与 CI 证明明确行为具备回归保护，不等于生产级高并发能力；
- FastEmbed 是 CPU 语义向量能力，不等于做过 GPU 推理优化；
- LangGraph 是编排框架，当前项目不是 Agent 强化学习/算法研究。

## 掌握度验收

不要用“文档看完了”作为完成标准。满足以下条件才算能写在简历上并接受追问：

- 能关闭文档画出图和数据流；
- 能从 API 入口定位到每个节点和数据库表；
- 能解释一个正常案例和三个失败案例；
- 能完成一项小修改并补测试；
- 能说明当前验证数据的边界；
- 能回答 [面试问题集](interview-questions.md) 的核心题而不夸大。
