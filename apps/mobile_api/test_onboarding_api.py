from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.companies.onboarding_models import CompanyOwnerInvitation
from apps.companies.models import Company
from apps.companies.services import create_company
from apps.mobile_api.onboarding_views import _client_ip
from apps.companies.onboarding_services import _client_ip as onboarding_client_ip
from apps.users.utils import _client_ip as audit_client_ip
from django.test import RequestFactory


User = get_user_model()


class MobileBusinessRegistrationTests(TestCase):
    def test_throttle_identity_ignores_untrusted_forwarded_header(self):
        request = RequestFactory().post(
            "/api/mobile/v1/business/request/",
            REMOTE_ADDR="192.0.2.10",
            HTTP_X_FORWARDED_FOR="198.51.100.2",
        )
        self.assertEqual(_client_ip(request), "192.0.2.10")
        self.assertEqual(onboarding_client_ip(request), "192.0.2.10")
        self.assertEqual(audit_client_ip(request), "192.0.2.10")

    def setUp(self):
        self.admin = User.objects.create_user(
            username="platform-reviewer",
            email="platform@example.test",
            password="StrongAdminPass!2026",
            is_active=True,
            is_platform_admin=True,
        )
        self.url = reverse("mobile_api:business_registration_request")
        self.payload = {
            "business_type": "pig",
            "owner_email": "new-owner@example.test",
            "company_name": "New Owner Pork House",
            "company_code": "new-owner-pork",
            "company_email": "new-owner@example.test",
            "company_phone": "+255700000001",
            "company_address": "Njombe",
            "company_timezone": "Africa/Dar_es_Salaam",
            "allowed_languages": "sw",
            "default_language": "sw",
        }

    def _create_active_company_owner(self):
        owner = User.objects.create_user(
            username="signed-owner",
            email="signed@example.test",
            password="StrongOwnerPass!2026",
            is_active=True,
        )
        create_company(
            actor=self.admin,
            name="Signed Owner Existing Company",
            code="signed-owner-existing",
            owner=owner,
            allowed_languages=["sw"],
            default_language="sw",
            step_up_verified=True,
        )
        return owner

    def test_public_request_enters_review_without_creating_access(self):
        response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 201)
        invitation = CompanyOwnerInvitation.objects.get(
            email="new-owner@example.test"
        )
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.SUBMITTED,
        )
        self.assertEqual(invitation.created_by, self.admin)
        self.assertFalse(
            User.objects.filter(email="new-owner@example.test").exists()
        )
        self.assertFalse(Company.objects.filter(code="new-owner-pork").exists())
        event = invitation.events.get(
            action="MOBILE_REGISTRATION_REQUESTED"
        )
        self.assertEqual(event.metadata["business_type"], "pig")
        self.assertFalse(event.metadata["authenticated_request"])
        self.assertEqual(event.metadata["owner_full_name"], "")
        self.assertEqual(event.metadata["owner_phone"], "")

    def test_owner_contact_is_recorded_in_request_audit(self):
        payload = {**self.payload,
                   "owner_full_name": "Amina Sanga",
                   "owner_phone": "+255700000111"}
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 201)
        invitation = CompanyOwnerInvitation.objects.get(email=payload["owner_email"])
        event = invitation.events.get(action="MOBILE_REGISTRATION_REQUESTED")
        self.assertEqual(event.metadata["owner_full_name"], "Amina Sanga")
        self.assertEqual(event.metadata["owner_phone"], "+255700000111")

    def test_invalid_owner_phone_does_not_create_request(self):
        payload = {**self.payload, "owner_phone": "not-a-number"}
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CompanyOwnerInvitation.objects.filter(
            email=payload["owner_email"]).exists())

    def test_request_requires_csrf_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            CompanyOwnerInvitation.objects.filter(
                email="new-owner@example.test"
            ).exists()
        )

    def test_existing_identity_must_sign_in_before_adding_business(self):
        User.objects.create_user(
            username="existing-owner",
            email="existing@example.test",
            password="StrongOwnerPass!2026",
        )
        payload = {**self.payload, "owner_email": "existing@example.test"}

        response = self.client.post(self.url, payload)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "account_exists_sign_in")

    def test_signed_in_user_must_use_own_email(self):
        owner = self._create_active_company_owner()
        self.client.force_login(owner)

        response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "owner_email_mismatch")

    def test_signed_in_owner_can_request_an_additional_business(self):
        owner = self._create_active_company_owner()
        self.client.force_login(owner)
        payload = {**self.payload, "owner_email": "signed@example.test"}

        response = self.client.post(self.url, payload)

        self.assertEqual(response.status_code, 201)
        invitation = CompanyOwnerInvitation.objects.get(
            email="signed@example.test"
        )
        self.assertEqual(
            invitation.status,
            CompanyOwnerInvitation.Status.SUBMITTED,
        )

    def test_unavailable_business_type_is_rejected(self):
        payload = {**self.payload, "business_type": "retail"}

        response = self.client.post(self.url, payload)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "business_type_unavailable")

    def test_transport_registration_records_selected_services(self):
        payload = {
            **self.payload,
            "business_type": "transport",
            "company_name": "Highland Transport",
            "company_code": "highland-transport",
            "transport_cargo_enabled": "true",
            "transport_passenger_enabled": "true",
        }
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 201)
        invitation = CompanyOwnerInvitation.objects.get(email=payload["owner_email"])
        self.assertEqual(invitation.business_type, "transport")
        self.assertTrue(invitation.transport_cargo_enabled)
        self.assertTrue(invitation.transport_passenger_enabled)

    def test_transport_registration_requires_at_least_one_service(self):
        payload = {
            **self.payload,
            "business_type": "transport",
            "transport_cargo_enabled": "false",
            "transport_passenger_enabled": "false",
        }
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CompanyOwnerInvitation.objects.filter(email=payload["owner_email"]).exists())

    def test_review_requires_one_active_platform_admin(self):
        User.objects.create_user(
            username="second-reviewer",
            email="second-platform@example.test",
            password="StrongAdminPass!2026",
            is_active=True,
            is_platform_admin=True,
        )

        response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["code"],
            "registration_review_unavailable",
        )
