---
title: Sources 与采集
description: 采集证据、查看 Source 记录并配置后续处理。
---

# Sources 与采集

Sources 在信息被筛选为知识之前保留证据。Content Source 保存采集的文本，其他 Source 类型可引用 Connector 提供的材料。
Work Contract 和 Task Outcome 也会保留 Source 证据。

## 采集与检查

1. 选择已有 Scope。[API Quick Start](../develop/api-quickstart.md)演示通过 `POST /v1/sources/content` 采集内容，
   并保留返回的 Source 引用。
2. 使用 `GET /v1/scopes/{scope_id}/sources` 浏览 Source journal，再通过
   `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` 读取选中的 Source。
3. 将符合条件的证据处理为 Memory，需要配置生成模型，并启用定时处理或显式执行 Memory flush。
   步骤见[启用提取与向量搜索](../get-started/configure-models.md)。

采集成功表示证据已保存，不表示提取成功，也不表示内容一定值得长期保留。
通过 Source 引用追溯生成结果的输入。不要在同一身份下修改内容并重试采集，将其当作新的观察。

## 选择采集入口

- Agent Prompt Hook：在[接入指南](../integrations/index.md)中选择宿主对应的采集开关。
- 文件与对象存储：使用 [OpenDAL Connector](ingest-text-files-with-opendal.md)。
- Scope Profile 的主体证据：按 [Scope Profile](use-profiles.md)写入。
- 应用或评测事件：使用应用对应适配器；[Bub 采集](../integrations/evaluation.md)需要显式启用。

Source 读写受所选 [Scope 与访问策略](scopes-and-access.md)约束。

## 采集 PDF 页面

`powercontext.sources.pdf_pages` 提供 `PDFDocumentInput`、`parse_pdf_document`、
`PDF_PAGE_SOURCE_DEFINITION` 和 `search_pdf_pages`。使用此模块需要安装内置 Runtime 依赖和
`pypdf`；比赛环境固定使用 `pypdf==6.19.0`。

将完整原 PDF 字节和非空白文档 ID 交给解析器。结果按物理页顺序返回，从 1 开始，包含空白页。
每页携带整个 PDF 的 SHA-256。可使用空密码读取的权限 PDF 属于合法输入；无效、截断或需要密码的
文件以 `ValueError` 整体失败，不返回部分页面。文本提取保留页边界，不执行 OCR。
接受文档前会核对页树计数及 Flate 压缩流完整性，检查范围包括表单资源和字体字符映射中的文本流。
字体将字形映射为 NUL 时，可使用 PDF 内无歧义的单字形 `/ActualText` 标记恢复 Unicode 映射。
标准 Unicode 连字会解码为对应字母序列。
几何提取仅在全部非空白字符和数字词项均保持完整、总词项数变化不超过普通提取的 10% 时修复断词。
保留 pypdf 能提取的全部文字，包括裁剪路径中的文字和表格中的重复数值，不按视觉裁剪结果删除采集内容。

使用包含 `PDF_PAGE_SOURCE_DEFINITION` 的 `SourceDefinitionRegistry` 逐页 resolve，
再通过 `project_source_for_transport` 生成 observation，并经 Runtime ingestion 提交。
提交前注册 `manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION)`。该 Definition 名为 `pdf-page`，
版本为 `1`；CAPTURED Source 可精确读回原 `PDFPageInput`。标准文本投影的正文为
`document_id + "\n" + page.text`，metadata 恰好包含 `document_id`、`document_sha256` 和
`page_number`。长文档 ID 在 Source 身份中使用摘要，原 ID 仍完整保存在页面和 metadata 中。
已有的短 Source 身份保留原有编码。

配置 pipeline，将每页投影保存为带有该 Source 引用的独立 Memory 条目。处理完成后，调用
`await search_pdf_pages(runtime.memory.for_scope(scope_id), question, limit=20)`。
Source 引用全部来自 PDF 页面的正文允许超过普通 Memory 条目的 8192 字节限制，
保证完整页面在内容哈希、持久化、重启读回和索引重建中保持一致。
这类正文保留 Unicode 的原始编码形式，避免不透明文档 ID 与已采集投影不一致；内容哈希继续使用规范 JSON 编码。
结果为不同页面的精确 `MemoryCitation` 元组；可通过
`memory.get(GetMemoryEntryRequest(citation=citation))` 回查，关闭并重新打开 Runtime 后仍然有效。
非正上限或空白问题返回空元组，其他 Source 类型不会出现在结果中。
同一文档的同一物理页在不同 PDF 修订之间最多占用一个结果名额，返回的引用仍精确指向所选的存储修订；旧引用继续可读。

检索通过 scoped Memory 的公共接口读取当前 PDF 条目，融合原始问题词项与财务扩展词项的独立排名，
优先选择与问题中公司名称和财年匹配的文档。涉及财务报表的问题会保留少量结果名额，
用于通过标题和数字内容识别出的相关利润表、现金流量表及资产负债表，以及相邻的明确续页。
单复数和财年前缀在评分时归一化。
文档 ID 可以是不包含公司信息的标识；无法从 ID 匹配公司时，利用问题与文档开头页面共有的词项确定文档上下文，
再检索没有重复公司名称的报表页面。
通用财务表述会映射到会计术语，例如向供应商付款对应应付账款、租用建筑物对应经营租赁。
结果预算为原始问题和扩展查询各自最强的候选保留少量名额，涵盖与原始问句没有相同词项的附注页。
报表识别支持标题单词被 PDF 文本对象切开、标题词序及单复数变化，以及标题在表格之后绘制的情况。
使用财务行项目区分实际报表与目录、正文引用；比率问题保留计算两端所需的报表，
例如股息支付率同时保留利润表和现金流量表。
按年份选择文档时，明确指定的财年优先于债务到期日等其他日期。

Memory 支持列举条目时，每个问题的处理量和临时存储量随作用域内的正文总量增长；
仅支持 search/get 时，通过扩展词项查询取得候选，再使用相同的页面选择逻辑。
两条路径均不依赖模型或网络连接。
调用方应按完整摘要保存原 PDF，以便按物理页码核对。PDF 字节变化后产生新身份；
保留旧修订原文件，才能继续核对旧引用。
