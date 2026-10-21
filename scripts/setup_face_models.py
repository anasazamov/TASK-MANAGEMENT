#!/usr/bin/env python3
"""Download the OpenCV face models used for attendance face matching.

Run once on any machine that will do face matching (the Linux server, or a dev
machine for testing):

    python scripts/setup_face_models.py

It fetches YuNet (detector) and SFace (recognizer) into ../face_models, or into
FACE_MODELS_DIR if that is set. Standard library only.
"""
import os
import sys
import urllib.request
from pathlib import Path

BASE = 'https://github.com/opencv/opencv_zoo/raw/main/models'
MODELS = {
    'face_detection_yunet_2023mar.onnx': f'{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx',
    'face_recognition_sface_2021dec.onnx': f'{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx',
}


def main():
    target = Path(os.environ.get('FACE_MODELS_DIR') or (Path(__file__).resolve().parent.parent / 'face_models'))
    target.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        dest = target / name
        if dest.exists() and dest.stat().st_size > 50000:
            print(f'bor: {dest.name} ({dest.stat().st_size} bayt)')
            continue
        print(f'yuklanmoqda: {name} ...')
        urllib.request.urlretrieve(url, dest)
        size = dest.stat().st_size
        if size < 50000:  # a git-lfs pointer file would be tiny
            print(f'XATO: {name} juda kichik ({size} bayt). URL yoki tarmoqni tekshiring.')
            return 1
        print(f'tayyor: {dest.name} ({size} bayt)')
    print('Barcha yuz modellari shu yerda:', target)
    return 0


if __name__ == '__main__':
    sys.exit(main())
