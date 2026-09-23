"""The two judgments, and what happens when the service says nothing useful.

No test here reaches TypeSafe: every call is answered from a stub, so the suite
stays offline and the fallbacks are exercised as often as the happy path.
"""
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

from . import agent_tools as tools
from . import live, typesafe
from .models import AgentConversation, Department, User

SETTINGS = dict(TYPESAFE_API_KEY='apikey_test', TYPESAFE_MODEL='jev-latest',
                TYPESAFE_COMMAND_MIN=0.4, TYPESAFE_PERSON_MIN=0.85,
                PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


def noul(value):
    return {'addressed': {'type': 'noul', 'noul': value}}


def choice(pick, probability):
    return {'person': {'type': 'choice', 'choice': pick, 'confidence': probability,
                       'probabilities': {pick: probability}}}


@override_settings(**SETTINGS)
class CommandGateTests(TestCase):
    def gate(self, text, answer):
        async def stub(state, questions):
            self.assertEqual(state['utterance'], text)
            return answer
        with patch('core.typesafe.judge_async', stub):
            return async_to_sync(typesafe.addressed_to_us)(text)

    def test_speech_in_the_room_is_left_alone_and_a_command_is_heard(self):
        self.assertTrue(self.gate('Kechikkan topshiriqlarni ko‘rsat', noul(0.84)))
        self.assertFalse(self.gate('Tushdan keyin choyxonaga boramizmi', noul(0.19)))
        # Right at the line the utterance is kept: being deaf is the worse failure.
        self.assertTrue(self.gate('Shuni yopamizmi', noul(0.4)))

    def test_anything_but_an_answer_keeps_the_microphone_working(self):
        for answer in [None, {}, {'addressed': {}}, {'addressed': {'noul': 'ha'}}]:
            with self.subTest(answer=answer):
                self.assertTrue(self.gate('Kechikkanlarni och', answer))

    def test_without_a_key_nothing_is_asked(self):
        with override_settings(TYPESAFE_API_KEY=''), patch('core.typesafe.judge_async') as call:
            self.assertTrue(async_to_sync(typesafe.addressed_to_us)('Har qanday gap'))
        call.assert_not_called()


@override_settings(**SETTINGS)
class PersonChoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build = Department.objects.create(name='Qurilish')
        money = Department.objects.create(name='Moliya')
        cls.head = User.objects.create_user('boshliq', full_name='Tuyliyev Asliddin', role='head', department=build)
        cls.first = User.objects.create_user('sherzod1', full_name='Rustamov Sherzod',
                                             job_title='Muhandis', department=build)
        cls.second = User.objects.create_user('sherzod2', full_name='Qodirov Sherzod',
                                              job_title='Iqtisodchi', department=money)

    def pick(self, answer, candidates=None, command='Sherzodga hisobot tayyorlashni topshir'):
        with patch('core.typesafe.judge', return_value=answer) as call:
            chosen = typesafe.choose_person(command, self.head, candidates or [self.first, self.second])
        return chosen, call

    def test_a_confident_answer_settles_which_namesake_was_meant(self):
        chosen, call = self.pick(choice(str(self.first.pk), 0.96))
        self.assertEqual(chosen, self.first.pk)
        state = call.call_args.args[0]
        # The judgment sees who is speaking and every candidate it may pick.
        self.assertEqual(state['speaker']['department'], 'Qurilish')
        self.assertEqual({c['id'] for c in state['candidates']}, {str(self.first.pk), str(self.second.pk)})
        options = call.call_args.args[1]['person']['criteria']
        self.assertIn('none', options)

    def test_an_unsure_or_missing_answer_leaves_the_question_to_the_person(self):
        for answer in [choice('none', 0.95), choice(str(self.first.pk), 0.6), None, {}, {'person': {}},
                       {'person': {'choice': '999999', 'probabilities': {'999999': 0.99}}}]:
            with self.subTest(answer=answer):
                self.assertIsNone(self.pick(answer)[0])

    def test_nothing_is_asked_when_there_is_nothing_to_settle(self):
        for command, candidates in [('Sherzodga ayt', [self.first]), ('', [self.first, self.second])]:
            with self.subTest(command=command, candidates=len(candidates)):
                with patch('core.typesafe.judge') as call:
                    self.assertIsNone(typesafe.choose_person(command, self.head, candidates))
                call.assert_not_called()


@override_settings(**SETTINGS)
class WiringTests(TestCase):
    """The judgments in the places that actually call them."""

    @classmethod
    def setUpTestData(cls):
        build = Department.objects.create(name='Qurilish')
        cls.head = User.objects.create_user('boshliq', full_name='Tuyliyev Asliddin', role='head', department=build)
        cls.first = User.objects.create_user('sherzod1', full_name='Rustamov Sherzod',
                                             job_title='Muhandis', department=build)
        cls.second = User.objects.create_user('sherzod2', full_name='Qodirov Sherzod',
                                              job_title='Muhandis', department=build)

    def recognise(self, transcript, addressed):
        async def stt(pcm):
            return {'event': 'final', 'text': transcript}

        async def gate(text):
            self.assertEqual(text, transcript)
            return addressed

        async def sent(message):
            pass

        async def call():
            return await live.verified_recognition({'user_id': self.head.pk}, b'\0\0' * 1600, sent)

        with patch('core.live.transcribe_utterance', stt), patch('core.typesafe.addressed_to_us', gate):
            return async_to_sync(call)()

    def test_room_speech_never_reaches_the_assistant(self):
        ignored = self.recognise('Mashina keldimi', addressed=False)
        self.assertEqual(ignored['event'], 'ignored')
        self.assertNotIn('text', ignored)  # Nothing to submit, so nothing is shown as heard.
        heard = self.recognise('Kechikkanlarni och', addressed=True)
        self.assertEqual((heard['event'], heard['text']), ('final', 'Kechikkanlarni och'))

    def conversation(self, command):
        return AgentConversation.objects.create(user=self.head,
                                                messages=[{'role': 'user', 'content': command}])

    def test_a_settled_namesake_is_returned_alone_without_a_question(self):
        talk = self.conversation('Sherzod Rustamovga hisobot tayyorlashni topshir')
        with patch('core.typesafe.choose_person', return_value=self.first.pk) as judge:
            answer = tools.execute(self.head, talk, 'list_people', {'query': 'Sherzod'}, set())
        self.assertEqual([p['id'] for p in answer['people']], [self.first.pk])
        self.assertFalse(answer['needs_clarification'])
        self.assertEqual(answer['resolved'], 'judgment')
        # The judgment is given the whole request, not just the name fragment.
        self.assertEqual(judge.call_args.args[0], 'Sherzod Rustamovga hisobot tayyorlashni topshir')

    def test_an_unsettled_namesake_still_reaches_the_person_as_a_question(self):
        talk = self.conversation('Sherzodga ayt')
        with patch('core.typesafe.choose_person', return_value=None):
            answer = tools.execute(self.head, talk, 'list_people', {'query': 'Sherzod'}, set())
        self.assertEqual(len(answer['people']), 2)
        self.assertTrue(answer['needs_clarification'])
        self.assertNotIn('resolved', answer)
