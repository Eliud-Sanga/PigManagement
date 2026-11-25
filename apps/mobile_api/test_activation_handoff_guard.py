from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from apps.companies.activation_services import issue_company_user_activation, complete_company_user_activation
from apps.mobile_api.test_platform_member_activation_api import MemberActivationApiTests
from apps.users.models import GoogleIdentity


class ActivationHandoffGuardTests(MemberActivationApiTests):
    def handoff(self, client, activation, token):
        return client.get(reverse('users:company_user_activation_handoff', args=[activation.public_id, token]), secure=True)

    def test_valid_link_allows_completion(self):
        activation, token = issue_company_user_activation(membership=self.member, actor=self.admin)
        browser = Client()
        self.assertEqual(self.handoff(browser, activation, token).status_code, 302)
        response = browser.post(reverse('users:company_user_activation_complete'), {
            'new_password1': 'Valid-Passphrase-482!', 'new_password2': 'Valid-Passphrase-482!'}, secure=True)
        self.assertEqual(response.status_code, 302, response.content)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_active)

    def test_rotation_invalidates_an_existing_handoff_session(self):
        activation, token = issue_company_user_activation(membership=self.member, actor=self.admin)
        old, new = Client(), Client()
        self.assertEqual(self.handoff(old, activation, token).status_code, 302)
        activation, new_token = issue_company_user_activation(membership=self.member, actor=self.admin)
        response = old.post(reverse('users:company_user_activation_complete'), {
            'new_password1': 'Valid-Passphrase-482!', 'new_password2': 'Valid-Passphrase-482!'}, secure=True)
        self.assertEqual(response.status_code, 400)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)
        self.assertEqual(self.handoff(new, activation, new_token).status_code, 302)

    def test_rotation_is_checked_again_under_transaction(self):
        activation, token = issue_company_user_activation(membership=self.member, actor=self.admin)
        old_hash = activation.token_hash
        issue_company_user_activation(membership=self.member, actor=self.admin)
        with self.assertRaises(ValidationError):
            complete_company_user_activation(activation=activation, new_password='Valid-Passphrase-482!', expected_token_hash=old_hash)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)

    def test_google_identity_added_after_issue_invalidates_link(self):
        activation, token = issue_company_user_activation(membership=self.member, actor=self.admin)
        GoogleIdentity.objects.create(user=self.pending, subject='v40-google-added', email_at_link=self.pending.email)
        browser = Client()
        self.assertEqual(self.handoff(browser, activation, token).status_code, 400)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)

    def test_google_identity_added_after_handoff_blocks_completion(self):
        activation, token = issue_company_user_activation(membership=self.member, actor=self.admin)
        browser = Client()
        self.assertEqual(self.handoff(browser, activation, token).status_code, 302)
        GoogleIdentity.objects.create(user=self.pending, subject='v40-google-after-handoff', email_at_link=self.pending.email)
        response = browser.post(reverse('users:company_user_activation_complete'), {
            'new_password1': 'Valid-Passphrase-482!', 'new_password2': 'Valid-Passphrase-482!'}, secure=True)
        self.assertEqual(response.status_code, 400)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)

    def test_legacy_session_without_token_proof_cannot_activate(self):
        activation, token = issue_company_user_activation(membership=self.member, actor=self.admin)
        browser = Client()
        session = browser.session
        session['pigs_company_user_activation'] = str(activation.public_id)
        session.save()
        response = browser.get(reverse('users:company_user_activation_complete'), secure=True)
        self.assertEqual(response.status_code, 400)
