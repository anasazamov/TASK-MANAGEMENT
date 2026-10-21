"""Self-hosted face matching with OpenCV (YuNet detector + SFace recognizer).

Needs only opencv-python (a prebuilt wheel on every platform — no insightface, no
onnxruntime, no C++ build tools) plus two small ONNX model files fetched by
`scripts/setup_face_models.py`. It is optional and degrades gracefully: if the
models are missing or no face is found in a frame, every function returns None and
the caller falls back to card-only attendance, exactly like `typesafe`.
"""
import logging
import threading
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Longest side a missed photo is shrunk to before the second detection try.
DETECT_SIDE = 1024

_detector = None
_recognizer = None
_lock = threading.Lock()
_unavailable = False


def _models():
    """Load (once) the YuNet detector and SFace recognizer, or give up for good."""
    global _detector, _recognizer, _unavailable
    if _unavailable:
        return None, None
    if _detector is not None and _recognizer is not None:
        return _detector, _recognizer
    with _lock:
        if _detector is None or _recognizer is None:
            try:
                import cv2
                detector_path = Path(settings.FACE_DETECTOR_MODEL)
                recognizer_path = Path(settings.FACE_RECOGNIZER_MODEL)
                if not detector_path.exists() or not recognizer_path.exists():
                    raise FileNotFoundError('face model files missing; run scripts/setup_face_models.py')
                # Measured: one thread is as fast as all cores for a single frame
                # (~50 ms), so cap it and leave the rest of the CPU to the app.
                cv2.setNumThreads(settings.FACE_CPU_THREADS)
                _detector = cv2.FaceDetectorYN.create(str(detector_path), '', (320, 320), 0.7, 0.3, 5000)
                _recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), '')
            except Exception as error:  # noqa: BLE001 — optional subsystem must never raise
                logger.warning('Face models unavailable: %s', type(error).__name__)
                _unavailable = True
                return None, None
    return _detector, _recognizer


def available():
    return _models()[0] is not None


def embed(image_bytes):
    """The SFace feature vector of the largest detected face, as a list, or None."""
    try:
        import cv2
        import numpy as np
        detector, recognizer = _models()
        if detector is None:
            return None
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        height, width = image.shape[:2]
        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        if (faces is None or len(faces) == 0) and max(height, width) > DETECT_SIDE:
            # A portrait photo of 3200x2400 holds a face too big for YuNet's anchors;
            # door frames already work at full size, so shrink only on a miss.
            scale = DETECT_SIDE / max(height, width)
            image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
            detector.setInputSize((image.shape[1], image.shape[0]))
            _, faces = detector.detect(image)
        if faces is None or len(faces) == 0:
            return None
        largest = max(faces, key=lambda face: face[2] * face[3])  # widest-by-area box
        aligned = recognizer.alignCrop(image, largest)
        feature = recognizer.feature(aligned)
        return [float(value) for value in feature.flatten()]
    except Exception as error:  # noqa: BLE001
        logger.warning('Face embed failed: %s', type(error).__name__)
        return None


def compare(embedding, image_bytes):
    """Cosine similarity of a stored feature vs the face in image_bytes, or None.

    SFace cosine for the same person is typically above ~0.36 (OpenCV's default),
    so FACE_MATCH_THRESHOLD sits there. Returns None when no face could be read.
    """
    other = embed(image_bytes)
    if other is None or not embedding:
        return None
    import numpy as np
    first = np.asarray(embedding, dtype='float32')
    second = np.asarray(other, dtype='float32')
    denom = float(np.linalg.norm(first) * np.linalg.norm(second)) or 1.0
    return float(np.dot(first, second) / denom)
