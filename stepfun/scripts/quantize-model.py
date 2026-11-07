"""Optional local regeneration; submitted measurements use the pinned upstream files.

pip install onnx==1.18.0 onnxruntime==1.22.0 onnxconverter-common==1.16.0
python scripts/quantize-model.py --precision int8
python scripts/quantize-model.py --precision fp16

Writes to .runtime/regenerated, never overwrites verified public model assets.
Regenerated files require new hashes/model identity and a fresh browser benchmark.
"""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--precision', choices=['int8', 'fp16'], default='int8')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
model_dir = root / 'public/models/Xenova/bge-small-zh-v1.5'
source = model_dir / 'onnx/model.onnx'
if not source.is_file():
    parser.error('Run npm run models first')
output_dir = root / '.runtime/regenerated'
output_dir.mkdir(parents=True, exist_ok=True)
config = json.loads((model_dir / 'quantize_config.json').read_text('utf8'))
target = output_dir / ('model_quantized.onnx' if args.precision == 'int8' else 'model_fp16.onnx')
if args.precision == 'int8':
    from onnxruntime.quantization import quantize_dynamic, QuantType
    quantize_dynamic(str(source), str(target), per_channel=config['per_channel'],
                     reduce_range=config['reduce_range'], weight_type=QuantType.QInt8,
                     op_types_to_quantize=config['per_model_config']['model']['op_types'])
else:
    import onnx
    from onnxconverter_common import float16
    onnx.save(float16.convert_float_to_float16(onnx.load(str(source)), keep_io_types=True), str(target))
import onnx
onnx.checker.check_model(str(target))
manifest = {'source': str(source.relative_to(root)), 'precision': args.precision,
            'output': str(target.relative_to(root)), 'bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'note': 'Regenerated variant; not the upstream artifact used by the submitted benchmark.'}
(output_dir / f'{args.precision}-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
print(json.dumps(manifest, indent=2))
