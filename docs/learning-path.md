# ResearchFlow 学习与面试路径

目标不是背下所有文件，而是在 2–3 天内能够独立运行、修改并解释这个项目。完成每一步后，至少自己回答一次“为什么这样设计，而不是更复杂的方案？”。

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
question → plan → retrieve/tool → answer → verify → SQLite trace
                         ↑                 │
                         └── retry once ────┘
```

## 第 2 天：理解 RAG 质量与证据

阅读 `app/ingestion.py` 与 `app/retrieval.py`，并用以下问题自测：

1. 为什么 PDF 要保留页码，而 Markdown 要保留标题层级？
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
- 为 PDF 导入写一项带页码的单元测试；或
- 改进 `plan_node` 的路由规则，并新增一个回归用例。

改完后依次执行：

```powershell
pytest
python scripts/run_eval.py --embedding-provider fastembed
git status
```

这一步比继续往简历里增加框架名更有价值：你能展示自己如何理解、修改并验证一个 Agent 服务。

## 面试的 90 秒版本

> 我做了一个面向科研文档的本地 Agent/RAG 服务。它支持 PDF、DOCX、Markdown、TXT 导入，检索端采用词法检索和向量检索的 RRF 融合，并将 PDF 页码和 Markdown 分节作为引用元数据返回。Agent 用 LangGraph 拆成计划、检索/工具、回答、引用校验和持久化五类节点；当 RAG 答案没有有效引用时只会扩展查询重试一次，避免无界循环。服务通过 FastAPI 暴露，SQLite 保存会话和运行轨迹，默认离线可复现，也可通过环境变量接 DeepSeek。项目有 14 项测试、8 条受控回归样例，并在 GitHub Actions 中持续测试和构建 Docker 镜像。

接着准备两个追问：

- **为什么不用向量数据库？** V1 的目标是单机小规模科研资料和可理解性。检索接口已抽象，语料和并发增加后再迁移到专用向量库。
- **引用校验能保证事实正确吗？** 不能完全保证。它只保证回答有检索证据和引用格式；下一步要在标注数据上测 Recall@K、引用忠实度，并引入更细的内容级校验。

## 不应在面试中夸大的内容

- 8/8 是受控项目回归集，不是企业场景准确率；
- 14 项测试与 CI 证明基本工程质量，不等于生产级高并发能力；
- FastEmbed 是 CPU 语义向量能力，不等于做过 GPU 推理优化；
- LangGraph 是编排框架，当前项目不是 Agent 强化学习/算法研究。
