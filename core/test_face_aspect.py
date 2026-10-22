from unittest.mock import patch

import cv2
import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import face
from .attendance import ingest_event
from .models import AccessCard, FaceEnrollment, User


def jpeg(width, height):
    return cv2.imencode('.jpg', np.full((height, width, 3), 128, np.uint8))[1].tobytes()


def size(data):
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return image.shape[1], image.shape[0]


@override_settings(ATTENDANCE_FRAME_ASPECT='16:9')
class RestoreAspectTests(TestCase):
    def test_squeezed_station_frame_is_widened_to_16_9(self):
        self.assertEqual(size(face.restore_aspect(jpeg(704, 576))), (1024, 576))

    def test_frame_already_16_9_is_untouched(self):
        data = jpeg(1920, 1080)
        self.assertIs(face.restore_aspect(data), data)

    @override_settings(ATTENDANCE_FRAME_ASPECT='')
    def test_disabled_setting_leaves_the_frame(self):
        data = jpeg(704, 576)
        self.assertIs(face.restore_aspect(data), data)

    def test_bytes_that_are_not_an_image_pass_through(self):
        self.assertEqual(face.restore_aspect(b'not-a-jpeg'), b'not-a-jpeg')


@override_settings(ATTENDANCE_FRAME_ASPECT='16:9', ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_DOOR_MAJOR=5)
class IngestUsesRestoredFrameTests(TestCase):
    def setUp(self):
        employee = User.objects.create_user('sinov', full_name='Sinov Xodim')
        AccessCard.objects.create(user=employee, card_no='1000000001')
        FaceEnrollment.objects.create(user=employee, embedding=[0.1] * 512,
                                      photo=SimpleUploadedFile('enrolled.jpg', b'enrolled'))

    def test_the_face_check_sees_the_true_shape(self):
        seen = []
        with patch('core.face.available', return_value=True), \
                patch('core.face.compare', side_effect=lambda emb, data: seen.append(size(data)) or 0.9):
            ingest_event({'major': 5, 'minor': 1, 'time': '2026-10-21 08:00:00', 'cardNo': '1000000001',
                          'employeeNoString': '', 'name': 'SINOV XODIM', 'doorNo': 1},
                         SimpleUploadedFile('capture.jpg', jpeg(704, 576), content_type='image/jpeg'))
        self.assertEqual(seen, [(1024, 576)])
