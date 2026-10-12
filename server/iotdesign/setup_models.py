"""Download the exact model files selected by the Windows hardware profile."""
import argparse
import json
from pathlib import Path


def download_models(profile, data):
    if not profile['worker_enabled']:
        print('Capture-only profile: GPU model downloads skipped.', flush=True)
        return
    from huggingface_hub import hf_hub_download
    print('Downloading/checking official VGGT checkpoint...', flush=True)
    hf_hub_download('facebook/VGGT-1B', 'model.pt')
    if profile['semantic_objects']:
        model_id = profile['sam_model']
        if model_id not in {'facebook/sam2.1-hiera-tiny', 'facebook/sam2.1-hiera-small'}:
            raise ValueError('Unsupported installer SAM 2 model')
        filename = model_id.split('/')[-1].replace('-', '_') + '.pt'
        print(f'Downloading/checking {model_id}...', flush=True)
        hf_hub_download(model_id, filename)
        from transformers import AutoConfig, AutoModelForZeroShotObjectDetection, AutoProcessor

        from .recognition import MIRROR_DETECTOR
        print('Downloading/checking targeted Grounding DINO mirror detector...', flush=True)
        processor = AutoProcessor.from_pretrained(MIRROR_DETECTOR)
        config = AutoConfig.from_pretrained(MIRROR_DETECTOR)
        config.disable_custom_kernels = True
        mirror_model = AutoModelForZeroShotObjectDetection.from_pretrained(MIRROR_DETECTOR, config=config)
        del mirror_model, processor
        from ultralytics import YOLO
        path = Path(data) / 'models' / 'yolov8n-oiv7.pt'
        path.parent.mkdir(parents=True, exist_ok=True)
        print('Downloading/checking mirror-capable YOLO Nano...', flush=True)
        detector = YOLO(str(path))
        if not any(str(name).lower() == 'mirror' for name in detector.names.values()):
            raise RuntimeError('Downloaded detector has no Mirror class')
        del detector
    print('Selected models are cached. First-run downloads may require several GB.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile-file', type=Path, required=True)
    parser.add_argument('--data', type=Path, default=Path('data'))
    args = parser.parse_args()
    download_models(json.loads(args.profile_file.read_text(encoding='utf-8-sig')), args.data)


if __name__ == '__main__':
    main()
