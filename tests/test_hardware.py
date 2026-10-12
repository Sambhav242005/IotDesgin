from pathlib import Path

import pytest
from iotdesign.hardware import select_profile
from iotdesign.launcher import worker_arguments
from iotdesign.worker import worker_lock


def hardware(vram=4, ram=16, capability=(6, 1), free=3.7, available=12):
    return {'cuda_available': True, 'vram_gb': vram, 'ram_gb': ram, 'free_vram_gb': free,
                'available_ram_gb': available, 'capability': capability}


def test_pascal_selects_low_and_preserves_memory_budget():
    profile = select_profile(hardware())
    assert profile['profile'] == 'Low'
    assert (profile['resolution'], profile['max_frames'], profile['weight_precision']) == (168, 8, 'fp16')
    assert profile['sam_model'] == 'facebook/sam2.1-hiera-tiny'
    assert profile['vram_budget_gb'] <= 3.2
    assert profile['low_profile_device_validation_pending']
    with pytest.raises(ValueError, match='exceeds'):
        select_profile(hardware(), 'High')


@pytest.mark.parametrize('change', [{'cuda_available': False}, {'free_vram_gb': 2.8},
                                    {'available_ram_gb': 9}, {'vram_gb': 3}])
def test_unavailable_resources_keep_capture_server(change):
    device = hardware()
    device.update(change)
    profile = select_profile(device)
    assert profile['profile'] == 'ServerOnly'
    assert not profile['worker_enabled']
    assert profile['sam_model'] is None


def test_high_and_explicit_downgrade():
    device = hardware(vram=12, ram=32, capability=(8, 9), free=10, available=22)
    assert select_profile(device)['profile'] == 'High'
    profile = select_profile(device, 'Low')
    arguments = worker_arguments(profile, Path('C:/Project With Spaces'), Path('C:/Project With Spaces/data'))
    assert arguments[arguments.index('--data') + 1] == 'C:\\Project With Spaces\\data'
    assert arguments[arguments.index('--max-frames') + 1] == '8'
    assert '--semantic-objects' in arguments


def test_busy_worker_lock_reports_actionable_error(tmp_path):
    with worker_lock(tmp_path), pytest.raises(RuntimeError, match='already owns'), worker_lock(tmp_path):
        pytest.fail('A second worker acquired the GPU lock')
    with worker_lock(tmp_path):
        pass
