import tempfile
import zipfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from . import staff_doc
from .models import FaceEnrollment, User

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def cell(text='', image=None):
    run = f'<w:r><w:t>{text}</w:t></w:r>' if text else ''
    if image:
        run += f'<w:r><w:pict><v:imagedata xmlns:v="urn:schemas-microsoft-com:vml" r:id="{image}"/></w:pict></w:r>'
    return f'<w:tc><w:p>{run}</w:p></w:tc>'


def staff_docx(rows):
    """A minimal Word file shaped like the HR list: No, name+birth, photo, position, phone."""
    body = ''.join('<w:tr>' + ''.join(cells) + '</w:tr>' for cells in rows)
    rels = ''.join(f'<Relationship Id="rId{i}" Target="media/image{i}.jpeg" '
                   f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>'
                   for i in range(1, 4))
    path = tempfile.NamedTemporaryFile(suffix='.docx', delete=False).name
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body><w:tbl>{body}'
                                              f'</w:tbl></w:body></w:document>')
        archive.writestr('word/_rels/document.xml.rels',
                         f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         f'{rels}</Relationships>')
        for i in range(1, 4):
            archive.writestr(f'word/media/image{i}.jpeg', b'jpeg-%d' % i)
    return path


ROWS = [
    [cell('Т/р'), cell('Ф.И.Ш.'), cell('Расми'), cell('Лавозими'), cell('Телефон')],
    [cell('Бошқарув')],                                                  # a section heading
    [cell(), cell('СиноваевАнвар Эргашевич1990 йил,Самарқанд'), cell(image='rId1'),
     cell('Бош мутахассис'), cell('(90) 123-45-67')],
    [cell(), cell('Янгиев Ўткир Ғайратович 1995 йил'), cell(image='rId2'), cell('Мутахассис'), cell()],
    [cell(), cell('Расмсиз Одам'), cell(), cell(), cell()],
]


class ReadTests(TestCase):
    def test_rows_with_photos_are_read_without_birth_data(self):
        people = staff_doc.read(staff_docx(ROWS))
        self.assertEqual([p['name'] for p in people], ['Sinovayev Anvar Ergashevich', 'Yangiyev O‘tkir G‘ayratovich'])
        self.assertEqual(people[0]['phone'], '998901234567')
        self.assertEqual(people[0]['position'], 'Bosh mutaxassis')
        self.assertEqual(people[0]['photo'], b'jpeg-1')
        self.assertNotIn('1990', str(people))

    def test_transliteration(self):
        self.assertEqual(staff_doc.latin('Ерназаров Неъматжон Ҳусанович'), 'Yernazarov Ne’matjon Husanovich')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ImportCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('sinovayev', full_name='Sinovayev Anvar Ergashevich')
        self.path = staff_docx(ROWS)

    def run_command(self, *args):
        out = StringIO()
        with patch('core.face.embed', return_value=[0.1, 0.2]):
            call_command('import_staff_faces', self.path, *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        self.assertIn('Hech narsa yozilmadi', self.run_command('--create'))
        self.assertFalse(FaceEnrollment.objects.exists())
        self.assertEqual(User.objects.count(), 1)

    def test_apply_enrolls_matched_and_creates_missing_without_password(self):
        self.run_command('--create', '--apply')
        self.assertEqual(FaceEnrollment.objects.get(user=self.user).embedding, [0.1, 0.2])
        created = User.objects.get(full_name='Yangiyev O‘tkir G‘ayratovich')
        self.assertEqual((created.username, created.job_title), ('yangiyev', 'Mutaxassis'))
        self.assertFalse(created.has_usable_password())
        self.assertTrue(FaceEnrollment.objects.filter(user=created).exists())

    def test_existing_face_is_kept_unless_replace(self):
        self.run_command('--apply')
        first = FaceEnrollment.objects.get().photo.name
        self.assertIn('yuzi bor', self.run_command('--apply'))
        self.assertEqual(FaceEnrollment.objects.get().photo.name, first)
        self.run_command('--apply', '--replace')
        self.assertNotEqual(FaceEnrollment.objects.get().photo.name, first)

    def test_unmatched_row_without_create_is_left_alone(self):
        self.run_command('--apply')
        self.assertEqual(User.objects.count(), 1)
