"""Official VGGT inference and portable, unscaled scene artifacts."""
import gc
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psutil
from PIL import Image
from plyfile import PlyData, PlyElement


def evenly_spaced(items, count):
    if count >= len(items):
        return list(items)
    return [items[i] for i in np.linspace(0, len(items) - 1, count, dtype=int)]


class MemorySampler:
    def __init__(self):
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        process = psutil.Process()
        while not self.stop.is_set():
            self.peak = max(self.peak, process.memory_info().rss)
            self.stop.wait(0.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()


def export_scene(output, scan_id, job_id, points, colors, cameras, metrics, preview, provenance=None):
    output.mkdir(parents=True, exist_ok=True)
    points = np.asarray(points, dtype=np.float32)
    colors = np.asarray(colors, dtype=np.uint8)
    mask = np.isfinite(points).all(axis=1)
    points, colors = points[mask], colors[mask]
    if not len(points):
        raise ValueError("No usable geometry; capture more overlapping, well-lit views")
    vertices = np.empty(len(points), dtype=[('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
                                           ('red', 'u1'), ('green', 'u1'), ('blue', 'u1')])
    for index, name in enumerate(('x', 'y', 'z')):
        vertices[name] = points[:, index]
    for index, name in enumerate(('red', 'green', 'blue')):
        vertices[name] = colors[:, index]
    PlyData([PlyElement.describe(vertices, 'vertex')], text=False).write(str(output / 'cloud.ply'))
    (output / 'cameras.json').write_text(json.dumps(cameras, indent=2), encoding='utf-8')
    arrays = {'points': points, 'colors': colors}
    if provenance is not None:
        arrays.update({key: value[mask] for key, value in provenance.items()})
    np.savez_compressed(output / 'viewer.npz', **arrays)
    Image.fromarray(np.asarray(preview, dtype=np.uint8)).save(output / 'preview.jpg', quality=85)
    metrics['point_count'] = len(points)
    manifest = {
        'version': 1, 'scan_id': scan_id, 'job_id': job_id,
        'representation': 'point_cloud', 'coordinates': 'unscaled',
        'camera_convention': 'opencv_camera_to_world',
        'artifacts': {'point_cloud': 'cloud.ply', 'cameras': 'cameras.json',
                      'preview': 'preview.jpg', 'viewer': 'viewer.npz'},
        'metrics': metrics,
    }
    # Manifest is the completion marker, written last.
    temporary = output / 'scene.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(output / 'scene.json')
    return metrics


def check_gpu(repo):
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable. Run Start-IoTDesign.cmd and check the NVIDIA driver.')
    if not (repo / 'vggt' / 'models' / 'vggt.py').exists():
        raise RuntimeError('Official VGGT checkout missing. Run Start-IoTDesign.cmd.')


def reconstruct(paths, output, scan_id, job_id, count=12, repo=Path('vendor/vggt'),
                resolution=336, stage=lambda value: None, retry=True, weight_precision='fp32',
                max_points=500000):
    import torch
    from huggingface_hub import hf_hub_download

    repo = repo.resolve()
    check_gpu(repo)
    if resolution < 140 or resolution % 14:
        raise ValueError('Resolution must be a multiple of 14, at least 140')
    if weight_precision not in {'fp32', 'fp16'} or max_points < 1:
        raise ValueError('Unsupported weight precision or point limit')
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from vggt.models.vggt import VGGT
    from vggt.utils.geometry import unproject_depth_map_to_point_map
    from vggt.utils.load_fn import load_and_preprocess_images
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri

    started = time.perf_counter()
    with MemorySampler() as ram:
        stage('loading_model')
        checkpoint = hf_hub_download('facebook/VGGT-1B', 'model.pt')
        state = torch.load(checkpoint, map_location='cpu', weights_only=True, mmap=True)
        # VGGT's initializers use operations such as Tensor.item() that are not
        # supported on meta tensors. Build on CPU; assign=True below replaces
        # initialized parameter storage with checkpoint tensors without copying.
        model = VGGT(enable_point=False, enable_track=False)
        filtered = {key: value for key, value in state.items()
                    if not key.startswith(('point_head.', 'track_head.'))}
        model.load_state_dict(filtered, strict=True, assign=True)
        del state, filtered
        # The geometry heads explicitly disable autocast and require float32 weights.
        # Store only the aggregator in FP16 on memory-limited hardware.
        if weight_precision == 'fp16':
            model.aggregator.half()
        model = model.eval().to('cuda')
        dtype = (torch.float16 if weight_precision == 'fp16' else
                 torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16)
        selected_count = min(count, len(paths))
        attempt_counts = [selected_count]
        if retry and selected_count > 8:
            attempt_counts.append(8)
        attempt_records = []
        peak_vram = 0
        try:
            for attempt_count in attempt_counts:
                images, prediction = None, None
                selected = evenly_spaced(paths, attempt_count)
                stage(f'preprocessing_{attempt_count}_frames')
                images = load_and_preprocess_images([str(p) for p in selected])
                height = max(14, round(images.shape[-2] * resolution / images.shape[-1] / 14) * 14)
                images = torch.nn.functional.interpolate(images, (height, resolution), mode='bilinear',
                                                        align_corners=False).to('cuda')
                torch.cuda.reset_peak_memory_stats()
                stage(f'inference_{attempt_count}_frames')
                try:
                    with torch.inference_mode(), torch.autocast(device_type='cuda', dtype=dtype):
                        prediction = model(images)
                        extrinsic, intrinsic = pose_encoding_to_extri_intri(prediction['pose_enc'], images.shape[-2:])
                    torch.cuda.synchronize()
                    peak_vram = max(peak_vram, torch.cuda.max_memory_allocated() / 1024**2)
                    depth = prediction['depth'][0].float().cpu().numpy()
                    confidence = prediction['depth_conf'][0].float().cpu().numpy()
                    extrinsic, intrinsic = extrinsic[0].float().cpu().numpy(), intrinsic[0].float().cpu().numpy()
                    rgb = (images.float().cpu().numpy().transpose(0, 2, 3, 1) * 255).astype(np.uint8)
                    attempt_records.append({'frames': attempt_count, 'state': 'completed'})
                    break
                except torch.cuda.OutOfMemoryError:
                    peak_vram = max(peak_vram, torch.cuda.max_memory_allocated() / 1024**2)
                    attempt_records.append({'frames': attempt_count, 'state': 'out_of_memory'})
                    if attempt_count == attempt_counts[-1]:
                        raise RuntimeError(f'GPU memory exhausted with {attempt_count} frames at {resolution}px. '
                                           'Close GPU applications or lower the worker resolution.') from None
                finally:
                    del prediction, images
                    gc.collect()
                    torch.cuda.empty_cache()
            stage('exporting_geometry')
            points = unproject_depth_map_to_point_map(depth, extrinsic, intrinsic)
            threshold = np.percentile(confidence[np.isfinite(confidence)], 50)
            mask = np.isfinite(points).all(axis=-1) & (confidence >= threshold) & (depth[..., 0] > 0)
            from .object_geometry import source_coordinates
            sizes = []
            for path in selected:
                with Image.open(path) as source:
                    sizes.append(source.size)
            source_uv = source_coordinates(sizes, height, resolution)
            mask &= (source_uv >= 0).all(axis=-1) & (source_uv < 1).all(axis=-1)
            frame_index = np.broadcast_to(np.arange(len(selected))[:, None, None], mask.shape)[mask]
            source_uv = source_uv[mask]
            points, colors = points[mask], rgb[mask]
            # Deterministic cap keeps browser scenes manageable; original images are retained.
            if len(points) > max_points:
                selection = np.linspace(0, len(points) - 1, max_points, dtype=int)
                points, colors = points[selection], colors[selection]
                frame_index, source_uv = frame_index[selection], source_uv[selection]
            cameras = []
            for index, ext in enumerate(extrinsic):
                homogeneous = np.eye(4)
                homogeneous[:3] = ext
                cameras.append({'frame': Path(selected[index]).stem,
                                'camera_to_world': np.linalg.inv(homogeneous).tolist(),
                                'intrinsics': intrinsic[index].tolist(),
                                'width': resolution, 'height': height})
            metrics = {'backend': 'official_vggt', 'device': torch.cuda.get_device_name(0),
                       'requested_frames': count, 'input_frames': len(selected),
                       'input_width': resolution, 'input_height': height,
                       'peak_vram_mb': peak_vram, 'peak_ram_mb': ram.peak / 1024**2,
                       'duration_seconds': time.perf_counter() - started,
                       'weight_precision': weight_precision, 'geometry_heads_precision': 'fp32',
                       'max_points': max_points,
                       'attempts': attempt_records}
            return export_scene(output, scan_id, job_id, points, colors, cameras, metrics, rgb[0],
                                provenance={'frame_index': frame_index, 'source_uv': source_uv})
        finally:
            del model
            gc.collect()
            torch.cuda.empty_cache()
