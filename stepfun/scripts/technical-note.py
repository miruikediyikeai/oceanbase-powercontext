"""Generate the one-page submission note from actual browser benchmark logs.

Optional documentation-only dependencies: pip install reportlab pypdf
"""
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from pypdf import PdfReader

root = Path(__file__).resolve().parents[1]
raw = json.loads((root / 'evidence/benchmark-raw.json').read_text('utf8'))
manifest = json.loads((root / 'evidence/model-manifest.json').read_text('utf8'))
assert len(raw['configs']) == 5 and all('summary' in x for x in raw['configs'])
baseline, unoptimized, batched, optimized, gpu = raw['configs']
assert gpu['load']['backend'] == 'webgpu', 'GPU results must actually use WebGPU'
assert all(not x['externalRequests'] and not x['browserErrors'] for x in raw['configs'])
sz = manifest['sizes']
ratio = manifest['compressionRatio']
f16ratio = sz['model.onnx'] / sz['model_fp16.onnx']
fmt = lambda x: f'{x:.1f}'
percentage = lambda x: f'{x * 100:.2f}%'

environment = ('2026-11-07 (UTC+8)；Windows 11 / WSL2 Ubuntu 22.04；i5-1240P / Intel Iris Xe / 23.7 GiB RAM；'
               f"Edge {baseline['browserVersion']} headless；Node 24.22.1；seekdb 1.4.0，内存 3G / CPU=0 (自动) / cosine HNSW / IK smart。")
sections = [
    ('模型与量化', 'BAAI/bge-small-zh-v1.5 (MIT)，ONNX 来自 Xenova/bge-small-zh-v1.5，固定提交 '
     '75c43b069aac4d136ba6bc1122f995fedcfd2781。Transformers.js 3.8.1 / ONNX Runtime Web '
     '1.22.0-dev.20250409-89f8206ba4。512 维、CLS、L2 归一化，查询加中文检索指令。采用公开的 INT8 '
     '(QInt8 / per-channel / reduce-range) 与 FP16 产物；SHA-256 与配置可查 model-manifest.json。'
     f"统一口径为单一 ONNX + 相同 tokenizer/config：FP32 {sz['model.onnx']:,} B；"
     f"INT8 {sz['model_quantized.onnx']:,} B ({ratio:.2f}x)；FP16 {sz['model_fp16.onnx']:,} B ({f16ratio:.2f}x)。"),
    ('端侧与 seekdb', '浏览器 Web Worker 执行文档/查询推理，默认显式 WebGPU + FP16；无 adapter 或初始化失败自动 '
     'WASM + INT8，并显示原因。所有模型和 WASM 本地托管，验收阻断外部域名且零远程请求。seekdb-js 1.4.0 '
     '关闭 embeddingFunction；向量、原文、元数据存于 seekdb。hybridSearch 调用 DBMS_HYBRID_SEARCH.GET_SQL，'
     '数据库内全文 + kNN + RRF (60，候选 30)，客户端仅展示排序。'),
    ('状态与优化', '独立集合 + seekdb 目录表记录模型指纹；localStorage 仅存选中集合。稳定 SHA-256 ID + upsert '
     '实现增量与重复导入幂等；每次导入结束等待索引刷新。失败保留数据并可重试。导入锁定集合，SDK 操作串行，防止 '
     '会话变量竞争；模型只加载一次。对照逐项改变精度、batch 1→8、每批刷新→最终刷新。'),
]

headers = ['配置', '模型 MB', '吞吐 段/s', 'DB ms', '展示 ms', 'Recall@5', 'nDCG@5']
rows = []
for item, label, modelsize in [
    (baseline, 'FP32 / WASM / B1', sz['model.onnx']),
    (unoptimized, 'INT8 / WASM / B1', sz['model_quantized.onnx']),
    (batched, 'INT8 / WASM / B8', sz['model_quantized.onnx']),
    (optimized, 'INT8 / WASM / B8*', sz['model_quantized.onnx']),
    (gpu, 'FP16 / WebGPU / B8*', sz['model_fp16.onnx']),
]:
    s = item['summary']
    rows.append([label, fmt(modelsize / 1e6), fmt(s['throughput']), fmt(s['searchMs']), fmt(s['displayMs']), f"{s['recall5']:.4f}", f"{s['ndcg5']:.4f}"])

method = ('24 文档 / 16 人工标注查询；每配置 3 轮导入、48 次查询，按文档 ID 去重，报告中位数 (质量取均值)。'
          '各配置复用同一 seekdb 集合并覆盖向量；* 为最终统一刷新，其他逐批刷新。MB=10⁶ B。'
          '吞吐仅计模型推理；DB 含 SDK 混合查询；展示包含查询推理、HTTP、DOM 更新及一帧等待。小测试集仅验证工程质量。')
fresh = ('首次加载/首推理 ms (FP32、INT8 WASM、FP16 GPU)：' + '；'.join(
    f"{x['load']['loadMs']:.0f}/{x['firstInferenceMs']:.0f}" for x in [baseline, optimized, gpu]) +
    '。模型本地 HTTP 获取时间独立记录于 load.modelFetchWallMs，包含于总加载时间；首次查询另存 coldQuery，未混入预热中位数。')
state = (f"INT8 WASM 导入总耗时 {unoptimized['summary']['importTotalMs']:.0f}→{optimized['summary']['importTotalMs']:.0f} ms；"
         f"batch 1→8 的纯向量化吞吐 {unoptimized['summary']['throughput']:.2f}→{batched['summary']['throughput']:.2f} 段/s，"
         '未据此宣称 CPU 推理加速；主要收益来自减少写入和刷新次数。FP32 与正式量化路径质量变化见表。'
         '探索性 INT8 WebGPU 的 Recall@5=0.625，故默认 GPU 改用 FP16；原始记录保留于 backend-selection.json。')
reproduce = ('复现：npm ci --ignore-scripts → docker compose up -d --wait (或 WSL 启动脚本) → npm run models → npm run dev；'
             'npm test / npm run test:e2e / npm run benchmark。完整条件、分阶段原始记录和源码见 stepfun/README.md、'
             'evidence/benchmark-raw.json、evidence/database.json、evidence/e2e.json。')

md = ['# LocalSeek · 一页技术说明', '', environment, '']
for title, text in sections: md += [f'**{title}。** {text}', '']
md += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
md += ['| ' + ' | '.join(row) + ' |' for row in rows]
md += ['', method, '', fresh, '', state, '', reproduce, '']
(root / 'TECHNICAL.md').write_text('\n'.join(md), encoding='utf8')

fontfile = os.environ.get('NOTE_FONT', 'C:/Windows/Fonts/msyh.ttc')
if Path(fontfile).exists():
    pdfmetrics.registerFont(TTFont('Note', fontfile, subfontIndex=0))
else:
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    fontfile = None
font = 'Note' if fontfile else 'STSong-Light'
style = ParagraphStyle('text', fontName=font, fontSize=8.1, leading=12.3, textColor=colors.HexColor('#3f5146'), wordWrap='CJK', spaceAfter=7)
tiny = ParagraphStyle('tiny', parent=style, fontSize=7.2, leading=10.5, textColor=colors.HexColor('#71816b'))
title_style = ParagraphStyle('title', parent=style, fontSize=23, leading=29, textColor=colors.HexColor('#294b35'), spaceAfter=7)
heading = ParagraphStyle('heading', parent=style, fontSize=9.4, leading=14, textColor=colors.HexColor('#294b35'), spaceBefore=4, spaceAfter=3)
out = root / 'output/pdf'; out.mkdir(parents=True, exist_ok=True)
target = out / 'LocalSeek-technical-note.pdf'
doc = SimpleDocTemplate(str(target), pagesize=A4, rightMargin=34, leftMargin=34, topMargin=31, bottomMargin=31,
                        title='LocalSeek - 一页技术说明', author='LocalSeek / seekdb competition')
story = [Paragraph('LocalSeek', title_style), Paragraph('端侧知识库 / seekdb × 阶跃星辰 / 一页技术说明', heading),
         Paragraph(escape(environment), tiny), Spacer(1, 6)]
for label, text in sections:
    story += [Paragraph(label, heading), Paragraph(escape(text), style)]
cell = ParagraphStyle('cell', parent=tiny, fontSize=7.1, leading=9.3, spaceAfter=0)
table = Table([[Paragraph(escape(x), cell) for x in row] for row in [headers] + rows], colWidths=[134, 58, 66, 51, 56, 62, 60], repeatRows=1)
table.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e6eedf')),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f6f8f2'), colors.white]),
    ('GRID', (0, 0), (-1, -1), .35, colors.HexColor('#dce4d3')),
    ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
]))
story += [Spacer(1, 3), table, Spacer(1, 7), Paragraph(escape(method), tiny), Paragraph(escape(fresh), style),
          Paragraph(escape(state), style), Paragraph(escape(reproduce), tiny)]
def footer(canvas, document):
    canvas.setStrokeColor(colors.HexColor('#dce4d3')); canvas.line(34, 25, A4[0] - 34, 25)
    canvas.setFont(font, 6.4); canvas.setFillColor(colors.HexColor('#8a987e'))
    canvas.drawString(34, 14, 'LOCAL INFERENCE / REAL SEEKDB / REPRODUCIBLE MEASUREMENTS')
    canvas.drawRightString(A4[0] - 34, 14, '2026 / 11 / 07     01')
doc.build(story, onFirstPage=footer, onLaterPages=footer)
pages = len(PdfReader(target).pages)
assert pages == 1, f'Expected exactly one page, got {pages}'
print(target, f'({pages} page)')
