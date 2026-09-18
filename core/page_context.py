"""Read the same authorized page as the UI, without running write endpoints."""
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.http import HttpRequest, QueryDict, Http404
from django.urls import resolve, Resolver404


READ_PAGES = {'dashboard', 'tasks', 'task_detail', 'task_create', 'employees',
              'structure', 'chains', 'timeline', 'notifications', 'employee_create', 'employee_edit'}
VOID = {'input', 'img', 'br', 'hr', 'meta', 'link', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'col', 'track'}


class MainContent(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        active = tag == 'main' or bool(self.stack and self.stack[-1][1])
        if tag in ('script', 'style', 'svg') or 'hidden' in attrs or attrs.get('aria-hidden') == 'true':
            active = False
        if active and tag == 'a' and attrs.get('href', '').startswith('/') and not attrs['href'].startswith('//'):
            self.links.append(attrs['href'])
        if tag not in VOID:
            self.stack.append((tag, active))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][1] and data.strip():
            self.parts.append(' '.join(data.split()))


def read(user, path):
    url = urlsplit(path)
    if url.scheme or url.netloc:
        return {}
    try:
        match = resolve(url.path)
    except Resolver404:
        return {}
    if match.url_name not in READ_PAGES:
        return {}
    request = HttpRequest()
    request.method = 'GET'
    request.path = request.path_info = url.path
    request.GET = QueryDict(url.query).copy()
    request.GET.pop('panel', None)  # Read a full page even when the UI uses a drawer.
    request.user = user
    request.session = {}
    request.resolver_match = match
    try:
        response = match.func(request, **match.kwargs)
    except Http404:
        return {}
    if response.status_code != 200:
        return {}
    parser = MainContent()
    parser.feed(response.content.decode('utf-8'))
    text = '\n'.join(parser.parts)
    return {'route': match.url_name, 'path': path, 'text': text[:24000],
            'links': list(dict.fromkeys(parser.links))[:120], 'truncated': len(text) > 24000}
