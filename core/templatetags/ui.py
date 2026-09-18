from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()
ICONS = {
    'grid': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'tasks': '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="m8 8 1 1 2-2m2 1h3M8 13h8M8 17h5"/>',
    'users': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/><circle cx="9" cy="7" r="4"/>',
    'structure': '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/>',
    'bell': '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    'sun': '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'arrow': '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    'back': '<path d="M19 12H5m6-6-6 6 6 6"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'alert': '<path d="m12 3 10 18H2L12 3ZM12 9v4m0 4h.01"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    'mic': '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
    'exit': '<path d="M9 3H4v18h5M9 12h12m-5-5 5 5-5 5"/>',
    'chevron': '<path d="m9 5 7 7-7 7"/>',
    'menu': '<path d="M4 6h16M4 12h16M4 18h16"/>',
    'close': '<path d="m6 6 12 12M6 18 18 6"/>',
    'history': '<path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7m2-5v6l4 2"/>',
    'link': '<path d="m10 13 4-2m-6 5-2 1a4 4 0 0 1-4-7l5-3a4 4 0 0 1 6 2m-2 6a4 4 0 0 0 6 2l5-3a4 4 0 0 0-4-7l-2 1"/>',
}


@register.simple_tag
def icon(name):
    return format_html('<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>', mark_safe(ICONS.get(name, ICONS['tasks'])))
