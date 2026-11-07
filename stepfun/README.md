# LocalSeek · seekdb × 阶跃星辰

浏览器本地 embedding + seekdb 数据库混合检索的中文迷你知识库。源码、模型获取、评测语料与原始日志全部在 `stepfun/`。开发基于官方 `2026-competition-ecosystem` 的提交 `d647275c308e09b7b623f322a26f5c4e85734fb0`，保留导入私有仓库的已有提交。

## 1. 启动

需要 **Node.js 24 LTS**、最新版 Chrome / Edge，以及 **seekdb 1.4+**。使用本机地址 `http://localhost:5173`，WebGPU 需要安全上下文。内存建议 8 GB 以上，数据库配置 3 GB，磁盘至少留出 10 GB。无需任何推理 API Key。

```sh
cd stepfun
npm ci --ignore-scripts
```

`--ignore-scripts` 避免安装服务端 ONNX 推理和嵌入式绑定的原生组件；本应用只使用浏览器运行时和 seekdb server。所有 JavaScript 依赖与平台构建包由锁文件固定。

### 数据库：二选一

**Docker Desktop / Linux Docker：**

```sh
docker compose up -d --wait
```

Compose 固定官方 1.4 镜像 tag 与 digest，只向本机开放 2881，命名卷挂载官方 1.4 的 `/var/lib/seekdb`，启动时创建 `stepfun` 数据库。通过 SQL 健康检查后再启动应用。

**已有 seekdb 二进制的 Linux / Windows WSL2：**

```sh
bash scripts/start-seekdb.sh
```

Windows 可在 PowerShell 中运行：

```powershell
wsl -d Ubuntu-22.04 -- bash /mnt/f/oceanbasestepfun/stepfun/scripts/start-seekdb.sh
```

路径按实际克隆位置替换，保持该终端打开。在另一终端启动 Node 应用。脚本使用独立目录 `$HOME/.local/share/localseek-seekdb`，可通过 `SEEKDB_BASE_DIR` 修改；复用已就绪实例，拒绝覆盖占用端口。已有数据库可以直接执行 `CREATE DATABASE IF NOT EXISTS stepfun;` 后连接。二进制安装参考 [seekdb 官方说明](https://github.com/oceanbase/seekdb)。

### 模型与应用

```sh
# 下载固定提交的模型和 tokenizer，并验证 ONNX 的 Git LFS SHA-256。
node --use-system-ca scripts/download-model.mjs
# 默认数据库连接见 .env.example，需要时复制为 .env 并编辑。
npm run dev
```

打开 [http://localhost:5173](http://localhost:5173)。点击「载入示例语料」→「向量化并导入」→输入问题→「检索」。也可多选 `.txt/.md` 文件、拖入文件或粘贴文本。进度条与运行记录显示模型加载、文档推理、写入、刷新、查询耗时。切换知识库后结果清空。首次加载模型后锁定精度与后端设置，刷新页面即可调整配置。

模型目录 `public/models` 不进入 Git，首次获取需要互联网，总计约 167 MB；实际使用时只加载所选精度。模型与 ONNX Runtime 均由本地 HTTP 服务提供，**文档和查询计算只发生在浏览器 Worker**。首次本地静态读取包含在加载耗时中；后续请求复用同一个模型。`evidence/model-manifest.json` 记录真实文件体积与校验值。

生产模式：

```sh
npm run build
npm start
```

`dist`、`public/models` 和 `node_modules` 需保留；Node 同时提供 API、模型和运行时静态文件。默认绑定 `127.0.0.1`。如设置 `HOST` 用于其他设备访问，应配置 HTTPS 以使用 WebGPU，并自行配置访问鉴权；比赛复现建议维持本机运行。

## 2. 模型与检索

- 原始模型：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，MIT；ONNX：[Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)，版本固定到该提交。
- 默认自动精度：WebGPU 使用 `fp16`（公开的 `onnx/model_fp16.onnx`），WASM 使用 `q8`（`onnx/model_quantized.onnx`）。采用作者发布的 ONNX Runtime 量化产物，不声称在提交机重新量化；INT8 的 `quantize_config.json` 固定 `per_channel=true`、`reduce_range=true`、`weight_type=QInt8`。FP16 使用同源半精度浮点模型，FP32 为同版本对照。需要重新生成时参考 [Transformers.js 模型转换与量化](https://huggingface.co/docs/transformers.js/custom_usage/custom_models)，必须重新执行质量评测和生成模型指纹。
- 量化处理步骤：`npm run models` 先获取同源 FP32 与作者发布的 INT8/FP16 → 下载脚本检查固定 LFS 哈希 → 默认 Worker 根据后端选择已量化文件 → 对相同文档/查询重新生成向量并在 seekdb 评测。可选的 `scripts/quantize-model.py` 展示按发布配置用 ONNX Runtime 动态 INT8 或浮点 FP16 重新生成的方法（依赖及命令在脚本头部）。它写入独立 `.runtime/regenerated`，不覆盖正式模型；其产物未用于本次实测，不保证与作者文件逐字节相同，采用时须更新指纹并重测。
- 512 维，CLS 池化，L2 归一化，512-token 窗口；查询添加 BGE 中文检索指令，文档不添加。默认每块 380 Unicode 字符，重叠 50 字符；极长输入由 tokenizer 截断。精度间共享相同语义空间，模型版本与池化等指纹存于 `kb_catalog`。
- 显式请求 `device: 'webgpu'`；无适配器或初始化失败则 `device: 'wasm'`，显示实际后端和原因。默认自动模式在降级时同时切换为 INT8。单 Worker 串行执行，WASM 单线程无需 COOP/COEP。FP16 是否可用取决于硬件和运行时。本机 Intel GPU 的 INT8 路径出现质量下降，因此默认 GPU 使用 FP16；保留的手工 INT8 选项用于实验，建议同时勾选 WASM。探索性原始数据见 `evidence/backend-selection.json`。
- **seekdb-js 1.4.0** 创建 `Schema(FulltextIndexConfig('ik', {ik_mode:'smart'}), VectorIndexConfig({hnsw:{dimension:512,distance:'cosine'},embeddingFunction:null}))`。服务端不安装或调用 default embedding provider，输入 API 强制要求浏览器向量。
- 默认调用 `collection.hybridSearch`：全文 + kNN 候选各 30，RRF 常数 60，最终 Top K 由 **seekdb 的 `DBMS_HYBRID_SEARCH.GET_SQL` 生成并在数据库内执行**。服务端仅转换返回结构，浏览器仅展示数据库顺序。语义模式单独使用 `collection.query`。
- seekdb 集合内部包含 `_id`、`document`、`metadata`、`embedding` 及自动生成索引；真实 DDL 见 `evidence/database.json`。目录表 `kb_catalog` 也在 seekdb。客户端 localStorage 仅保存当前集合名称。
- SHA-256 稳定片段 ID + `upsert` 保证相同文件重复导入幂等。修改内容作为新版本追加，**不会自动删除旧版本**。每批写入后最后统一 `refresh_index()`，等待完成才报告成功。失败保留已写入数据，可重试相同文件；也可点击「刷新索引」。
- 服务端串行调度完整 SDK 操作，防止 `SET @search_parm` 的连接会话变量被并发请求覆盖。导入开始时捕获集合，并在操作期间禁止切换。接口限制来源、集合名称、向量维度和批量大小；原文通过 DOM `textContent` 显示。

## 3. 复现测试

```sh
npm test
npx playwright install chromium
# 先启动应用和数据库，然后另开终端：
npm run test:e2e
npm run benchmark
node scripts/verify-db.mjs
# WebGPU 可用的机器还可验收自动后端与实际界面：
node scripts/webgpu-smoke.mjs
```

已有 Edge 时无需下载 Chromium：

```powershell
$env:BROWSER_CHANNEL='msedge'
npm run test:e2e
npm run benchmark
```

Linux/macOS 可用 `BROWSER_CHANNEL=msedge npm run benchmark`。如果需要自定义地址，设置 `APP_URL`。脚本会在 seekdb 中创建 `kb_e2e_*`、`kb_benchmark_*` 独立集合以保留可核查数据；一次正式评测的各配置复用同一集合、相同片段 ID 并覆盖向量，以控制数据和索引数量。重复运行不会覆盖个人知识库。

评测数据为 `public/data/evaluation.json` 的 24 份人工语料、16 条标注查询。每配置 3 轮导入、48 次预热查询；报告中位数，保留全部原始记录。FP32 和 INT8 使用相同 CLS/归一化/文本分块与数据库参数。配置逐项对比量化、batch=1→8、每批刷新→最终刷新。浏览器测试拦截所有外部网络请求，任何远程资源请求会进入失败记录。首次模型加载、首次推理及首次查询单列。WebGPU 记录实际 adapter 与实际后端，若发生降级不将结果冒称 GPU 成绩。

详细结果见 [一页技术说明](TECHNICAL.md)、[单页 PDF](output/pdf/LocalSeek-technical-note.pdf)、[原始性能日志](evidence/benchmark-raw.json)、[端到端验收](evidence/e2e.json) 和 [模型大小清单](evidence/model-manifest.json)。本评测集仅用于可复现的小规模工程验证，不代表通用检索基准。正式性能记录来自开发模式；生产构建另通过相同浏览器完整验收。可设置 `BENCH_RUNS` 调整重复次数，`BENCH_FILTER` 仅用于调试特定配置，正式报告应包含全部配置。

PDF 仅为提交材料，应用运行不依赖 Python。重新生成说明可安装 `pip install reportlab pypdf`，执行 `python scripts/technical-note.py`；脚本读取真实评测日志并断言 PDF 恰好一页。非 Windows 环境可通过 `NOTE_FONT` 指定支持中文的 TTF/TTC。

## 4. 故障排查

| 现象 | 处理 |
| --- | --- |
| Node 提示连接 2881 失败 | 等待 SQL 健康检查；确认 `SEEKDB_HOST`，WSL 下可用实例 IP 替代 localhost。检查端口占用，不要删除已有数据 |
| 模型 404 | 执行下载脚本，核对 `public/models/Xenova/bge-small-zh-v1.5/onnx` 的实际文件与 manifest |
| 企业网络证书错误 | Node 24 用 `--use-system-ca` 信任系统证书，或配置可信的 `NODE_EXTRA_CA_CERTS`；不要关闭 TLS 验证 |
| WebGPU 不可用 | 查看界面后端和降级原因；默认自动模式改用 WASM + INT8。可勾选强制 WASM 验证 |
| 导入中断 | 原数据保留，重新导入同一文件；稳定 ID 会覆盖同一片段。必要时手工刷新索引 |
| 更换模型 | 修改模型及固定 revision、重新生成 manifest、更新模型指纹、使用新集合，并重新跑评测 |

## 5. 文件布局

`src/` 为界面、Worker 和浏览器引擎；`server/` 为本地 API 与 seekdb 层；`shared/` 为模型配置和分块；`scripts/` 为模型下载、数据库启动与验证、浏览器验收与评测；`public/data/` 为可复现实验语料；`evidence/` 为提交应用生成的实际日志和截图。没有云端推理、随机向量、预计算 embedding、模拟数据库或回放检索结果。
