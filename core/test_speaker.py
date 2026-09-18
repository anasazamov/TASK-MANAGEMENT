import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from .models import User, VoiceProfile
from . import live

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
 STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class VoiceWithoutEnrollmentTests(TestCase):
 def setUp(self):
  cache.clear()
  self.user=User.objects.create_user('voice-user',password='test-pass')
  self.client.force_login(self.user)
 def test_login_goes_directly_to_requested_page(self):
  self.client.logout()
  result=self.client.post('/login/?next=/tasks/',{'username':self.user.username,'password':'test-pass'})
  self.assertEqual(result.url,'/tasks/')
  self.assertNotContains(self.client.get('/tasks/'),'voice-profile-link')
  for path in ['/account/voice/','/account/voice/enroll/','/account/voice/remove/']:
   self.assertEqual(self.client.get(path).status_code,404)
 def test_upload_without_profile_reaches_stt(self):
  with patch('core.voicelab.transcribe',return_value={'status':'completed','text':'Tasdiqlayman'}) as stt:
   result=self.client.post('/voice/transcribe/',{'audio':SimpleUploadedFile('audio.wav',b'audio'), 'request_id':str(uuid.uuid4())})
  self.assertEqual(result.status_code,200)
  stt.assert_called_once()
  self.assertFalse(VoiceProfile.objects.exists())
 def test_live_ticket_needs_session_not_voice_profile_and_cannot_be_reused(self):
  result=self.client.post('/voice/realtime-session/')
  self.assertEqual(result.status_code,200)
  scope={'headers':[(b'origin',b'http://testserver'),(b'cookie',f'sessionid={self.client.cookies["sessionid"].value}'.encode())]}
  self.assertEqual(live.authenticate(scope,result.json()['ticket']),{'user_id':self.user.pk})
  self.assertFalse(live.authenticate(scope,result.json()['ticket']))
  self.client.logout()
  self.assertEqual(self.client.post('/voice/realtime-session/').status_code,401)
