"""Evaluate grounded SAM mirror proposals against downloaded Mirror3D example masks."""
import argparse
import json
from pathlib import Path

import numpy as np
from iotdesign.segmentation import segment_frames
from iotdesign.worker import worker_lock
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sam-size', choices=('tiny', 'small'), default='tiny')
    parser.add_argument('--box-only', action='store_true')
    args = parser.parse_args()
    root = Path('data/benchmark/mirror3d-samples')
    paths = sorted((root / 'images').glob('*.jpg'))
    if len(paths) != 3:
        raise RuntimeError('Download the three Mirror3D example images and masks before this check')
    suffix = f'{args.sam_size}-' + ('box-only' if args.box_only else 'refined')
    output = root / f'grounded-semantic-{suffix}'
    with worker_lock(Path('data')):
        metrics = segment_frames(paths, output, semantic=True, model_id=f'facebook/sam2.1-hiera-{args.sam_size}',
                                 max_edge=768, mirror_refinement=not args.box_only,
                                 stage=lambda value: print(value, flush=True))
    manifest = json.loads((output / 'objects.json').read_text())
    samples = []
    with np.load(output / 'objects.npz') as masks:
        for frame, path in zip(manifest['frames'], paths):
            truth = np.asarray(Image.open(root / 'ground-truth' / f'{path.stem}.png')) > 0
            if truth.ndim == 3:
                truth = truth.any(axis=-1)
            merged = np.zeros((frame['height'], frame['width']), dtype=bool)
            mirrors = [obj for obj in frame['objects'] if obj['label'] == 'Mirror']
            for obj in mirrors:
                merged |= np.unpackbits(masks[frame['mask_array']][obj['mask_index']], bitorder='little',
                                        count=merged.size).reshape(merged.shape).astype(bool)
            resized = np.asarray(Image.fromarray(merged).resize((truth.shape[1], truth.shape[0]),
                                                               Image.Resampling.NEAREST), dtype=bool)
            intersection, union = (resized & truth).sum(), (resized | truth).sum()
            samples.append({'image': path.stem, 'mirror_proposals': len(mirrors),
                            'mirror_mask_iou': float(intersection / union) if union else 0,
                            'labels_are_proposals': True})
    result = {'metrics': metrics, 'samples': samples, 'dataset_scope': 'three Mirror3D example images'}
    (root / f'grounded-results-{suffix}.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
