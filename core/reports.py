"""Employee task statistics, shown on the employees page and exported to Excel."""
import io
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Task

COLUMNS = [('T/R', 7), ('F.I.Sh.', 50), ('Topshiriqlar soni', 15), ('Bajarilmoqda', 17),
           ('Bajarilmagan', 19), ('Tanishilmagan topshiriqlar', 19)]


def parse_week(value):
    """'2026-W38' (HTML week input) -> (monday, next monday) as aware datetimes, else None."""
    try:
        year, week = value.split('-W')
        monday = date.fromisocalendar(int(year), int(week), 1)
    except (AttributeError, ValueError):
        return None
    start = timezone.make_aware(datetime.combine(monday, time.min))
    return start, start + timedelta(days=7)


def current_week():
    year, week, _ = timezone.localdate().isocalendar()
    return f'{year}-W{week:02d}'


def employee_stats(user, period=None):
    tasks = Task.objects.visible_to(user)
    if period:
        tasks = tasks.filter(created_at__gte=period[0], created_at__lt=period[1])
    now = timezone.now()
    overdue = Q(status='active', due_at__lt=now)
    return list(tasks.values('assignee_id', 'assignee__full_name').annotate(
        total=Count('pk'),
        in_progress=Count('pk', filter=Q(status='submitted') | (Q(status='active') & ~overdue)),
        overdue=Count('pk', filter=overdue),
        unseen=Count('pk', filter=Q(seen_at__isnull=True)),
    ).order_by('-total', 'assignee__full_name'))


def period_label(period):
    if not period:
        return 'Barcha vaqt'
    last = timezone.localtime(period[1]) - timedelta(days=1)
    return f"{timezone.localtime(period[0]):%d.%m.%Y}–{last:%d.%m.%Y}"


def workbook(rows, period, prepared_by):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = 'Xodimlar statistikasi'
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    colors = {4: '00B050', 5: 'FF0000'}
    for index, (_, width) in enumerate(COLUMNS, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet['C1'] = 'Xodimlar statistikasi'
    sheet['C1'].font = Font(name='Times New Roman', size=14, bold=True)
    sheet['C2'] = f'Davr: {period_label(period)}'
    sheet['C3'] = f'{timezone.localdate():%d.%m.%Y}-yil'
    for cell in ('C2', 'C3'):
        sheet[cell].font = Font(name='Times New Roman', size=14)
    for index, (title, _) in enumerate(COLUMNS, 1):
        cell = sheet.cell(row=5, column=index, value=title)
        cell.font = Font(name='Times New Roman', size=12, bold=True, color=colors.get(index))
        cell.alignment, cell.border = center, border
    for number, row in enumerate(rows, 1):
        values = [number, row['assignee__full_name'], row['total'], row['in_progress'], row['overdue'], row['unseen']]
        for index, value in enumerate(values, 1):
            cell = sheet.cell(row=5+number, column=index, value=value)
            cell.font = Font(name='Times New Roman', size=14, color=colors.get(index))
            cell.alignment = Alignment(horizontal='left' if index == 2 else 'center', vertical='center', wrap_text=index == 2)
            cell.border = border
    sheet.cell(row=5+len(rows)+3, column=3, value=prepared_by).font = Font(name='Times New Roman', size=14)
    sheet.print_options.horizontalCentered = True
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
