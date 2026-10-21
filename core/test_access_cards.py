import json
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from . import access_cards
from .attendance import ingest_event
from .models import AccessCard, User


def person(employee_no, name, *cards):
    return {'employee_no': employee_no, 'name': name, 'cards': list(cards)}


class NameScoreTests(TestCase):
    def test_device_spelling_variants_score_high(self):
        for device, ours in [('ILYOS AXMEDOV', 'Ahmedov Ilyos Izzatullo o‘g‘li'),
                             ('SARDOR BOYQOBILOV', 'Bayqobilov Sardor Ilhomovich'),
                             ('AZIZ KARIMOV', 'Karimov Azizbek Ozodovich'),
                             ('ASLIDDIN TOSHMATOV', 'Toshmatov Asliddin Keldiyor o‘g‘li'),
                             ('NEMATJON SINOVOV', 'Sinovov Ne’matjon Ixalovich')]:
            self.assertGreaterEqual(access_cards.name_score(device, ours), access_cards.SURE, device)

    def test_different_people_score_low(self):
        self.assertLess(access_cards.name_score('NODIRA SINOVOVA', 'Karimov Azizbek Ozodovich'), access_cards.LIKELY)
        self.assertLess(access_cards.name_score('123', 'Karimov Azizbek Ozodovich'), access_cards.LIKELY)


class MatchAndApplyTests(TestCase):
    def setUp(self):
        self.ahmedov = User.objects.create_user('ahmedov', full_name='Ahmedov Ilyos Izzatullo o‘g‘li')
        self.karimov = User.objects.create_user('karimov', full_name='Karimov Azizbek Ozodovich')
        self.surname = User.objects.create_user('sinovov', full_name='Sinovov')
        self.users = list(User.objects.all())

    def rows(self, people, **kwargs):
        return access_cards.match(people, self.users, **kwargs)

    def test_sure_matches_are_written_with_card_and_employee_no(self):
        rows = access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000001'),
                                             person('a1b2', 'AZIZ KARIMOV', '1000000002')]))
        self.assertEqual([r.state for r in rows], ['sure', 'sure'])
        card = AccessCard.objects.get(device_employee_no='7')
        self.assertEqual((card.user, card.card_no, card.label), (self.ahmedov, '1000000001', 'ILYOS AXMEDOV'))
        self.assertEqual(AccessCard.objects.get(card_no='1000000002').user, self.karimov)

    def test_likely_and_unmatched_are_not_written_by_default(self):
        rows = access_cards.apply(self.rows([person('17', 'OBID SINOVOV', '1000000003'),
                                             person('99', 'NODIRA BEGIMOVA', '1000000004')]))
        self.assertEqual([r.state for r in rows], ['likely', 'unmatched'])
        self.assertFalse(AccessCard.objects.exists())
        access_cards.apply(self.rows([person('17', 'OBID SINOVOV', '1000000003')]), include_likely=True)
        self.assertEqual(AccessCard.objects.get().user, self.surname)

    def test_one_user_takes_only_one_device_person(self):
        rows = self.rows([person('1', 'ILYOS AXMEDOV', '1000000001'), person('2', 'ILYOS AHMEDOV', '1000000005')])
        self.assertEqual(sum(1 for r in rows if r.user == self.ahmedov), 1)

    def test_manual_and_skip(self):
        rows = access_cards.apply(self.rows([person('99', 'NODIRA BEGIMOVA', '1000000004'),
                                             person('7', 'ILYOS AXMEDOV', '1000000001')],
                                            manual={'99': self.karimov}, skip={'7'}))
        self.assertEqual({r.employee_no: r.state for r in rows}, {'99': 'manual', '7': 'skipped'})
        self.assertEqual(AccessCard.objects.get().user, self.karimov)

    def test_rerun_is_unchanged_and_card_change_updates_in_place(self):
        access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000001')]))
        rows = access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000001')]))
        self.assertEqual(rows[0].outcome, 'unchanged')
        rows = access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000009')]))
        self.assertEqual(rows[0].outcome, 'updated')
        self.assertEqual(AccessCard.objects.get().card_no, '1000000009')

    def test_hand_made_card_row_gets_the_employee_no(self):
        AccessCard.objects.create(user=self.ahmedov, card_no='1000000001')
        rows = access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000001')]))
        self.assertEqual(rows[0].outcome, 'updated')
        self.assertEqual(AccessCard.objects.get().device_employee_no, '7')

    def test_card_held_by_someone_else_is_a_conflict(self):
        AccessCard.objects.create(user=self.karimov, card_no='1000000001')
        rows = access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000001')]))
        self.assertEqual(rows[0].outcome, 'conflict')
        self.assertEqual(AccessCard.objects.get().user, self.karimov)

    @override_settings(ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_DOOR_MAJOR=5)
    def test_pin_opening_matches_by_employee_no(self):
        access_cards.apply(self.rows([person('7', 'ILYOS AXMEDOV', '1000000001')]))
        result = ingest_event({'major': 5, 'minor': 1, 'time': '2026-10-21 08:10:00', 'cardNo': '',
                               'employeeNoString': '7', 'name': 'ILYOS AXMEDOV', 'doorNo': 1})
        self.assertTrue(result['matched'])


class CommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ahmedov', full_name='Ahmedov Ilyos Izzatullo o‘g‘li')
        self.file = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump({'people': [person('7', 'ILYOS AXMEDOV', '1000000001')]}, self.file)
        self.file.close()

    def run_command(self, *args):
        out = StringIO()
        call_command('sync_access_cards', '--file', self.file.name, *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self.run_command()
        self.assertIn('Hech narsa yozilmadi', output)
        self.assertFalse(AccessCard.objects.exists())

    def test_apply_writes(self):
        self.assertIn('Bog‘lash yozildi', self.run_command('--apply'))
        self.assertEqual(AccessCard.objects.get().user, self.user)

    def test_seed_binds_known_staff_and_is_rerunnable(self):
        from .management.commands.seed_access_cards import CARDS
        username, employee_no, card_no, _name = CARDS[0]
        user = User.objects.create_user(username, full_name='Sinov Seed')
        call_command('seed_access_cards', '--dry-run', stdout=StringIO())
        self.assertFalse(AccessCard.objects.exists())
        out = StringIO()
        call_command('seed_access_cards', stdout=out)
        card = AccessCard.objects.get(user=user)
        self.assertEqual((card.user, card.device_employee_no, card.card_no), (user, employee_no, card_no))
        self.assertIn('o‘tkazildi', out.getvalue())          # the rest have no account here
        out = StringIO()
        call_command('seed_access_cards', stdout=out)
        self.assertIn('yangilandi: 0, o‘zgarmadi: 2', out.getvalue())   # this user and the fixture's ahmedov

    def test_seed_does_not_move_a_card_bound_to_someone_else(self):
        from .management.commands.seed_access_cards import CARDS
        username, _employee_no, card_no, _name = CARDS[0]
        User.objects.create_user(username, full_name='Sinov Seed')
        AccessCard.objects.create(user=self.user, card_no=card_no)
        call_command('seed_access_cards', stdout=StringIO())
        self.assertEqual(AccessCard.objects.get(card_no=card_no).user, self.user)

    def test_map_takes_our_user_id_and_a_person_without_a_card(self):
        other = User.objects.create_user('begimova', full_name='Begimova Nodira')
        with open(self.file.name, 'w', encoding='utf-8') as handle:
            json.dump({'people': [person('20', 'NODIRA SINOVOVA')]}, handle)
        self.run_command('--map', f'20={other.pk}', '--apply')
        card = AccessCard.objects.get()
        self.assertEqual((card.user, card.device_employee_no, card.card_no), (other, '20', ''))
