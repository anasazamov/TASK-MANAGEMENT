"""Read the HR staff list (a Word table: No, name + birth, photo, position, phone).

Only what attendance needs is taken: the name, the photo, the position and the
phone. The birth year and place that share the name cell are dropped here and
never stored.
"""
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

CYRILLIC = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'ғ': 'g‘', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j', 'з': 'z',
    'и': 'i', 'й': 'y', 'к': 'k', 'қ': 'q', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ў': 'o‘', 'ф': 'f', 'х': 'x', 'ҳ': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh',
    'ъ': '’', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ы': 'i',
}
VOWELS = set('аеёиоуўэюяъь')


def latin(text):
    """Uzbek Cyrillic to Latin; е is "ye" at the start of a word and after a vowel."""
    out, previous = [], ''
    for char in text:
        lower = char.lower()
        if lower in CYRILLIC:
            value = CYRILLIC[lower]
            if lower == 'е' and (not previous.isalpha() or previous.lower() in VOWELS):
                value = 'ye'
            out.append(value.capitalize() if char.isupper() else value)
        else:
            out.append(char)
        previous = char
    return ''.join(out)


def clean_name(cell_text):
    """'КувандиковМухтор Нарзуллаевич1985 йил,…' -> 'Quvandikov Muxtor Narzullayevich'."""
    name = re.split(r'\d', cell_text, maxsplit=1)[0]
    name = re.sub(r'(?<=[а-яёўқғҳ])(?=[А-ЯЁЎҚҒҲ])', ' ', name)     # a missing space between parts
    return ' '.join(latin(name).split())


def phone_digits(text):
    digits = re.sub(r'\D', '', text)
    if len(digits) == 9:
        digits = '998' + digits
    return digits if len(digits) == 12 else ''


def to_docx(path):
    """A .doc is converted with LibreOffice when it is installed; Word saves .docx itself."""
    path = Path(path)
    if path.suffix.lower() == '.docx':
        return path, None
    office = shutil.which('soffice') or shutil.which('libreoffice')
    if not office:
        raise ValueError('.doc faylni o‘qish uchun LibreOffice kerak. Word’da «Saqlash → .docx» qilib bering.')
    folder = tempfile.mkdtemp()
    subprocess.run([office, '--headless', '--convert-to', 'docx', '--outdir', folder, str(path)],
                   check=True, capture_output=True, timeout=300)
    return Path(folder) / (path.stem + '.docx'), folder


def _text(cell):
    return ''.join(node.text or '' for node in cell.iter(W + 't')).strip()


def read(path):
    """[{name, position, phone, photo, photo_name}] for every table row that has a photo."""
    docx, temporary = to_docx(path)
    try:
        with zipfile.ZipFile(docx) as archive:
            rels = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
            targets = {rel.get('Id'): rel.get('Target') for rel in rels}
            body = ET.fromstring(archive.read('word/document.xml'))
            people = []
            for row in body.iter(W + 'tr'):
                cells = row.findall(W + 'tc')
                if len(cells) < 3:
                    continue           # a section heading spanning the row
                refs = [node.get(R + 'embed') or node.get(R + 'id') for node in cells[2].iter()
                        if node.get(R + 'embed') or node.get(R + 'id')]
                media = next((targets[ref] for ref in refs if targets.get(ref, '').startswith('media/')), None)
                name = clean_name(_text(cells[1]))
                if not media or not name:
                    continue
                people.append({
                    'name': name,
                    'position': latin(_text(cells[3])) if len(cells) > 3 else '',
                    'phone': phone_digits(_text(cells[4])) if len(cells) > 4 else '',
                    'photo': archive.read('word/' + media),
                    'photo_name': media.rsplit('/', 1)[-1],
                })
            return people
    finally:
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
