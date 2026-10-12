"""Local Open Images YOLO category proposals; these are not confirmed material labels."""
import gc
from pathlib import Path

import numpy as np
from PIL import Image

DETECTOR = 'yolov8n-oiv7.pt'
MIRROR_DETECTOR = 'IDEA-Research/grounding-dino-tiny'


def merge_mirror_proposals(objects, mirrors, threshold=0.5):
    """Deduplicate same-category prompt boxes; never discard other object categories."""
    result = [obj for obj in objects if obj['label'].lower() != 'mirror']
    candidates = mirrors + [obj for obj in objects if obj['label'].lower() == 'mirror']
    kept = []
    for candidate in sorted(candidates, key=lambda obj: -obj['label_score']):
        box = np.asarray(candidate['box'], dtype=float)
        if not np.isfinite(box).all() or np.any(box[2:] <= box[:2]):
            continue
        duplicate = False
        for other in kept:
            other_box = np.asarray(other['box'])
            intersection = np.maximum(0, np.minimum(box[2:], other_box[2:])
                                      - np.maximum(box[:2], other_box[:2])).prod()
            union = (box[2:] - box[:2]).prod() + (other_box[2:] - other_box[:2]).prod() - intersection
            if intersection / union > threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return result + kept[:16]


def detect_mirrors(paths, max_edge=1024, confidence=0.25, text_threshold=0.25,
                   stage=lambda value: None):
    """Targeted text detection on CPU, sequentially; no generated descriptions or custom CUDA kernels."""
    import torch
    from transformers import AutoConfig, AutoModelForZeroShotObjectDetection, AutoProcessor

    stage('loading_mirror_detector')
    processor = AutoProcessor.from_pretrained(MIRROR_DETECTOR)
    config = AutoConfig.from_pretrained(MIRROR_DETECTOR)
    config.disable_custom_kernels = True
    model = AutoModelForZeroShotObjectDetection.from_pretrained(MIRROR_DETECTOR, config=config).eval()
    frames = []
    try:
        for index, path in enumerate(paths):
            stage(f'mirror_recognition_{index + 1}_of_{len(paths)}')
            rgb, _ = read_rgb(path, max_edge)
            height, width = rgb.shape[:2]
            inputs = processor(images=Image.fromarray(rgb), text='mirror.', return_tensors='pt',
                               size={'shortest_edge': min(512, max_edge), 'longest_edge': min(768, max_edge)})
            with torch.inference_mode():
                outputs = model(**inputs)
            result = processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, threshold=confidence, text_threshold=text_threshold,
                target_sizes=[(height, width)])[0]
            proposals = []
            for box, score, label in zip(result['boxes'], result['scores'], result['text_labels']):
                if 'mirror' not in label.lower():
                    continue
                coordinates = box.detach().cpu().numpy().clip([0, 0, 0, 0], [width, height, width, height])
                proposals.append({'label': 'Mirror', 'label_score': float(score), 'box': coordinates.tolist(),
                                  'label_model': MIRROR_DETECTOR, 'label_prompt': 'mirror.'})
            frames.append(merge_mirror_proposals([], proposals))
            del inputs, outputs, result
        return frames
    finally:
        del model, processor
        gc.collect()


def read_rgb(path, max_edge=1024):
    with Image.open(path) as source:
        image = source.convert('RGB')
        size = image.size
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return np.array(image), size


def detect_objects(paths, model_path, max_edge=1024, confidence=0.3, stage=lambda value: None):
    import torch
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError('Install the semantic extra: uv pip install --python .venv/Scripts/python.exe '
                           '".[semantic]"') from exc
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    stage('loading_yolo')
    model = YOLO(str(model_path))
    if not any(str(name).lower() == 'mirror' for name in model.names.values()):
        raise RuntimeError('The detector checkpoint must contain a Mirror class (Open Images V7).')
    frames = []
    try:
        for index, path in enumerate(paths):
            stage(f'recognition_{index + 1}_of_{len(paths)}')
            rgb, _ = read_rgb(path, max_edge)
            # Ultralytics NumPy inputs use BGR; SAM 2 inputs use RGB.
            result = model.predict(source=rgb[:, :, ::-1].copy(), device=0, conf=confidence,
                                   max_det=64, verbose=False)[0]
            boxes = result.boxes
            frames.append([{'label': result.names[int(cls)], 'label_score': float(score),
                            'box': box.tolist(), 'label_model': model_path.name}
                           for box, cls, score in zip(boxes.xyxy.cpu().numpy(),
                                                     boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy())])
            del result, boxes
        return frames
    finally:
        del model
        gc.collect()
        torch.cuda.empty_cache()
