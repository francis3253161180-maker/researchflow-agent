# 失败案例与调试

Agent项目的区分度常来自“失败时能否定位”，而不是正常demo。

## 通用定位顺序

```text
输入是否合法？
  -> 文档是否解析正确？
  -> chunks和元数据是否正确？
  -> 相关证据是否进入top-K？
  -> 融合排序是否合理？
  -> LLM是否使用证据并生成引用？
  -> verify规则是否正确？
  -> state/events/errors是否持久化？
```

不要一失败就调大 `top_k` 或改 prompt。

## 案例1：空知识库

**复现**：删除全部文档后提问知识型问题。

**预期**：`route` 路由到 `direct`，回答提示先导入文档或配置模型；不伪造引用。

**检查**：`route=direct`、`citations=[]`、`verified=true`。这里verified只表示direct回答非空。

## 案例2：RAG回答没有引用标记

**复现方式**：参考 `test_missing_citations_retry_exactly_once`，替换一个总是返回无引用文本的 fake LLM。

**预期**：retrieve执行两次，`retry_count=2`，最终 `verified=false` 并持久化。

**关键解释**：只允许一次回环；第二次verify不再回到retrieve。

## 案例3：模型服务异常

**复现方式**：使用会抛出异常的 fake LLM，或配置不可达的base URL。

**预期**：接口返回受限失败消息；state和run中只保存 `llm_error: 异常类型`。

**检查**：不应把上游响应正文、API Key、内部路径写入errors。

## 案例4：PDF没有文本层

**现象**：扫描版PDF导入后提示没有可提取文本。

**原因**：PyMuPDF/pypdf 都只能读取已有文本层，不是 OCR。

**正确处理**：明确V1边界；后续接OCR和版面分析，并用抽样质检验证。

## 案例5：Markdown引用没有分节

**检查顺序**：

1. 标题是否以 `#` 开头；
2. `_markdown_blocks` 是否正确更新current_section；
3. `chunk_blocks` 是否保留section；
4. SQLite chunks.section是否写入；
5. citation response是否序列化section。

## 案例6：精确关键词能搜到，同义问法搜不到

**可能原因**：使用HashEmbedding，或FastEmbed没有启用/模型未加载。

**验证**：分别运行hash和fastembed评测。不要把HashEmbedding的哈希碰撞称为语义能力。

## 案例7：向量结果异常

**检查**：

- query/document是否使用正确编码接口；
- 维度是否一致；
- 是否归一化；
- 远程服务返回顺序是否按index恢复；
- 文本是否被截断或为空。

FAISS 会在索引与查询边界做 L2 归一化；若出现维度不一致，索引会给出明确错误而不是静默比较不同向量空间。更换 embedding provider/model 后应删除旧语料并重导入。

## 案例8：网络搜索未配置或 MCP 调用失败

**复现**：在默认 `WEB_SEARCH_PROVIDER=none` 下选择“仅网络搜索”，或配置不可用的 MCP command/tool。

**预期**：`web_search` 节点记录脱敏错误，回答明确说明“未获得可核验网页证据”；不会把网络问题伪装成本地 RAG 命中，也不会杜撰 URL 引用。

**检查**：查看 `route=web`、`errors` 和 `events`；确认 `.env` 中 API Key 未进入 Git 或 run 轨迹。

## 案例9：FAISS 索引与 SQLite 分块不同步

**现象**：直接手改 SQLite，或服务异常中断在文档写入后，索引大小和 `chunks` 数不一致。

**预期**：下一次查询会检查数量并从 SQLite 重建 FAISS 索引；正常导入、删除和服务重启也都会触发重建。

**边界**：FAISS 不是持久化真源。若 SQLite 向量已来自旧 embedding 模型，重建不会修复向量空间混用，仍需重导入。

## 案例10：SQLite锁或写入异常

**检查**：

- 是否有长事务；
- 是否多个进程同时写一个文件；
- 数据目录是否可写；
- WAL文件是否位于持久化volume；
- 是否误把SQLite用于多实例共享。

当前连接timeout为20秒，成功commit、异常rollback。

## 案例11：Docker重启后数据消失

**检查**：是否通过Compose启动并挂载 `researchflow-data:/app/data`；直接运行容器但未挂volume会丢失容器可写层中的数据。

## 调试证据清单

- `/health`：服务与汇总指标；
- `/api/documents`：文档与分块数；
- `/api/runs/{run_id}`：route、events、errors、latency；
- SQLite表：documents/chunks/messages/runs；
- `pytest -vv`：失败测试；
- `run_eval.py` details：检索、答案、引用和verified分别是否命中；
- Docker日志和GitHub Actions日志。
