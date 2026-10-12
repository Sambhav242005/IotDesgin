"""Bounded mirror prompt hypotheses; geometric coverage is not material certainty."""
import numpy as np


def mirror_points(box, shape):
    height, width = shape[:2]
    x0, y0, x1, y1 = np.asarray(box, dtype=float)
    positive = [[x0 + (x1 - x0) * x, y0 + (y1 - y0) * y]
                for y in (0.2, 0.5, 0.8) for x in (0.2, 0.5, 0.8)]
    margin = max(2, min(x1 - x0, y1 - y0) * 0.05)
    negative = [[x0 - margin, (y0 + y1) / 2], [x1 + margin, (y0 + y1) / 2],
                [(x0 + x1) / 2, y0 - margin], [(x0 + x1) / 2, y1 + margin]]
    negative = [point for point in negative if 0 <= point[0] < width and 0 <= point[1] < height]
    return np.asarray(positive + negative, dtype=np.float32), np.asarray([1] * 9 + [0] * len(negative))


def rank_mask(mask, quality, box):
    """Prefer coherent surface hypotheses over tiny reflected contents; retain model quality gate."""
    height, width = mask.shape
    x0, y0 = np.maximum(0, np.floor(box[:2])).astype(int)
    x1, y1 = np.minimum([width, height], np.ceil(box[2:])).astype(int)
    inside = int(mask[y0:y1, x0:x1].sum())
    area = int(mask.sum())
    coverage = inside / max(1, (x1 - x0) * (y1 - y0))
    leakage = (area - inside) / max(1, area)
    return float(quality) + 0.5 * coverage - leakage


def predict_region(predictor, detection, shape, mirror_refinement=True):
    box = np.asarray(detection['box'], dtype=np.float32)
    masks, scores, logits = predictor.predict(box=box, multimask_output=False)
    candidates = [(masks[0], float(scores[0]), 'box', None, None)]
    if mirror_refinement and detection['label'].lower() == 'mirror':
        coords, labels = mirror_points(box, shape)
        masks, scores, logits = predictor.predict(box=box, point_coords=coords, point_labels=labels,
                                                 multimask_output=True)
        candidates.extend((mask, float(score), 'box_with_points', coords, labels)
                          for mask, score in zip(masks, scores))
        best = int(np.argmax([rank_mask(mask, score, box) for mask, score in zip(masks, scores)]))
        refined, refined_scores, _ = predictor.predict(box=box, point_coords=coords, point_labels=labels,
                                                      mask_input=logits[best][None], multimask_output=False)
        candidates.append((refined[0], float(refined_scores[0]), 'points_with_mask_refinement', coords, labels))
    usable = [candidate for candidate in candidates if candidate[1] >= 0.5 and candidate[0].any()]
    if not usable:
        return None
    mask, score, strategy, coords, labels = max(usable, key=lambda c: rank_mask(c[0], c[1], box))
    return {'segmentation': np.asarray(mask, dtype=bool), 'predicted_iou': score,
            'prompt_strategy': strategy, 'point_coords': coords.tolist() if coords is not None else [],
            'point_labels': labels.tolist() if labels is not None else [],
            'prompt_points_are_heuristic': coords is not None,
            'selection_score': rank_mask(mask, score, box), 'candidate_count': len(candidates)}
