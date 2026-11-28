import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.companies.middleware import COMPANY_SESSION_KEY
from apps.companies.models import Company

from . import workspace_views


class DummySession(dict):
    modified = False


def user(*, platform_admin=False):
    return SimpleNamespace(
        pk=7,
        is_authenticated=True,
        is_platform_admin=platform_admin,
        get_username=lambda: "eliud",
    )


class MobileWorkspaceContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_workspace_denies_anonymous_user(self):
        request = self.factory.get("/api/mobile/v1/workspace/")
        request.user = AnonymousUser()

        response = workspace_views.workspace(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            json.loads(response.content)["code"],
            "authentication_required",
        )

    def test_platform_admin_stays_out_of_business_plane(self):
        request = self.factory.get("/api/mobile/v1/workspace/")
        request.user = user(platform_admin=True)

        response = workspace_views.workspace(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["platformAdmin"])
        self.assertFalse(payload["businessAccessAllowed"])
        self.assertEqual(payload["companies"], [])

    def test_dashboard_requires_selected_company(self):
        request = self.factory.get("/api/mobile/v1/pig/dashboard/")
        request.user = user()
        request.company = None
        request.company_membership = None

        response = workspace_views.pig_dashboard(request)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            json.loads(response.content)["code"],
            "company_selection_required",
        )

    @patch("apps.mobile_api.workspace_views.resolve_company_context")
    def test_company_selection_is_resolved_before_session_write(self, resolve):
        company = SimpleNamespace(
            public_id="a8e1b4bd-7e65-4c31-8444-f16ed4ef9710",
            code="pig-one",
            name="Pig One",
        )
        resolve.return_value = SimpleNamespace(company=company)
        request = self.factory.post(
            "/api/mobile/v1/workspace/company/select/",
            data={"company": str(company.public_id)},
        )
        request._dont_enforce_csrf_checks = True
        request.user = user()
        request.session = DummySession()

        response = workspace_views.select_company(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            request.session[COMPANY_SESSION_KEY],
            str(company.public_id),
        )
        self.assertTrue(request.session.modified)
        resolve.assert_called_once_with(
            user=request.user,
            company_public_id=str(company.public_id),
        )

    @patch("apps.mobile_api.workspace_views.get_effective_permission_codes")
    @patch("apps.mobile_api.workspace_views.CompanyMembership")
    def test_workspace_returns_only_membership_companies(
        self,
        membership_model,
        effective_permissions,
    ):
        company = SimpleNamespace(
            public_id="9f337ef4-b741-4ec1-8c97-699b544b9c52",
            code="farm",
            name="Eliud Farm",
            default_language="sw",
            business_type=Company.BusinessType.PIG,
        )
        role = SimpleNamespace(code="OWNER", name="Owner")
        membership = SimpleNamespace(
            company=company,
            role=role,
            approved_language="sw",
            requested_language="sw",
        )
        membership_model.objects.active.return_value.filter.return_value.select_related.return_value.order_by.return_value = [
            membership
        ]
        effective_permissions.return_value = frozenset({"pigs.view_pig"})

        request = self.factory.get("/api/mobile/v1/workspace/")
        request.user = user()
        request.company = company
        request.company_membership = membership

        response = workspace_views.workspace(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["companies"]), 1)
        self.assertEqual(payload["selectedCompany"]["name"], "Eliud Farm")
        self.assertEqual(payload["permissions"], ["pigs.view_pig"])
        self.assertEqual(payload["selectedCompany"]["businessType"], "pig")
        self.assertIsNone(payload["selectedCompany"]["transportServices"])
        self.assertTrue(payload["selectedCompany"]["selected"])

    def test_transport_company_payload_reports_enabled_services(self):
        company = SimpleNamespace(
            public_id="13f49e07-b99e-48d5-8ce8-18b34c37e647",
            code="transport-test",
            name="Synthetic Transport",
            default_language="sw",
            business_type=Company.BusinessType.TRANSPORT,
        )
        membership = SimpleNamespace(
            company=company,
            role=SimpleNamespace(code="OWNER", name="Owner"),
            approved_language="sw",
            requested_language="sw",
        )
        for profile, expected in (
            (None, {"cargo": False, "passenger": False}),
            (SimpleNamespace(cargo_enabled=True, passenger_enabled=False),
             {"cargo": True, "passenger": False}),
            (SimpleNamespace(cargo_enabled=False, passenger_enabled=True),
             {"cargo": False, "passenger": True}),
            (SimpleNamespace(cargo_enabled=True, passenger_enabled=True),
             {"cargo": True, "passenger": True}),
        ):
            with self.subTest(expected=expected):
                company.transport_profile = profile
                payload = workspace_views._company_payload(membership)
                self.assertEqual(payload["businessType"], "transport")
                self.assertEqual(payload["transportServices"], expected)
                self.assertFalse(payload["selected"])

    @override_settings(DEBUG=True)
    def test_new_endpoints_keep_exact_dev_cors_allowlist(self):
        request = self.factory.get(
            "/api/mobile/v1/workspace/",
            HTTP_ORIGIN="http://127.0.0.1:5173",
        )
        request.user = AnonymousUser()

        response = workspace_views.workspace(request)

        self.assertEqual(
            response["Access-Control-Allow-Origin"],
            "http://127.0.0.1:5173",
        )
        self.assertEqual(
            response["Access-Control-Allow-Credentials"],
            "true",
        )
