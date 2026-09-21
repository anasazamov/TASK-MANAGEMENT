"""Agent-generated pages: the model writes the layout, the server writes the data.

Stored HTML is sanitized on the way in (no scripts, no network, no forms) and
served from a sandboxed response, so a page cannot act for the signed-in user.
Numbers come from re-running the page's stored workflow under the viewer's own
permissions, never from text the model wrote.
"""
import json
import re
from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import escape

from . import dynamic_tools
from .models import GeneratedPage
from .services import require

MAX_PAGES = 20
MAX_HTML = 20000
MAX_ROWS = 200
MAX_COLUMNS = 12
TAGS = {'div', 'section', 'article', 'header', 'footer', 'aside', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'p', 'span', 'strong', 'b', 'em', 'i', 'small', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'table',
        'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'figure', 'figcaption', 'blockquote',
        'code', 'pre', 'hr', 'br', 'style'}
VOID = {'hr', 'br'}
# Everything these contain is dropped; other unknown tags keep their text.
DROP_TREE = {'script', 'iframe', 'object', 'embed', 'noscript', 'svg', 'math', 'form', 'template',
             'canvas', 'video', 'audio', 'frame', 'frameset', 'applet', 'link', 'meta', 'base'}
ATTRIBUTES = {'class', 'style', 'colspan', 'rowspan', 'scope', 'title'}
PLACEHOLDERS = ('{{table}}', '{{chart}}', '{{total}}', '{{title}}', '{{generated_at}}')
DATA_PLACEHOLDERS = ('{{table}}', '{{chart}}', '{{total}}')
UNSAFE_CSS = re.compile(r'(?i)@import[^;]*;?|url\s*\([^)]*\)?|expression\s*\([^)]*\)?|javascript:|</|'
                        r'behavior\s*:[^;]*;?|-moz-binding[^;]*;?')


class Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.open = []
        self.dropping = []

    def attributes(self, attrs):
        result = ''
        for name, value in attrs:
            if name not in ATTRIBUTES or value is None:
                continue
            if name in ('colspan', 'rowspan') and not (value.isdigit() and 1 <= int(value) <= 50):
                continue
            if name == 'style' and UNSAFE_CSS.search(value):
                continue
            result += f' {name}="{escape(value)}"'
        return result

    def handle_starttag(self, tag, attrs):
        if self.dropping:
            self.dropping.append(tag)
            return
        if tag in DROP_TREE:
            self.dropping.append(tag)
            return
        if tag not in TAGS:
            return
        self.parts.append(f'<{tag}{self.attributes(attrs)}>')
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if not self.dropping and tag in TAGS:
            self.parts.append(f'<{tag}{self.attributes(attrs)}>' if tag in VOID else f'<{tag}{self.attributes(attrs)}></{tag}>')

    def handle_endtag(self, tag):
        if self.dropping:
            if tag in self.dropping:
                del self.dropping[self.dropping.index(tag):]
            return
        if tag in self.open:
            index = self.open.index(tag)
            for item in reversed(self.open[index:]):
                self.parts.append(f'</{item}>')
            del self.open[index:]

    def handle_data(self, data):
        if self.dropping:
            return
        if self.open and self.open[-1] == 'style':
            self.parts.append(UNSAFE_CSS.sub(' ', data))
            return
        self.parts.append(escape(data))

    def result(self):
        self.close()
        return ''.join(self.parts) + ''.join(f'</{tag}>' for tag in reversed(self.open))


def sanitize(html):
    if not isinstance(html, str) or not html.strip():
        raise ValidationError('Sahifa mazmuni bo‘sh.')
    if len(html) > MAX_HTML:
        raise ValidationError(f'Sahifa {MAX_HTML} belgidan uzun bo‘lmasin.')
    parser = Sanitizer()
    parser.feed(html)
    clean = parser.result()
    if not any(item in clean for item in DATA_PLACEHOLDERS):
        raise ValidationError('Sahifada {{table}}, {{chart}} yoki {{total}} o‘rin egasi bo‘lishi shart; sonlarni o‘zingiz yozmang.')
    return clean


INSTRUCTIONS = '''You can also BUILD A PAGE when a table in chat is not enough, or
the user asks for a sahifa/hisobot/dashboard. First create_tool for the data, then
create_page with your own HTML layout. Write layout, headings and explanation text
only; never write numbers or names yourself. Put the data in with the placeholders
{{table}}, {{chart}}, {{total}}, {{title}}, {{generated_at}}; at least one of the
first three is required and they are filled by the server on every visit. Allowed:
headings, text, lists, div/section, class and style attributes, and a <style> block.
Scripts, images, links, forms and network requests are removed. Pages are private to
this user, kept until deleted, and refreshed with live data. Re-using an existing
title replaces that page. open_page shows a saved page, list_pages lists them.'''


TOOLS = ('create_page', 'open_page', 'list_pages')


def schemas():
    return [
        {'type': 'function', 'name': 'create_page', 'strict': True,
         'description': 'Create or replace a private page that renders a dyn_ workflow result with your HTML layout. Data placeholders are required; the server fills them.',
         'parameters': {'type': 'object', 'additionalProperties': False,
                        'required': ['title', 'html', 'tool', 'arguments_json'],
                        'properties': {
                            'title': {'type': 'string', 'description': 'Page name in Uzbek, 3-80 characters. Reusing a name replaces that page.'},
                            'html': {'type': 'string', 'description': 'Layout HTML with {{table}}, {{chart}}, {{total}}, {{title}}, {{generated_at}} placeholders. No facts of your own.'},
                            'tool': {'type': 'string', 'description': 'Name of a dyn_ tool created in this conversation that returns the rows.'},
                            'arguments_json': {'type': 'string', 'description': 'JSON object with that tool arguments, "{}" when it has none.'}}}},
        {'type': 'function', 'name': 'open_page', 'strict': True,
         'description': 'Open one of this user saved generated pages by its id from list_pages.',
         'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['page_id'],
                        'properties': {'page_id': {'type': 'integer', 'description': 'Page id returned by list_pages or create_page.'}}}},
        {'type': 'function', 'name': 'list_pages', 'strict': True,
         'description': 'List this user saved generated pages with their ids.',
         'parameters': {'type': 'object', 'additionalProperties': False, 'required': [], 'properties': {}}},
    ]


def execute(user, conversation, name, raw, read_only=False):
    if name == 'create_page':
        return create(user, conversation, raw, read_only)
    if name == 'list_pages':
        require(user.is_active)
        items = list(GeneratedPage.objects.filter(user=user)[:MAX_PAGES])
        return {'pages': [{'id': item.pk, 'title': item.title,
                           'updated_at': timezone.localtime(item.updated_at).strftime('%d.%m.%Y %H:%M')} for item in items]}
    page = find(user, raw.get('page_id'))
    return {'page_id': page.pk, 'navigation': {'url': page.get_absolute_url(), 'label': page.title},
            'message': f'«{page.title}» sahifasini ochyapman.'}


def find(user, page_id):
    require(user.is_active)
    page = GeneratedPage.objects.filter(user=user, pk=page_id if isinstance(page_id, int) else 0).first()
    if not page:
        raise ValidationError('Bunday sahifa topilmadi. list_pages bilan ro‘yxatni tekshiring.')
    return page


def create(user, conversation, raw, read_only=False):
    from .models import AgentConversation
    require(user.is_active and conversation.user_id == user.pk)
    title = (raw.get('title') or '').strip()
    if not 3 <= len(title) <= 80:
        raise ValidationError('Sahifa nomi 3–80 belgidan iborat bo‘lsin.')
    definitions = dynamic_tools.registry(user, conversation)
    if raw.get('tool') not in definitions:
        raise ValidationError('Avval shu suhbatda ma’lumot vositasini yarating, keyin sahifaga ulang.')
    definition = dynamic_tools.parse_definition(definitions[raw['tool']])
    if any(step.operation in dynamic_tools.WRITES for step in definition.steps):
        raise ValidationError('Sahifa faqat ma’lumot ko‘rsatadi; o‘zgartirish vositasi ulanmaydi.')
    if definition.steps[-1].operation not in dynamic_tools.OPS:
        raise ValidationError('Sahifaga ulanadigan vosita oxirgi qadamda qatorlar qaytarsin (masalan group_rows yoki select_rows).')
    arguments = json.loads(raw.get('arguments_json') or '{}')
    if not isinstance(arguments, dict):
        raise ValidationError('Vosita argumentlari JSON obyekt bo‘lishi kerak.')
    html = sanitize(raw.get('html'))
    page = GeneratedPage.objects.filter(user=user, title__iexact=title).first()
    if not page and GeneratedPage.objects.filter(user=user).count() >= MAX_PAGES:
        raise ValidationError(f'Sizda {MAX_PAGES} ta sahifa bor. Eskisini o‘chiring yoki nomini takrorlab yangilang.')
    # Run once now: a page that cannot produce data must not be saved.
    fields = {'html': html, 'tool': definition.model_dump(), 'arguments': arguments}
    page = GeneratedPage(pk=page.pk if page else None, user=user, title=title, **fields)
    body(user, page)
    page.save()
    AgentConversation.objects.filter(pk=conversation.pk, user=user).update(updated_at=timezone.now())
    return {'page_id': page.pk, 'title': page.title,
            'navigation': {'url': page.get_absolute_url(), 'label': page.title},
            'message': f'«{page.title}» sahifasi tayyor. Ma’lumot har ochilganda yangilanadi.'}


def table(rows):
    if not rows:
        return '<p class="page-empty">Ma’lumot topilmadi.</p>'
    columns = list(rows[0])[:MAX_COLUMNS]
    head = ''.join(f'<th>{escape(str(name))}</th>' for name in columns)
    body_rows = ''
    for row in rows[:MAX_ROWS]:
        body_rows += '<tr>' + ''.join(f'<td>{escape(cell(row.get(name)))}</td>' for name in columns) + '</tr>'
    more = f'<caption>Dastlabki {MAX_ROWS} ta qator ko‘rsatilgan.</caption>' if len(rows) > MAX_ROWS else ''
    return f'<table class="page-table">{more}<thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>'


def cell(value):
    if isinstance(value, float):
        return f'{value:.2f}'
    return '—' if value is None else str(value)


def chart(rows):
    numeric = [name for name in (rows[0] if rows else {})
               if all(type(row.get(name)) in (int, float) for row in rows)]
    labels = [name for name in (rows[0] if rows else {}) if name not in numeric]
    if not rows or not numeric:
        return '<p class="page-empty">Diagramma uchun son ustuni yo‘q.</p>'
    key, label_key = numeric[0], (labels[0] if labels else None)
    items = rows[:20]
    highest = max([row[key] for row in items] + [0]) or 1
    height, step = 30 * len(items) + 10, 30
    bars = ''
    for index, row in enumerate(items):
        width = max(1, round(abs(row[key]) / highest * 320))
        name = escape(cell(row[label_key]) if label_key else str(index + 1))[:42]
        bars += (f'<text x="0" y="{index*step+19}" class="page-chart-label">{name}</text>'
                 f'<rect x="230" y="{index*step+7}" width="{width}" height="16" rx="3"></rect>'
                 f'<text x="{238+width}" y="{index*step+19}" class="page-chart-value">{escape(cell(row[key]))}</text>')
    return (f'<svg class="page-chart" viewBox="0 0 620 {height}" role="img" '
            f'aria-label="{escape(str(key))} bo‘yicha diagramma">{bars}</svg>')


def body(user, page):
    """Re-run the stored workflow as this viewer and fill the layout's placeholders."""
    result = dynamic_tools.run_definition(user, None, page.tool, page.arguments, set(), read_only=True)
    data = result.get('all_rows')
    if data is None:
        rows = result['data'].get('rows', [])
    else:
        rows = data
    values = {'{{table}}': table(rows), '{{chart}}': chart(rows), '{{total}}': str(len(rows)),
              '{{title}}': escape(page.title),
              '{{generated_at}}': timezone.localtime().strftime('%d.%m.%Y %H:%M')}
    html = page.html
    for name, value in values.items():
        html = html.replace(name, value)
    return html
