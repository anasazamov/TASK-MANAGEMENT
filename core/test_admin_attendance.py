import tempfile
from unittest.mock import patch

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .attendance import ingest_event
from .models import (AccessCard, AccessEvent, Attendance, AttendanceAlert, AttendanceExcuse, FaceEnrollment,
                     FaceReview, User)

# A 1x1 JPEG: enough for the image field; the face check itself is mocked.
JPEG = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912'
                     '130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b0800'
                     '01000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002'
                     '010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f024'
                     '33627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a63646566676869'
                     '6a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4'
                     'c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3'
                     'ffd9')


@override_settings(ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_DOOR_MAJOR=5, MEDIA_ROOT=tempfile.mkdtemp())
class AttendanceAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('root', password='x', full_name='Admin Sinov')
        self.client.force_login(self.admin)
        self.employee = User.objects.create_user('sinov', full_name='Sinov Xodim Karimovich')
        AccessCard.objects.create(user=self.employee, card_no='1000000001', device_employee_no='7')
        ingest_event({'major': 5, 'minor': 1, 'time': '2026-10-21 08:10:00', 'cardNo': '1000000001',
                      'employeeNoString': '7', 'name': 'SINOV XODIM', 'doorNo': 1})
        self.event = AccessEvent.objects.get()
        self.day = Attendance.objects.get()

    def pending_review(self):
        self.event.photo.save('x.jpg', ContentFile(JPEG))
        self.day.status = Attendance.Status.REVIEW
        self.day.save()
        return FaceReview.objects.create(event=self.event, user=self.employee, attendance=self.day, confidence=0.2)

    def test_every_attendance_page_opens(self):
        self.pending_review()
        AttendanceExcuse.objects.create(user=self.employee, date=self.day.date, kind='sick')
        AttendanceAlert.objects.create(subject=self.employee, date=self.day.date, kind='late')
        FaceEnrollment.objects.create(user=self.employee, photo=ContentFile(JPEG, 'f.jpg'), embedding=[0.1])
        for name in ['accesscard', 'accessevent', 'attendance', 'facereview', 'attendanceexcuse',
                     'faceenrollment', 'attendancealert']:
            model = {'accesscard': AccessCard, 'accessevent': AccessEvent, 'attendance': Attendance,
                     'facereview': FaceReview, 'attendanceexcuse': AttendanceExcuse,
                     'faceenrollment': FaceEnrollment, 'attendancealert': AttendanceAlert}[name]
            self.assertEqual(self.client.get(reverse(f'admin:core_{name}_changelist')).status_code, 200, name)
            obj = model.objects.first()
            self.assertEqual(self.client.get(reverse(f'admin:core_{name}_change', args=[obj.pk])).status_code,
                             200, name)
        self.assertEqual(self.client.get(reverse('admin:core_user_change', args=[self.employee.pk])).status_code, 200)

    def test_event_log_can_be_deleted(self):
        url = reverse('admin:core_accessevent_delete', args=[self.event.pk])
        self.client.post(url, {'post': 'yes'})
        self.assertFalse(AccessEvent.objects.filter(pk=self.event.pk).exists())

    def test_review_photo_only_for_admins(self):
        review = self.pending_review()
        url = reverse('admin:core_facereview_photo', args=[review.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.client.force_login(self.employee)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_approve_action_settles_the_day(self):
        review = self.pending_review()
        self.client.post(reverse('admin:core_facereview_changelist'),
                         {'action': 'approve', ACTION_CHECKBOX_NAME: [review.pk]})
        review.refresh_from_db()
        self.day.refresh_from_db()
        self.assertEqual((review.state, review.resolved_by), (FaceReview.State.APPROVED, self.admin))
        self.assertEqual(self.day.status, Attendance.Status.PRESENT)

    def test_reject_action_asks_for_a_reason_then_rejects(self):
        review = self.pending_review()
        url = reverse('admin:core_facereview_changelist')
        response = self.client.post(url, {'action': 'reject', ACTION_CHECKBOX_NAME: [review.pk]})
        self.assertContains(response, 'Rad etish sababi')
        review.refresh_from_db()
        self.assertEqual(review.state, FaceReview.State.PENDING)
        self.client.post(url, {'action': 'reject', ACTION_CHECKBOX_NAME: [review.pk], 'reason': 'Boshqa odam'})
        review.refresh_from_db()
        self.day.refresh_from_db()
        self.assertEqual((review.state, review.reason), (FaceReview.State.REJECTED, 'Boshqa odam'))
        self.assertEqual(self.day.status, Attendance.Status.REJECTED)

    def test_resettle_action_rederives_the_day(self):
        Attendance.objects.filter(pk=self.day.pk).update(status=Attendance.Status.REJECTED)
        self.client.post(reverse('admin:core_attendance_changelist'),
                         {'action': 'resettle', ACTION_CHECKBOX_NAME: [self.day.pk]})
        self.day.refresh_from_db()
        self.assertEqual(self.day.status, Attendance.Status.PRESENT)

    def test_card_can_be_added_from_admin(self):
        other = User.objects.create_user('ikkinchi', full_name='Ikkinchi Xodim')
        self.client.post(reverse('admin:core_accesscard_add'),
                         {'user': other.pk, 'card_no': '1000000002', 'device_employee_no': '8', 'label': '',
                          'active': 'on'})
        self.assertEqual(AccessCard.objects.get(card_no='1000000002').user, other)

    def test_excuse_records_who_entered_it_and_checks_the_note(self):
        url = reverse('admin:core_attendanceexcuse_add')
        data = {'user': self.employee.pk, 'date': '2026-10-22', 'kind': 'other', 'note': ''}
        self.assertContains(self.client.post(url, data), 'izoh yozing')
        self.client.post(url, {**data, 'note': 'Oilaviy'})
        self.assertEqual(AttendanceExcuse.objects.get().created_by, self.admin)

    def test_enrollment_from_admin_embeds_the_face(self):
        with patch('core.face.embed', return_value=[0.5, 0.5]):
            self.client.post(reverse('admin:core_faceenrollment_add'),
                             {'user': self.employee.pk, 'image': SimpleUploadedFile('f.jpg', JPEG, 'image/jpeg')})
        self.assertEqual(FaceEnrollment.objects.get().embedding, [0.5, 0.5])

    def test_enrollment_without_a_face_is_refused(self):
        with patch('core.face.embed', return_value=None):
            response = self.client.post(reverse('admin:core_faceenrollment_add'),
                                        {'user': self.employee.pk,
                                         'image': SimpleUploadedFile('f.jpg', JPEG, 'image/jpeg')})
        self.assertContains(response, 'yuz aniqlanmadi')
        self.assertFalse(FaceEnrollment.objects.exists())

    def test_deleting_an_alert_marker_is_allowed(self):
        alert = AttendanceAlert.objects.create(subject=self.employee, date=timezone.localdate(), kind='late')
        self.client.post(reverse('admin:core_attendancealert_delete', args=[alert.pk]), {'post': 'yes'})
        self.assertFalse(AttendanceAlert.objects.filter(pk=alert.pk).exists())
