from django.test import TestCase

from . import agent_tools
from .models import Department, User
from .person_search import find_people


class PersonSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.it = Department.objects.create(name='Axborot texnologiyalari')
        cls.chair = User.objects.create(username='chair', full_name='Rais', role='chair')
        cls.aziz = User.objects.create(username='aziz', full_name='A’zamov Aziz Akmalovich',
            department=cls.it, job_title='IT bosh mutaxassisi')
        cls.akmal = User.objects.create(username='akmal', full_name='Kamolov Akmal Anvarovich', department=cls.it)
        cls.sherzod = User.objects.create(username='sherzod', full_name='Abdullayev Sherzod Zokirovich')
        cls.sherzod2 = User.objects.create(username='sherzod2', full_name='Shonazarov Sherzod Shavkatovich')
        cls.ulugbek = User.objects.create(username='ulugbek', full_name='Usmonov Ulug‘bek Nuriddinovich')
        cls.inactive = User.objects.create(username='hidden', full_name='A’zamov Aziz', is_active=False)

    def search(self, query, user=None):
        return agent_tools.execute(user or self.chair, None, 'list_people', {'query': query}, set())

    def test_first_name_surname_case_apostrophes_and_cyrillic(self):
        for query in ['Aziz', 'Azizning', 'Azizni', 'Azizga', 'Aziz aka', 'Azamov',
                      'Azamovni', "A'zamov", 'Aʻzamov', 'Азиз', 'Аъзамовни',
                      'Aziz Azamov', 'Azamov Azizning', 'Aziz akaga']:
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual([p['id'] for p in result['people']], [self.aziz.pk])
                self.assertFalse(result['needs_clarification'])
        for query in ['Ulugbek', "Ulug'bek", 'Улуғбекнинг']:
            self.assertEqual(self.search(query)['people'][0]['id'], self.ulugbek.pk)

    def test_uzbek_spelling_variants_match_without_clarification(self):
        halimov = User.objects.create(username='halimov', full_name='Xalimov Farrux Zafarzoda')
        ahmedov = User.objects.create(username='ahmedov', full_name='Ahmedov Ilhom Ismoilovich')
        for query, person in [('Shohnazarovni', self.sherzod2), ('Шоҳназаров', self.sherzod2),
                              ('Halimovning', halimov), ('Axmedov', ahmedov), ('Amedov', ahmedov),
                              ('Ilxom Ahmedov', ahmedov)]:
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual([p['id'] for p in result['people']], [person.pk])
                self.assertFalse(result['needs_clarification'])
                self.assertEqual(result['match'], 'spelling')

    def test_spelling_variants_of_two_people_still_require_clarification(self):
        other = User.objects.create(username='shoh', full_name='Shohnazarov Bobur')
        for query in ['Shohnazarov', 'Shonazarovni', 'Shohnazaro']:
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual({p['id'] for p in result['people']}, {other.pk, self.sherzod2.pk})
                self.assertTrue(result['needs_clarification'])
        self.assertEqual([p['id'] for p in self.search('Shohnazarov Bobur')['people']], [other.pk])

    def test_first_name_does_not_match_another_person_patronymic_prefix(self):
        self.assertEqual([p['id'] for p in self.search('Akmal')['people']], [self.akmal.pk])

    def test_duplicate_first_names_require_clarification(self):
        result = self.search('Sherzodning')
        self.assertEqual({p['id'] for p in result['people']}, {self.sherzod.pk, self.sherzod2.pk})
        self.assertTrue(result['needs_clarification'])
        self.assertEqual(result['total'], 2)

    def test_partial_and_speech_spelling_errors_are_only_suggestions(self):
        for query in ['Azziz', 'Azamo', 'Azmaov', 'Azamof']:
            with self.subTest(query=query):
                result = self.search(query)
                self.assertIn(self.aziz.pk, [p['id'] for p in result['people']])
                self.assertTrue(result['needs_clarification'])
                self.assertEqual(result['match'], 'suggested')

    def test_mismatched_full_names_and_unknown_names_do_not_choose_someone(self):
        for query in ['Aziz Kamolov', 'Aziz Aziz', 'Qobiljon', 'Az', '!!!']:
            self.assertEqual(self.search(query)['people'], [])

    def test_role_scope_is_applied_before_all_normalized_and_fuzzy_matching(self):
        for query in ['Azamov', 'Azziz', 'Aziz', 'IT']:
            self.assertEqual(self.search(query, self.sherzod)['people'], [])
        self.akmal.role = 'head'
        self.akmal.save(update_fields=['role'])
        self.assertEqual(self.search('Azamov', self.akmal)['people'][0]['id'], self.aziz.pk)
        self.assertEqual(self.search('Sherzod', self.akmal)['people'], [])

    def test_job_and_department_search_and_truncation_are_explicit(self):
        self.assertEqual(self.search('IT')['people'][0]['id'], self.aziz.pk)
        self.assertEqual(self.search('Axborot')['total'], 2)
        rows, meta = find_people(User.objects.filter(is_active=True), '', limit=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(meta['truncated'])
        self.assertEqual(meta['total'], 6)
