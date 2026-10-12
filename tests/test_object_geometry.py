import json

import numpy as np
import pytest
from iotdesign.object_geometry import apply_review, associate_objects, load_arrays, source_coordinates
from iotdesign.reconstruction import export_scene
from iotdesign.segmentation import attach_segmentation, pack_frame


def make_scene(folder):
    folder.mkdir(parents=True, exist_ok=True)
    rgb = np.zeros((2, 4, 3), dtype=np.uint8)
    mask = np.zeros((2, 4), dtype=bool)
    mask[:, :2] = True
    ann = {'segmentation': mask, 'area': 4, 'bbox': [0, 0, 2, 2], 'predicted_iou': 0.9,
           'stability_score': None, 'label': 'Mirror', 'label_score': 0.8}
    objects, packed = pack_frame(rgb, [ann], 0, min_area_fraction=0)
    (folder / 'objects.json').write_text(json.dumps({'frames': [
        {'frame_id': 'frame-a', 'width': 4, 'height': 2, 'mask_array': 'frame_0000', 'objects': objects}]}))
    np.savez_compressed(folder / 'objects.npz', frame_0000=packed)
    points = np.arange(12, dtype=np.float32).reshape(4, 3)
    colors = np.full((4, 3), 100, dtype=np.uint8)
    export_scene(folder, 'scan', 'job', points, colors, [{'frame': 'frame-a'}], {}, rgb,
                 provenance={'frame_index': np.zeros(4, dtype=int),
                             'source_uv': np.array([[0.1, 0.5], [0.4, 0.5], [0.6, 0.5], [0.9, 0.5]])})
    attach_segmentation(folder, {'regions': 1})
    associate_objects(folder)
    return objects[0]['id']


def test_vggt_crop_and_mixed_batch_padding_mapping():
    coords = source_coordinates([(1280, 720), (720, 1280)], 336, 336)
    assert coords.shape == (2, 336, 336, 2)
    assert coords[0, 0, 0, 1] < 0  # landscape padding is not source image content
    assert coords[0, -1, 0, 1] > 1
    assert 0.21 < coords[1, 0, 0, 1] < 0.23  # portrait top was center cropped
    assert 0.77 < coords[1, -1, 0, 1] < 0.79
    assert coords[0, 0, 0, 0] == pytest.approx(0.5 / 336)


def test_mask_association_filter_restore_and_revision_validation(tmp_path):
    object_id = make_scene(tmp_path)
    base = load_arrays(tmp_path / 'viewer-base.npz')
    np.testing.assert_array_equal(base['object_index'], [0, 0, -1, -1])
    meta = json.loads((tmp_path / 'objects.json').read_text())
    assert meta['frames'][0]['objects'][0]['centroid_unscaled'] is None  # mirror geometry untrusted
    with pytest.raises(ValueError, match='Unknown'):
        apply_review(tmp_path, ['unknown'], 0)
    result = apply_review(tmp_path, [object_id], 0)
    assert result['point_count_after'] == 2
    np.testing.assert_array_equal(load_arrays(tmp_path / 'viewer.npz')['points'], base['points'][2:])
    with pytest.raises(ValueError, match='another window'):
        apply_review(tmp_path, [], 0)
    restore = apply_review(tmp_path, [], 1)
    assert restore['point_count_after'] == 4
    np.testing.assert_array_equal(load_arrays(tmp_path / 'viewer.npz')['points'], base['points'])


def test_export_provenance_follows_invalid_point_removal(tmp_path):
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    points = np.array([[0, 0, 1], [np.nan, 0, 0], [2, 1, 1]])
    export_scene(tmp_path, 's', 'j', points, np.zeros((3, 3), dtype=np.uint8), [], {}, rgb,
                 provenance={'frame_index': np.array([2, 3, 4]), 'source_uv': np.zeros((3, 2))})
    arrays = load_arrays(tmp_path / 'viewer.npz')
    np.testing.assert_array_equal(arrays['frame_index'], [2, 4])
