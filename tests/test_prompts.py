import numpy as np
from iotdesign.prompts import mirror_points, predict_region, rank_mask


def test_edge_box_does_not_label_inside_pixels_as_background():
    points, labels = mirror_points([0, 0, 40, 30], (30, 40, 3))
    assert len(points) == 9
    assert (labels == 1).all()
    assert ((points >= 0) & (points < [40, 30])).all()
    points, labels = mirror_points([10, 10, 30, 20], (30, 40))
    assert (labels == 0).sum() == 4
    for x, y in points[labels == 0]:
        assert x < 10 or x > 30 or y < 10 or y > 20


def test_surface_ranking_penalizes_leakage():
    surface = np.zeros((10, 10), dtype=bool)
    surface[2:8, 2:8] = True
    tiny = np.zeros_like(surface)
    tiny[4:6, 4:6] = True
    assert rank_mask(surface, 0.8, [2, 2, 8, 8]) > rank_mask(tiny, 0.9, [2, 2, 8, 8])
    assert rank_mask(surface, 0.8, [2, 2, 8, 8]) > rank_mask(np.ones_like(surface), 0.8, [2, 2, 8, 8])


class Predictor:
    def __init__(self, bad_refinement=False):
        self.calls = []
        self.bad_refinement = bad_refinement

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        mask = np.zeros((10, 10), dtype=bool)
        mask[2:8, 2:8] = True
        score = 0.8
        if 'point_coords' in kwargs:
            score = 0.1 if self.bad_refinement else 0.9
        return np.stack([mask]), np.array([score]), np.zeros((1, 256, 256))


def test_refinement_reuses_logits_and_records_proposal_points():
    predictor = Predictor()
    result = predict_region(predictor, {'box': [2, 2, 8, 8], 'label': 'Mirror'}, (10, 10))
    assert len(predictor.calls) == 3
    assert predictor.calls[-1]['mask_input'].shape == (1, 256, 256)
    assert result['candidate_count'] == 3
    assert result['prompt_points_are_heuristic']
    assert 0 in result['point_labels'] and 1 in result['point_labels']


def test_bad_refinement_retains_box_candidate_and_nonmirrors_stay_box_only():
    predictor = Predictor(bad_refinement=True)
    result = predict_region(predictor, {'box': [2, 2, 8, 8], 'label': 'Mirror'}, (10, 10))
    assert result['prompt_strategy'] == 'box'
    predictor = Predictor()
    predict_region(predictor, {'box': [2, 2, 8, 8], 'label': 'Person'}, (10, 10))
    assert len(predictor.calls) == 1
