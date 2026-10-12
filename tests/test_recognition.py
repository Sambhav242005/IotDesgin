from iotdesign.recognition import merge_mirror_proposals


def proposal(box, label='Mirror', score=0.8):
    return {'box': box, 'label': label, 'label_score': score}


def test_mirror_merge_preserves_categories_and_separate_regions():
    person = proposal([0, 0, 100, 100], 'Person')
    objects = [person, proposal([0, 0, 100, 100], score=0.6)]
    mirrors = [proposal([1, 1, 99, 99]), proposal([200, 0, 300, 100])]
    result = merge_mirror_proposals(objects, mirrors)
    assert result == [person, *mirrors]


def test_invalid_mirror_boxes_are_rejected():
    assert merge_mirror_proposals([], [proposal([3, 3, 2, 2]), proposal([0, 0, float('nan'), 1])]) == []
