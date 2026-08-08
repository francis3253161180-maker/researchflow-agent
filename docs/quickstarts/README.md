# ResearchFlow 快速入门手册索引

这些手册服务于两天项目冲刺，不追求替代官方文档。每篇只覆盖核心概念、高频追问、ResearchFlow 代码映射和动手验收。

## 建议顺序

| 顺序 | 手册 | 建议时间 | 达成目标 |
| --- | --- | --- | --- |
| 1 | [FastAPI](fastapi.md) | 60–90 分钟 | 从 HTTP 走到 service；掌握 Pydantic、Depends、lifespan、上传和测试 |
| 2 | [LangGraph](langgraph.md) | 90 分钟 | 掌握 state/node/edge、条件路由、有限重试和持久化边界 |
| 3 | [SQLite](sqlite.md) | 60–90 分钟 | 掌握 schema、事务、外键、索引、WAL 和扩展边界 |
| 4 | [配套技术栈](supporting-stack.md) | 90–120 分钟 | 建立 Pydantic、Uvicorn、HTTPX、FastEmbed、pytest、Docker/CI 全景 |
| 5 | [框架与组件边界](../framework-boundaries.md) | 30 分钟 | 能比较 LangGraph、LangChain、LlamaIndex、MCP、Dify/Coze 和 vLLM |
| 6 | [知识关系图全集](../knowledge-maps.md) | 每阶段 5–10 分钟 | 复盘知识连接，不把关系图当成要背的定义 |

## 阅读方法

1. 先运行对应代码或 API；
2. 只看一节手册；
3. 关掉手册，用自己的话画图并解释；
4. 在仓库中定位实现；
5. 回答高频题；
6. 完成至少一个验收动作。

判断是否掌握，不看阅读时长，而看是否能“画得出、讲得清、改得动、排得出”。

## 两天内不要求

- 背完框架所有 API；
- 把项目改写成 LangChain/LlamaIndex 版本；
- 实现生产级鉴权、分布式数据库或 Kubernetes；
- 系统掌握 vLLM/PyTorch 内核；
- 为每个源文件重复写一篇说明。

需要深入时，再沿各手册结尾的官方资料继续学习。
