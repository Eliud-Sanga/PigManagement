"""Small-fixture read-path benchmark; not a network or load test."""
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from apps.companies.scope import company_scope
from .test_home_m26_backend import BackendReadTests
from . import workspace_views as pig_api, transport_views as tr_api, platform_views as admin_api


class ReadPathBenchmark(BackendReadTests):
    def test_measure_read_paths(self):
        self.assertEqual(connection.vendor, 'postgresql')
        def measure(name, action, limit):
            action()  # warmup, outside samples
            times, counts = [], []
            for _ in range(20):
                with CaptureQueriesContext(connection) as queries:
                    start = perf_counter()
                    action()
                    times.append((perf_counter() - start) * 1000)
                counts.append(len(queries))
            self.assertLessEqual(max(counts), limit, name)
            print(f'BENCH {name}: samples=20 queries_max={max(counts)} median_ms={median(times):.3f} p95_ms={sorted(times)[18]:.3f}', flush=True)
        def checked(response):
            self.assertEqual(response.status_code, 200)
        req = RequestFactory().get('/pig/dashboard/')
        req.user = self.actor
        member = SimpleNamespace(role=SimpleNamespace(name='Fixture role'))
        codes = frozenset(code for group in pig_api.MODULE_PERMISSIONS.values() for code in group)
        with company_scope(self.a), patch.object(pig_api, '_business_context', return_value=(self.a, member, codes, None)):
            measure('pig_dashboard', lambda: checked(pig_api.pig_dashboard(req)), 14)
        with company_scope(self.transport):
            measure('transport_schedule_list', lambda: [tr_api._record_payload('passenger_schedules', s) for s in tr_api._module_queryset('passenger_schedules')], 1)
        req = RequestFactory().get('/platform/overview/')
        req.user = SimpleNamespace(is_authenticated=True, is_active=True, is_platform_admin=True)
        measure('admin_overview', lambda: checked(admin_api.overview(req)), 2)
        measure('admin_users_list', lambda: checked(admin_api.records(req, 'users')), 2)
