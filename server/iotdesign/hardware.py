"""Conservative Windows runtime profiles; low-memory inference is not a device-quality guarantee."""
import argparse
import json
import sys
from pathlib import Path

import psutil


def detect_hardware():
    memory = psutil.virtual_memory()
    result = {'ram_gb': memory.total / 1024**3, 'available_ram_gb': memory.available / 1024**3,
              'cuda_available': False, 'gpu_name': None, 'vram_gb': 0, 'free_vram_gb': 0,
              'capability': [0, 0], 'error': None}
    try:
        import torch
        result['torch_version'] = torch.__version__
        if torch.cuda.is_available():
            # Availability alone does not prove this wheel contains kernels for a legacy GPU.
            probe = torch.ones((32, 32), device='cuda', dtype=torch.float16)
            value = (probe @ probe).float().mean().item()
            if value != 32:
                raise RuntimeError('CUDA FP16 matrix multiplication smoke test failed')
            del probe
            torch.cuda.empty_cache()
            free, total = torch.cuda.mem_get_info()
            result.update(cuda_available=True, gpu_name=torch.cuda.get_device_name(0),
                          vram_gb=total / 1024**3, free_vram_gb=free / 1024**3,
                          capability=list(torch.cuda.get_device_capability(0)))
    except (ImportError, RuntimeError, OSError, AssertionError) as exc:
        result['error'] = str(exc)
    return result


def select_profile(hardware, requested='Auto'):
    names = {'Auto', 'High', 'Balanced', 'Low', 'ServerOnly'}
    if requested not in names:
        raise ValueError('Unknown Windows hardware profile')
    reason = None
    if (not hardware['cuda_available'] or hardware['vram_gb'] < 3.5
            or hardware['ram_gb'] < 12 or hardware.get('available_ram_gb', hardware['ram_gb']) < 11
            or hardware['free_vram_gb'] < 3.1 or hardware['capability'][0] < 6):
        name = 'ServerOnly'
        reason = 'CUDA unavailable or insufficient current GPU/system memory. Capture server remains usable.'
    elif hardware['vram_gb'] >= 10 and hardware['ram_gb'] >= 24 and hardware['capability'][0] >= 8:
        name = 'High'
    elif hardware['vram_gb'] >= 6 and hardware['ram_gb'] >= 14 and hardware['capability'][0] >= 7:
        name = 'Balanced'
    else:
        name = 'Low'
    rank = {'ServerOnly': 0, 'Low': 1, 'Balanced': 2, 'High': 3}
    if requested != 'Auto':
        if rank[requested] > rank[name]:
            raise ValueError(f'{requested} exceeds the detected safe profile {name}; close GPU apps or use Auto')
        name = requested
    presets = {
        'High': (336, 12, 'fp32', 500000, 'small', 1024),
        'Balanced': (224, 8, 'fp16', 300000, 'small', 896),
        'Low': (168, 8, 'fp16', 150000, 'tiny', 768),
        'ServerOnly': (0, 0, 'none', 0, 'none', 0),
    }
    resolution, frames, precision, points, sam, edge = presets[name]
    budget = round(min(hardware['vram_gb'] * 0.82, max(0, hardware['free_vram_gb'] - 0.4),
                       {'High': 10, 'Balanced': 6, 'Low': 3.2, 'ServerOnly': 0}[name]), 2)
    return {'version': 1, 'profile': name, 'worker_enabled': name != 'ServerOnly',
            'reason': reason, 'hardware': hardware, 'resolution': resolution, 'max_frames': frames,
            'weight_precision': precision, 'max_points': points,
            'sam_model': f'facebook/sam2.1-hiera-{sam}' if sam != 'none' else None,
            'segmentation_edge': edge, 'semantic_objects': name != 'ServerOnly',
            'vram_budget_gb': budget if name != 'ServerOnly' else 0,
            'low_profile_device_validation_pending': name == 'Low'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='Auto')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cuda-check', action='store_true')
    args = parser.parse_args()
    hardware = detect_hardware()
    if args.cuda_check:
        sys.exit(0 if hardware['cuda_available'] else 1)
    profile = select_profile(hardware, args.profile)
    rendered = json.dumps(profile, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
