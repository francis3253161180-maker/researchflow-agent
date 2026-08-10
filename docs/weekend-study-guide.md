# ResearchFlow 周末学习冲刺

目标：用两天把“项目已经完成”转化为“可以独立演示、解释、修改和排错”。周一开始投递，不以继续补功能为借口延期。

这份文档是两天学习的唯一主计划。不要尝试平均阅读所有文件；先按 [文件地图与精读优先级](file-map.md) 吃透 P0，再补 P1。技术手册用于查缺补漏，不要求从第一页背到最后一页。

框架知识按 [快速入门手册](quickstarts/README.md) 学习；每完成一块，关掉手册复画 [知识关系图](knowledge-maps.md)，再回到代码定位，而不是连续阅读多个小时。

## 两天时间预算

每天按约 8 小时有效学习设计，中间休息不计入。若启动或环境排错超时，先记录问题并切到代码走读，不要让安装问题吞掉半天。

| 时间 | 任务 | 可验收产物 |
| --- | --- | --- |
| 周六 09:00–10:00 | 跑测试、回归评测和 Web/API 完整链路 | 一次上传、问答、引用、run trace 和删除操作 |
| 周六 10:00–12:00 | 架构与文件地图 | 不看稿画架构；能说出 P0 文件职责 |
| 周六 14:00–17:00 | 精读 `main -> service -> graph -> llm/db` | 从 HTTP 请求讲到响应和持久化；解释三个 route 与一次重试 |
| 周六 19:00–21:00 | FastAPI/LangGraph 快速入门与高频题 | 闭卷复画请求和状态流；完成 30 秒与 90 秒介绍 |
| 周日 09:00–12:00 | 精读 ingestion/retrieval/eval | 解释分块、BM25、embedding、RRF、引用与评测边界 |
| 周日 14:00–16:00 | 制造三个失败案例并排错 | 每例写出症状、定位证据、原因和修复/降级方式 |
| 周日 16:00–18:00 | 完成一个小修改并回归 | 一个可解释的 diff，`pytest` 与回归评测通过 |
| 周日 19:00–21:00 | SQLite/配套栈复盘、模拟面试与演示 | 3 分钟演示；5 分钟项目讲解；核心题正确率至少 80% |

两天内不做：重写前端、迁移向量数据库、训练 embedding/reranker、系统学习全部 PyTorch、继续为了“更完整”堆功能。这些都不能替代对当前实现的掌握。

## 完成标准

周日结束前，应当能够做到：

- 不看稿画出 `plan -> rewrite -> retrieve/tool/direct -> answer -> verify -> persist`；
- 指出一次请求在 `main.py`、`service.py`、`graph.py`、`retrieval.py`、`llm.py`、`db.py` 中如何流动；
- 解释 BM25、向量检索和 RRF 各自解决什么问题；
- 独立跑通上传、问答、引用、运行轨迹、测试和回归评测；
- 制造并定位空知识库、引用缺失、模型异常三个失败案例；
- 完成至少一项小修改，并用测试证明没有破坏现有行为；
- 用 30 秒、90 秒和 5 分钟三个版本介绍项目；
- 明确说明 V1 的边界，不把受控回归集包装成企业准确率。

## 周六上午：先跑通系统

### 1. 基线检查

```powershell
cd C:\Users\32531\Desktop\找实习\researchflow-agent
python -m pytest
python scripts/run_eval.py --embedding-provider hash
uvicorn app.main:app --reload
```

打开：

- Web UI：<http://127.0.0.1:8000>
- OpenAPI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

### 2. 完成一次真实操作

1. 上传一篇有文本层的 PDF；
2. 查看文档列表中的文件名、类型、分块数量；
3. 提出一个能从文档直接回答的问题；
4. 检查 `route`、`citations`、`verified`、`latency_ms`；
5. 通过 `run_id` 查看节点事件；
6. 回到原始页码核对引用；
7. 删除文档，确认关联 chunks 被级联删除。

### 3. 自测问题

- 为什么 `/health` 不受 API Key 保护？
- 上传文件为什么只读取 `max_upload_bytes + 1`？
- `session_id` 不传时在哪里生成？
- 为什么 FastAPI 层不直接实现检索和编排？

## 周六下午：跟踪一条请求

按 [代码走读](code-walkthrough.md) 操作，不要先泛读框架文档。

建议在纸上记录：

```text
HTTP request
  -> Pydantic validation
  -> ResearchFlowService.chat
  -> initial_state
  -> compiled LangGraph.invoke
  -> plan route
  -> retrieve/tool/direct
  -> answer
  -> verify
  -> optional retry
  -> persist messages + run trace
  -> ChatResponse
```

当天必须能解释：

- State、Node、Edge、Conditional Edge 在本项目中分别是什么；
- 为什么设置 `recursion_limit=12`；
- 为什么业务重试上限和 LangGraph recursion limit 是两层不同的保护；
- `verify_node` 当前验证了什么、没有验证什么；
- 为什么模型异常只保存 `llm_error: <ExceptionType>`。

## 周日上午：掌握检索与引用

阅读：

1. `app/ingestion.py`
2. `app/retrieval.py`
3. `scripts/run_eval.py`
4. `evals/eval_set.json`

### 必须算懂的两个公式

本项目词法检索接近 BM25：

```text
IDF(t) = log(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

score(t,d) = IDF(t) * TF(t,d) * (k1 + 1)
             / (TF(t,d) + k1 * (1 - b + b * |d| / avgdl))
```

当前代码中 `k1=1.5`、`b=0.75`。

RRF 融合：

```text
RRF(d) = 1 / (60 + rank_lexical(d))
       + 1 / (60 + rank_vector(d))
```

它融合的是排名，不直接比较 BM25 分数和 cosine 分数。

### 动手观察

- 用精确术语提问，观察词法检索的价值；
- 把问题改成同义表达，观察 FastEmbed 的价值；
- 修改一个问题，使 top-1 错误但 top-4 命中；
- 判断问题来自切块、召回、融合排序还是回答/引用。

## 周日下午：失败案例与小修改

先完成 [失败案例与调试](failure-cases-and-debugging.md) 中前三个案例，再从 [动手练习](hands-on-exercises.md) 选择一项 Level 1 或 Level 2 任务。

推荐最小修改：给 `plan_node` 新增一条路由回归测试，或给 PDF/Markdown 元数据新增一个断言。完成后运行：

```powershell
python -m pytest
python scripts/run_eval.py --embedding-provider hash
git diff
```

不要把“修改了代码”当作完成；必须能解释：

1. 修改前的行为；
2. 为什么需要改；
3. 修改影响哪些模块；
4. 用什么测试证明修改正确；
5. 还有哪些未覆盖边界。

## 周日晚：面试表达

### 30 秒版本

> 我独立开发了一个可本地部署的科研文档 Agent。系统以 FastAPI 提供服务，用 LangGraph 显式编排检索、工具、回答、引用校验、有限重试和持久化；检索侧采用 BM25 风格排序与向量检索，再用 RRF 融合。项目支持多格式导入、页码/分节引用、SQLite 运行轨迹、Docker 部署和 CI 测试。

### 90 秒版本

> 普通 RAG demo 往往只能生成回答，缺少证据追溯、失败定位和可重复验证。我把系统拆成文档解析、混合检索、Agent 状态流、受限回答、Query Rewrite、结构化引用校验和运行轨迹。FastAPI 负责服务边界；LangGraph 负责 `plan -> rewrite -> retrieve/tool -> answer -> verify -> persist`：Rewrite 只使用近期已验证的用户 + 助手 turn 解决指代，历史回答不是证据；Verify 固定先判无候选、再判候选不相关、最后才判缺引/错引。无候选或候选不相关时仅做一次中性扩展改写/重检索，引用缺失或越界时用不同约束只在同一证据上重答一次。网页通过 SSE 实时反馈当前节点，但只有 Verify 后的最终答案会进入聊天记录。PDF 保留页码与可跨页继承的标题栈，DOCX/Markdown 保留完整标题路径；BM25 和向量结果通过 RRF 融合。SQLite 保存会话、逐轮 citations 和节点事件，网页可恢复同一会话的全部 runs，并可为单轮选择 DeepSeek 快速回答或深度思考；首轮结束后会在模型可用时生成简短会话标题。项目还有独立 MCP Server，向外部 Host 提供检索、引用回查和计算。当前有 61 项测试和 8 条受控回归样例；这些证明链路可回归，但不代表真实业务准确率。

### 5 分钟版本结构

1. 场景和问题；
2. 架构图与一次请求；
3. 检索和引用；
4. 状态、路由、重试和持久化；
5. 工程验证；
6. V1 边界与下一步。

## 周一投递前检查

- [ ] GitHub README 和文档链接可打开；
- [ ] 本地 `pytest` 通过；
- [ ] 能在 3 分钟内启动并演示；
- [ ] 能回答 [面试问题集](interview-questions.md) 中的核心题；
- [ ] 不再修改简历措辞；
- [ ] 开始第一批岗位投递，并记录岗位、时间、版本和状态。

## 两天结束时的闭卷验收

只有同时满足以下四项，才算“掌握项目”，而不是“看过文档”：

1. **画得出**：10 分钟内画出组件图、请求链路和 RAG 重试环；
2. **讲得清**：不看稿完成 90 秒介绍，并回答核心面试题至少 80%；
3. **改得动**：独立完成一项小修改，能说明影响范围并补测试；
4. **排得出**：能用 run trace、API 响应、SQLite、pytest 和评测结果定位至少三个失败案例。

没有记住所有函数名不算失败；无法解释数据如何流动、为何这样取舍、测试证明了什么，才说明还没有掌握。
