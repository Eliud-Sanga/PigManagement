"""Read-only period reports; existing ledger values and permission boundaries.

Totals and chart buckets are computed by the database over the whole period,
independently of record pagination. No write, migration or new profit formula.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.pigs.models import BatchProfitReport, DailySale, Expense, Purchase
from .views import _json
from .workspace_views import (
    _business_context, _error, _money, _purchase_item, _expense_item,
    _report_item, _sale_item,
)

ZERO = Decimal('0.00')
SPECS = {
    'sales': (DailySale, 'sale_date', 'total_money_received', 'pigs.view_dailysalereport'),
    'purchases': (Purchase, 'purchase_date', 'total_cost', 'pigs.view_purchase'),
    'expenses': (Expense, 'expense_date', 'amount', 'pigs.view_expense'),
    'profit': (BatchProfitReport, 'finalized_date', 'total_profit', 'pigs.view_batchprofitreport'),
}


def _period(request):
    start = date.fromisoformat(request.GET.get('start', ''))
    end = date.fromisoformat(request.GET.get('end', ''))
    if end < start or (end - start).days > 3660:
        raise ValueError('Invalid date range')
    group = request.GET.get('group', 'day')
    if group not in ('day', 'month'):
        raise ValueError('Invalid grouping')
    page = int(request.GET.get('page', '1'))
    if not 1 <= page <= 100000:
        raise ValueError('Invalid page')
    return start, end, group, page


def _query(kind, company, start, end):
    model, field, _, _ = SPECS[kind]
    # Explicit filter supplements the fail-closed scoped manager.
    return model.objects.filter(company=company, **{field+'__range': (start, end)})


def _summary(kind, qs):
    amount = SPECS[kind][2]
    fields = {'total': Sum(amount), 'count': Count('pk')}
    if kind == 'sales':
        fields.update(meat=Sum('total_meat_sales'), food=Sum('total_food_sales'))
    elif kind == 'purchases':
        fields.update(pigs=Sum('number_of_pigs'))
    elif kind == 'profit':
        fields.update(meat=Sum('total_meat_sales'), cost=Sum('total_purchase_cost'), pigs=Sum('total_pigs'))
    values = qs.aggregate(**fields)
    return {k: int(v or 0) if k in ('count', 'pigs') else _money(v) for k, v in values.items()}


def _trend(kind, qs, group):
    _, field, amount, _ = SPECS[kind]
    if group == 'month':
        rows = qs.order_by().annotate(bucket=TruncMonth(field)).values('bucket').annotate(total=Sum(amount), count=Count('pk')).order_by('bucket')
        return [{'date': r['bucket'].isoformat(), 'total': _money(r['total']), 'count': r['count']} for r in rows]
    rows = qs.order_by().values(field).annotate(total=Sum(amount), count=Count('pk')).order_by(field)
    return [{'date': r[field].isoformat(), 'total': _money(r['total']), 'count': r['count']} for r in rows]


@never_cache
@require_GET
def period_report(request, kind):
    if kind not in ('all', *SPECS):
        return _error(request, 'not_found', 'Report not found.', 404)
    required = ('pigs.view_dailysalereport', 'pigs.view_batchprofitreport') if kind == 'all' else (SPECS[kind][3],)
    company, membership, codes, error = _business_context(request, permissions=required)
    if error is not None:
        return error
    try:
        start, end, group, page = _period(request)
    except (ValueError, TypeError, OverflowError):
        return _error(request, 'invalid_period', 'Choose a valid start and end date within ten years.', 400)
    allowed = {key: spec[3] in codes for key, spec in SPECS.items()}
    kinds = [key for key in SPECS if allowed[key]] if kind == 'all' else [kind]
    summaries, series = {}, {}
    selected = None
    for key in kinds:
        qs = _query(key, company, start, end)
        summaries[key] = _summary(key, qs)
        series[key] = _trend(key, qs, group)
        if key == kind:
            selected = qs
    items, count, has_next = [], 0, False
    if selected is not None:
        query = request.GET.get('q', '').strip()[:200]
        if query:
            if kind == 'sales':
                try:
                    selected = selected.filter(sale_date=date.fromisoformat(query))
                except ValueError:
                    selected = selected.none()
            elif kind == 'purchases':
                selected = selected.filter(supplier_name__icontains=query)
            elif kind == 'expenses':
                selected = selected.filter(title__icontains=query)
            elif kind == 'profit':
                selected = selected.filter(slaughter_batch__batch_number__icontains=query)
        count = selected.count()
        selected = selected.order_by('-'+SPECS[kind][1], '-pk')
        if kind == 'sales':
            selected = selected.select_related('slaughter_batch')
        elif kind == 'profit':
            selected = selected.select_related('slaughter_batch')
        limit = 25
        offset = (page-1)*limit
        for item in selected[offset:offset+limit]:
            if kind == 'sales':
                data = _sale_item(item)
            elif kind == 'purchases':
                data = _purchase_item(item)
                data['pigs'] = item.number_of_pigs
            elif kind == 'expenses':
                data = _expense_item(item)
            else:
                data = _report_item('batch', item)
                data['reportId'] = str(item.pk)
                data['pigs'] = item.total_pigs
            items.append(data)
        has_next = offset+limit < count
    return _json(request, {
        'ok': True, 'company': {'id': str(company.public_id), 'name': company.name},
        'currency': 'TZS', 'kind': kind, 'start': start.isoformat(), 'end': end.isoformat(),
        'group': group, 'available': allowed, 'summary': summaries, 'series': series,
        'items': items, 'count': count, 'page': page, 'hasNext': has_next,
    })


@never_cache
@require_GET
def profit_detail(request, report_id):
    company, membership, codes, error = _business_context(request, permissions=('pigs.view_batchprofitreport',))
    if error is not None:
        return error
    report = BatchProfitReport.objects.filter(company=company, pk=report_id).select_related('slaughter_batch').first()
    if report is None:
        return _error(request, 'not_found', 'Report not found.', 404)
    item = _report_item('batch', report)
    item.update(reportId=str(report.pk), pigs=report.total_pigs, start=report.slaughter_batch.slaughter_date.isoformat(), end=report.finalized_date.isoformat())
    # Batch-level attribution is existing business logic. Never divide by pigs.
    sales = DailySale.objects.filter(company=company, slaughter_batch=report.slaughter_batch).order_by('sale_date').values('sale_date', 'total_meat_sales')
    return _json(request, {'ok': True, 'company': {'id': str(company.public_id), 'name': company.name}, 'report': item,
        'daily': [{'date': r['sale_date'].isoformat(), 'total': _money(r['total_meat_sales'])} for r in sales]})
