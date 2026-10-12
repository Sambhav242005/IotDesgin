"""Optional official SAM 2 image masks; IDs are local to each frame, not object tracks."""
import gc
import json
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from PIL import Image

DEFAULT_MODEL = 'facebook/sam2.1-hiera-small'
ARTIFACTS = {'object_masks': 'objects.npz', 'object_metadata': 'objects.json',
             'object_preview': 'objects-preview.jpg', 'object_previews': 'objects-previews.zip'}


def pack_frame(image, annotations, frame_index, max_masks=64, min_area_fraction=0.001):
    """Preserve overlapping masks independently rather than merging object boundaries."""
    height, width = image.shape[:2]
    candidates = sorted(annotations, key=lambda a: (-float(a['predicted_iou']), -int(a['area'])))
    objects, masks = [], []
    for ann in candidates:
        mask = np.asarray(ann['segmentation'], dtype=bool)
        if mask.shape != (height, width):
            raise ValueError('SAM 2 mask dimensions do not match the input image')
        area = int(mask.sum())
        if area < max(1, height * width * min_area_fraction):
            continue
        index = len(masks)
        masks.append(np.packbits(mask.reshape(-1), bitorder='little'))
        objects.append({'id': f'f{frame_index:04d}-o{index + 1:03d}', 'mask_index': index,
                        'label': ann.get('label'), 'label_score': ann.get('label_score'),
                        'label_model': ann.get('label_model'),
                        'label_prompt': ann.get('label_prompt'),
                        'sam_prompt': {key: ann.get(key) for key in ('prompt_strategy', 'point_coords',
                                      'point_labels', 'prompt_points_are_heuristic', 'selection_score',
                                      'candidate_count')},
                        'label_status': 'proposed' if ann.get('label') else 'unknown',
                        'moving': None, 'reflective': None,
                        'area_pixels': area, 'bbox_xywh': [float(v) for v in ann['bbox']],
                        'predicted_iou': float(ann['predicted_iou']),
                        'stability_score': (float(ann['stability_score'])
                                            if ann.get('stability_score') is not None else None),
                        'color_rgb': np.random.default_rng(index + 42).integers(50, 240, 3).tolist()})
        if len(masks) >= max_masks:
            break
    packed = np.stack(masks) if masks else np.empty((0, (height * width + 7) // 8), dtype=np.uint8)
    return objects, packed


def render_preview(image, objects, packed):
    height, width = image.shape[:2]
    colors = np.array([obj.get('color_rgb', [120, 220, 140]) for obj in objects])
    output = image.astype(np.float32).copy()
    # Paint large regions first, so smaller overlapping regions remain visible.
    for index in sorted(range(len(objects)), key=lambda i: -objects[i]['area_pixels']):
        mask = np.unpackbits(packed[index], bitorder='little', count=height * width).reshape(height, width)
        selected = mask.astype(bool)
        output[selected] = output[selected] * 0.55 + colors[index] * 0.45
        padded = np.pad(selected, 1, constant_values=False)
        interior = (selected & padded[:-2, 1:-1] & padded[2:, 1:-1]
                    & padded[1:-1, :-2] & padded[1:-1, 2:])
        output[selected & ~interior] = colors[index]
    return Image.fromarray(output.astype(np.uint8))


def segment_frames(paths, output, model_id=DEFAULT_MODEL, max_edge=1024, points_per_side=16,
                   max_masks=64, stage=lambda value: None, semantic=False,
                   detector_path=Path('data/models/yolov8n-oiv7.pt'), mirror_refinement=True):
    """Run one frame at a time and write a reloadable, nonsemantic segmentation bundle."""
    if not paths:
        raise ValueError('Segmentation requires at least one image')
    if max_edge < 64 or points_per_side < 1 or max_masks < 1:
        raise ValueError('Invalid segmentation size, prompt-grid size, or mask limit')
    try:
        import torch
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        from sam2.build_sam import build_sam2_hf
    except ImportError as exc:
        raise RuntimeError('Install official SAM 2 first; see README.md object segmentation setup') from exc
    if not torch.cuda.is_available():
        raise RuntimeError('SAM 2 segmentation requires CUDA in this application')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    detections = None
    if semantic:
        from .recognition import detect_mirrors, detect_objects, merge_mirror_proposals
        detections = detect_objects(paths, detector_path, max_edge=max_edge, stage=stage)
        mirrors = detect_mirrors(paths, max_edge=max_edge, stage=stage)
        detections = [merge_mirror_proposals(objects, candidates)
                      for objects, candidates in zip(detections, mirrors)]
    stage('loading_sam2')
    # Disable optional custom-kernel postprocessing for the Windows-native path.
    model = build_sam2_hf(model_id, device='cuda', apply_postprocessing=False)
    if semantic:
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        generator = SAM2ImagePredictor(model)
    else:
        generator = SAM2AutomaticMaskGenerator(model, points_per_side=points_per_side,
                                              points_per_batch=16, crop_n_layers=0,
                                              min_mask_region_area=0, output_mode='binary_mask')
    arrays, frames, previews = {}, [], {}
    try:
        dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
        for index, path in enumerate(paths):
            stage(f'segmentation_{index + 1}_of_{len(paths)}')
            with Image.open(path) as source:
                image = source.convert('RGB')
                original_size = image.size
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                rgb = np.array(image)
            with torch.inference_mode(), torch.autocast('cuda', dtype=dtype):
                if semantic:
                    annotations = []
                    if detections[index]:
                        generator.set_image(rgb)
                    for detection in detections[index]:
                        from .prompts import predict_region
                        box = np.array(detection['box'])
                        proposal = predict_region(generator, detection, rgb.shape, mirror_refinement)
                        if proposal is None:
                            continue
                        mask = proposal['segmentation']
                        annotations.append({**detection, **proposal, 'area': int(mask.sum()),
                                            'bbox': [box[0], box[1], box[2] - box[0], box[3] - box[1]],
                                            'stability_score': None})
                else:
                    annotations = generator.generate(rgb)
            objects, packed = pack_frame(rgb, annotations, index, max_masks=max_masks,
                                        min_area_fraction=0 if semantic else 0.001)
            key = f'frame_{index:04d}'
            arrays[key] = packed
            frames.append({'frame_id': Path(path).stem, 'source_width': original_size[0],
                           'source_height': original_size[1], 'width': rgb.shape[1],
                           'height': rgb.shape[0], 'mask_array': key, 'objects': objects,
                           'preview_archive_member': f'{key}.jpg'})
            preview = render_preview(rgb, objects, packed)
            buffer = BytesIO()
            preview.save(buffer, format='JPEG', quality=90)
            previews[f'{key}.jpg'] = buffer.getvalue()
            if index == 0:
                (output / 'objects-preview.jpg').write_bytes(buffer.getvalue())
            del annotations
        torch.cuda.synchronize()
        metrics = {'model': model_id, 'frames': len(frames),
                   'semantic_labels': semantic,
                   'mirror_prompt_mode': ('surface_hypotheses' if mirror_refinement else 'box_only')
                   if semantic else 'not_applicable',
                   'regions': sum(len(f['objects']) for f in frames),
                   'duration_seconds': time.perf_counter() - started,
                   'peak_vram_mb': torch.cuda.max_memory_allocated() / 1024**2}
        with (output / 'objects.npz.tmp').open('wb') as handle:
            np.savez_compressed(handle, **arrays)
        (output / 'objects.npz.tmp').replace(output / 'objects.npz')
        with ZipFile(output / 'objects-previews.zip.tmp', 'w') as archive:
            for name, data in previews.items():
                archive.writestr(name, data)
        (output / 'objects-previews.zip.tmp').replace(output / 'objects-previews.zip')
        manifest = {'version': 1, 'backend': 'official_sam2', 'metrics': metrics,
                    'id_scope': 'per_frame', 'semantic_labels': semantic, 'motion_detection': False,
                    'reflection_detection': False, 'geometry_filtering': False,
                    'mask_encoding': 'numpy.packbits', 'bitorder': 'little',
                    'coordinate_space': 'resized_image_pixels', 'points_per_side': points_per_side,
                    'max_masks_per_frame': max_masks, 'artifacts': ARTIFACTS, 'frames': frames}
        if semantic:
            manifest['detector'] = {'checkpoint': Path(detector_path).name, 'confidence_threshold': 0.3,
                                    'labels_are_proposals': True}
            from .recognition import MIRROR_DETECTOR
            manifest['mirror_detector'] = {'checkpoint': MIRROR_DETECTOR, 'prompt': 'mirror.',
                                           'confidence_threshold': 0.25, 'text_threshold': 0.25,
                                           'device': 'cpu', 'labels_are_proposals': True,
                                           'completeness_guaranteed': False}
        temporary = output / 'objects.json.tmp'
        temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        temporary.replace(output / 'objects.json')
        return metrics
    finally:
        del generator, model
        gc.collect()
        torch.cuda.empty_cache()


def attach_segmentation(folder, metrics):
    """Attach the bundle only after segmentation succeeded; job completion follows this step."""
    path = Path(folder) / 'scene.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['artifacts'].update(ARTIFACTS)
    manifest['metrics']['segmentation'] = metrics
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(path)
