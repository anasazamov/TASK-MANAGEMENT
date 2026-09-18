"""Local speaker verification. Raw recordings are never persisted or sent for enrollment."""
import base64
import hashlib
import io
import json
import threading
import wave
from functools import lru_cache

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .models import VoiceProfile
from .voice_errors import VoiceError

MODEL_SHA = 'aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2'
RATE = 16000
MAX_SECONDS = 30


def require_profile(user_id):
    profile = VoiceProfile.objects.filter(user_id=user_id, user__is_active=True, model_version=MODEL_SHA).first()
    if profile is None:
        raise VoiceError('speaker_enrollment_required', 'Avval «Ovozimni tanitish» sahifasida ovoz namunangizni yozing.', 428)
    return profile


def ready(user_id):
    return VoiceProfile.objects.filter(user_id=user_id, model_version=MODEL_SHA).exists()


def check_revision(user_id, revision):
    profile = require_profile(user_id)
    if str(profile.revision) != str(revision):
        raise VoiceError('speaker_profile_changed', 'Ovoz namunasi o‘zgardi. Yangi yozuv yuboring.', 428)
    return profile


def cipher():
    key = hashlib.sha256((settings.SECRET_KEY + ':speaker-profile-v1').encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(user_id, vectors):
    return cipher().encrypt(json.dumps({'user': user_id, 'model': MODEL_SHA, 'vectors': vectors}).encode()).decode()


def unseal(profile):
    try:
        data = json.loads(cipher().decrypt(profile.encrypted_embedding.encode()))
        vectors = np.asarray(data['vectors'], dtype=np.float32)
        if data['user'] != profile.user_id or data['model'] != MODEL_SHA or vectors.shape != (3, 192) or not np.isfinite(vectors).all():
            raise ValueError
        return np.stack([unit(v) for v in vectors])
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise VoiceError('speaker_profile_invalid', 'Ovoz namunasi eskirgan yoki o‘qilmadi. Ovozingizni qayta taniting.', 428)


def read_wav(upload, max_seconds=MAX_SECONDS):
    if not upload or upload.size > max_seconds * RATE * 2 + 4096:
        raise VoiceError('speaker_audio_invalid', 'Ovoz yozuvi hajmi yoki davomiyligi noto‘g‘ri.', 413)
    try:
        with wave.open(io.BytesIO(upload.read()), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, RATE, 'NONE'):
                raise ValueError
            count = audio.getnframes()
            if not 1 <= count <= max_seconds * RATE:
                raise ValueError
            pcm = audio.readframes(count)
            if len(pcm) != count * 2:
                raise ValueError
            return pcm
    except (wave.Error, EOFError, ValueError):
        raise VoiceError('speaker_audio_invalid', '16 kHz mono WAV yozuvi kerak. Sahifani yangilab qayta yozing.', 400)


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if not np.isfinite(vector).all() or norm < 1e-6:
        raise VoiceError('speaker_audio_unclear', 'Ovoz aniq eshitilmadi. Mikrofonga yaqinroq gapiring.', 422)
    return vector / norm


class Engine:
    def __init__(self):
        import sherpa_onnx
        path = settings.VOICE_SPEAKER_MODEL
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA:
            raise VoiceError('speaker_unavailable', 'Ovozni tanish modeli serverda tayyor emas. Administratorga murojaat qiling.', 503)
        self.lock = threading.Lock()
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(path), provider='cpu', num_threads=2))
        self.vad_config = sherpa_onnx.VadModelConfig()
        self.vad_config.silero_vad.model = str(settings.BASE_DIR / 'static/vendor/voice-filter-v1/silero_vad_v5.onnx')
        self.vad_config.silero_vad.threshold = 0.5
        self.vad_config.silero_vad.min_speech_duration = 0.20
        self.vad_config.silero_vad.min_silence_duration = 0.25
        self.vad_config.sample_rate = RATE
        self.vad_config.num_threads = 1

    def analyze(self, pcm, minimum=1.2):
        import sherpa_onnx
        if len(pcm) % 2 or not 0 < len(pcm) <= RATE * 2 * MAX_SECONDS:
            raise VoiceError('speaker_audio_invalid', 'Audio davomiyligi noto‘g‘ri.', 422)
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        if np.mean(np.abs(samples) >= .995) > .03:
            raise VoiceError('speaker_audio_unclear', 'Ovoz juda baland va buzilgan. Mikrofonni biroz uzoqlashtiring.', 422)
        with self.lock:
            vad = sherpa_onnx.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=MAX_SECONDS + 1)
            for start in range(0, len(samples), 512):
                chunk = samples[start:start+512]
                vad.accept_waveform(np.pad(chunk, (0, 512-len(chunk))))
            vad.flush()
            speech = []
            while not vad.empty():
                speech.extend(vad.front.samples)
                vad.pop()
            speech = np.asarray(speech, dtype=np.float32)
            if len(speech) < minimum * RATE or np.sqrt(np.mean(speech ** 2)) < .003:
                raise VoiceError('speaker_audio_short', f'Kamida {minimum:g} soniya aniq gapiring. Qisqa yoki past ovoz tanilmadi.', 422)
            def embedding(audio):
                stream = self.extractor.create_stream()
                stream.accept_waveform(sample_rate=RATE, waveform=audio)
                stream.input_finished()
                if not self.extractor.is_ready(stream):
                    raise VoiceError('speaker_audio_unclear', 'Ovoz yetarlicha aniq emas. Qayta gapiring.', 422)
                return unit(self.extractor.compute(stream))
            whole = embedding(speech)
            # Check overlapping windows too, so a matching start cannot authorize
            # an unrelated colleague's later speech in the same utterance.
            width, step = int(2.5 * RATE), int(1.25 * RATE)
            starts = list(range(0, max(1, len(speech)-width+1), step))
            if len(speech) > width and starts[-1] != len(speech)-width:
                starts.append(len(speech)-width)
            windows = [embedding(speech[s:s+width]) for s in starts] if len(speech) > width else [whole]
            return whole, windows


@lru_cache(maxsize=1)
def engine():
    try:
        return Engine()
    except VoiceError:
        raise
    except (ImportError, RuntimeError, OSError):
        raise VoiceError('speaker_unavailable', 'Ovozni tanish xizmati tayyor emas. Administratorga murojaat qiling.', 503)


def enroll_vectors(recordings):
    analyses = [engine().analyze(pcm, minimum=3) for pcm in recordings]
    vectors = [a[0] for a in analyses]
    if any(float(a @ b) < .60 for i, a in enumerate(vectors) for b in vectors[i+1:]):
        raise VoiceError('speaker_samples_differ', 'Namunalardagi ovozlar mos kelmadi. Tinch joyda uchalasini faqat o‘zingiz qayta yozing.', 422)
    center = unit(np.mean(vectors, axis=0))
    if any(float(window @ center) < .55 for _, windows in analyses for window in windows):
        raise VoiceError('speaker_samples_mixed', 'Yozuvda boshqa yoki noaniq ovoz bor. Namunalaringizni tinch joyda qayta yozing.', 422)
    return [v.tolist() for v in vectors]


def verify(user_id, pcm, revision=None):
    profile = require_profile(user_id)
    if revision is not None and str(profile.revision) != str(revision):
        raise VoiceError('speaker_profile_changed', 'Ovoz namunasi o‘zgardi. Jonli suhbatni qayta yoqing.', 428)
    center = unit(unseal(profile).mean(axis=0))
    short = False
    try:
        whole, windows = engine().analyze(pcm)
    except VoiceError as error:
        if error.code != 'speaker_audio_short':
            raise
        # Short replies still need their OWN embedding. Never borrow identity
        # from a preceding utterance or pad with the enrolled user's recording.
        whole, windows = engine().analyze(pcm, minimum=.6)
        short = True
    threshold = max(settings.VOICE_SPEAKER_THRESHOLD, .80) if short else settings.VOICE_SPEAKER_THRESHOLD
    window_threshold = max(settings.VOICE_SPEAKER_WINDOW_THRESHOLD, .80) if short else settings.VOICE_SPEAKER_WINDOW_THRESHOLD
    if float(whole @ center) < threshold or any(
            float(window @ center) < window_threshold for window in windows):
        raise VoiceError('speaker_mismatch', 'Bu ovoz sizning namunangizga mos kelmadi. Buyruq yuborilmadi.', 422)
    # Enrollment/reset during a recording invalidates that recording as well.
    if not VoiceProfile.objects.filter(pk=profile.pk, revision=profile.revision, user__is_active=True).exists():
        raise VoiceError('speaker_profile_changed', 'Ovoz namunasi o‘zgardi. Suhbatni qayta yoqing.', 428)
    return profile
