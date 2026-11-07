# LocalSeek · 一页技术说明

2026-11-07 (UTC+8)；Windows 11 / WSL2 Ubuntu 22.04；i5-1240P / Intel Iris Xe / 23.7 GiB RAM；Edge 157.0.4341.42 headless；Node 24.22.1；seekdb 1.4.0，内存 3G / CPU=0 (自动) / cosine HNSW / IK smart。

**模型与量化。** BAAI/bge-small-zh-v1.5 (MIT)，ONNX 来自 Xenova/bge-small-zh-v1.5，固定提交 75c43b069aac4d136ba6bc1122f995fedcfd2781。Transformers.js 3.8.1 / ONNX Runtime Web 1.22.0-dev.20250409-89f8206ba4。512 维、CLS、L2 归一化，查询加中文检索指令。采用公开的 INT8 (QInt8 / per-channel / reduce-range) 与 FP16 产物；SHA-256 与配置可查 model-manifest.json。统一口径为单一 ONNX + 相同 tokenizer/config：FP32 95,402,424 B；INT8 24,561,389 B (3.88x)；FP16 48,047,974 B (1.99x)。

**端侧与 seekdb。** 浏览器 Web Worker 执行文档/查询推理，默认显式 WebGPU + FP16；无 adapter 或初始化失败自动 WASM + INT8，并显示原因。所有模型和 WASM 本地托管，验收阻断外部域名且零远程请求。seekdb-js 1.4.0 关闭 embeddingFunction；向量、原文、元数据存于 seekdb。hybridSearch 调用 DBMS_HYBRID_SEARCH.GET_SQL，数据库内全文 + kNN + RRF (60，候选 30)，客户端仅展示排序。

**状态与优化。** 独立集合 + seekdb 目录表记录模型指纹；localStorage 仅存选中集合。稳定 SHA-256 ID + upsert 实现增量与重复导入幂等；每次导入结束等待索引刷新。失败保留数据并可重试。导入锁定集合，SDK 操作串行，防止 会话变量竞争；模型只加载一次。对照逐项改变精度、batch 1→8、每批刷新→最终刷新。

| 配置 | 模型 MB | 吞吐 段/s | DB ms | 展示 ms | Recall@5 | nDCG@5 |
| --- | --- | --- | --- | --- | --- | --- |
| FP32 / WASM / B1 | 95.4 | 3.7 | 50.2 | 156.9 | 0.9375 | 0.8761 |
| INT8 / WASM / B1 | 24.6 | 6.3 | 49.0 | 132.4 | 0.9375 | 0.8789 |
| INT8 / WASM / B8 | 24.6 | 5.5 | 50.0 | 138.2 | 0.9375 | 0.8761 |
| INT8 / WASM / B8* | 24.6 | 5.5 | 47.7 | 129.2 | 0.9375 | 0.8761 |
| FP16 / WebGPU / B8* | 48.0 | 67.6 | 51.0 | 107.2 | 0.9375 | 0.8761 |

24 文档 / 16 人工标注查询；每配置 3 轮导入、48 次查询，按文档 ID 去重，报告中位数 (质量取均值)。各配置复用同一 seekdb 集合并覆盖向量；* 为最终统一刷新，其他逐批刷新。MB=10⁶ B。吞吐仅计模型推理；DB 含 SDK 混合查询；展示包含查询推理、HTTP、DOM 更新及一帧等待。小测试集仅验证工程质量。

首次加载/首推理 ms (FP32、INT8 WASM、FP16 GPU)：3196/528；1114/264；2685/969。模型本地 HTTP 获取时间独立记录于 load.modelFetchWallMs，包含于总加载时间；首次查询另存 coldQuery，未混入预热中位数。

INT8 WASM 导入总耗时 20728→5058 ms；batch 1→8 的纯向量化吞吐 6.28→5.50 段/s，未据此宣称 CPU 推理加速；主要收益来自减少写入和刷新次数。FP32 与正式量化路径质量变化见表。探索性 INT8 WebGPU 的 Recall@5=0.625，故默认 GPU 改用 FP16；原始记录保留于 backend-selection.json。

复现：npm ci --ignore-scripts → docker compose up -d --wait (或 WSL 启动脚本) → npm run models → npm run dev；npm test / npm run test:e2e / npm run benchmark。完整条件、分阶段原始记录和源码见 stepfun/README.md、evidence/benchmark-raw.json、evidence/database.json、evidence/e2e.json。
