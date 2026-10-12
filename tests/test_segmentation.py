import json

import numpy as np
import pytest
from iotdesign.segmentation import attach_segmentation, pack_frame, render_preview


def annotation(mask, quality=0.95):
    return {'segmentation': mask, 'area': int(mask.sum()), 'bbox': [0, 0, 4, 3],
            'predicted_iou': quality, 'stability_score': 0.97}


def test_overlapping_masks_roundtrip_without_merging():
    image = np.zeros((3, 4, 3), dtype=np.uint8)
    first = np.zeros((3, 4), dtype=bool)
    first[:, :3] = True
    second = np.zeros((3, 4), dtype=bool)
    second[:, 2:] = True
    objects, packed = pack_frame(image, [annotation(first), annotation(second, 0.9)], 2)
    decoded = np.unpackbits(packed, axis=1, count=12, bitorder='little').reshape(2, 3, 4)
    np.testing.assert_array_equal(decoded[0], first)
    np.testing.assert_array_equal(decoded[1], second)
    assert objects[0]['id'] == 'f0002-o001'
    assert objects[0]['label'] is None and objects[0]['moving'] is None
    assert render_preview(image, objects, packed).size == (4, 3)


def test_empty_frame_and_invalid_shape():
    image = np.zeros((3, 4, 3), dtype=np.uint8)
    objects, packed = pack_frame(image, [], 0)
    assert objects == [] and packed.shape == (0, 2)
    with pytest.raises(ValueError, match='dimensions'):
        pack_frame(image, [annotation(np.ones((2, 4), dtype=bool))], 0)


def test_grounded_mirror_keeps_provenance_without_confirming_material():
    image = np.zeros((3, 4, 3), dtype=np.uint8)
    ann = annotation(np.ones((3, 4), dtype=bool))
    ann.update(label='Mirror', label_score=0.6, label_model='grounding-dino-tiny', label_prompt='mirror.')
    objects, _ = pack_frame(image, [ann], 0)
    assert objects[0]['label_prompt'] == 'mirror.'
    assert objects[0]['label_model'] == 'grounding-dino-tiny'
    assert objects[0]['label_status'] == 'proposed'
    assert objects[0]['reflective'] is None
    assert objects[0]['moving'] is None


def test_attach_keeps_geometry_and_adds_segmentation_artifacts(tmp_path):
    manifest = {'artifacts': {'point_cloud': 'cloud.ply'}, 'metrics': {'point_count': 12}}
    (tmp_path / 'scene.json').write_text(json.dumps(manifest), encoding='utf-8')
    attach_segmentation(tmp_path, {'regions': 3})
    updated = json.loads((tmp_path / 'scene.json').read_text())
    assert updated['artifacts']['point_cloud'] == 'cloud.ply'
    assert updated['artifacts']['object_metadata'] == 'objects.json'
    assert updated['metrics']['point_count'] == 12
    assert updated['metrics']['segmentation']['regions'] == 3
