import base64
import json
from unittest.mock import patch

import requests

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..client import WapioClient, WapioError, international_phone


def response(envelope, status=200):
    http_response = requests.Response()
    http_response.status_code = status
    http_response._content = json.dumps(envelope).encode()
    return http_response


@tagged('post_install', '-at_install')
class TestWapioConnector(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sender = new_test_user(cls.env, login='wapio_test_sender',
                                   groups='base.group_user,wapio_whatsapp.group_wapio_user,sales_team.group_sale_salesman,account.group_account_invoice')
        cls.other = new_test_user(cls.env, login='wapio_other_sender',
                                  groups='base.group_user,wapio_whatsapp.group_wapio_user')
        cls.connection = cls.env['wapio.connection'].create({
            'name': 'Synthetic test', 'api_key': 'synthetic-not-a-real-key', 'company_id': cls.env.company.id,
        })
        cls.session = cls.env['wapio.session'].create({
            'name': 'Synthetic session', 'remote_id': 'test-session',
            'connection_id': cls.connection.id, 'status': 'connected',
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Synthetic recipient', 'phone': '+15555550123'})

    def compose(self, **changes):
        return self.env['wapio.compose'].with_user(self.sender).with_context(
            active_model='res.partner', active_id=self.partner.id).create({
                'session_id': self.session.id, 'text': 'Requested test update', 'consent': True, **changes,
            })

    def test_duplicate_queue_and_timeout_retry_preserve_message(self):
        composer = self.compose()
        action = composer.action_queue()
        self.assertEqual(composer.action_queue()['res_id'], action['res_id'])
        message = self.env['wapio.message'].browse(action['res_id'])
        with patch('requests.request', side_effect=requests.Timeout):
            message._cron_dispatch()
        self.assertEqual(message.state, 'failed')
        key = message.name
        message.with_user(self.sender).action_retry()
        with patch('requests.request', return_value=response({'success': True, 'data': {'msgId': 'synthetic-message'}})) as network:
            message._cron_dispatch()
        self.assertEqual(message.state, 'submitted')
        self.assertEqual(message.name, key)
        self.assertEqual(network.call_args.kwargs['headers']['Idempotency-Key'], 'odoo:' + key)
        self.assertEqual(message.message_id, 'synthetic-message')

    def test_sender_cannot_read_key_change_payload_or_retry_another_owner(self):
        composer = self.compose()
        composer.action_queue()
        message = composer.queued_message_id
        with self.assertRaises(AccessError):
            self.connection.with_user(self.sender).read(['api_key'])
        with self.assertRaises(AccessError):
            message.with_user(self.sender).write({'recipient': '+15555550124'})
        with self.assertRaises(AccessError):
            message.with_user(self.other).read(['recipient'])
        with self.assertRaises(AccessError):
            message.with_user(self.other).action_retry()

    def test_wrong_company_and_missing_consent_cannot_queue(self):
        with self.assertRaises(UserError):
            self.compose(consent=False).action_queue()
        second_company = self.env['res.company'].create({'name': 'Other test company'})
        foreign_partner = self.partner.copy({'company_id': second_company.id})
        composer = self.compose(source_id=foreign_partner.id)
        with self.assertRaises(AccessError):
            composer.action_queue()

    def test_session_refresh_paginates_and_does_not_store_private_fields(self):
        pages = [response({'success': True, 'data': [{'session_id': 'one', 'name': 'First', 'status': 'connected', 'webhook_url': 'https://private.invalid'}], 'next_cursor': 'cursor'}),
                 response({'success': True, 'data': [{'session_id': 'two', 'name': 'Second', 'status': 'disconnected'}], 'next_cursor': None})]
        with patch('requests.request', side_effect=pages):
            self.connection.action_refresh_sessions()
        self.assertEqual(set(self.connection.session_ids.mapped('remote_id')), {'test-session', 'one', 'two'})
        self.assertEqual(self.session.status, 'unavailable')
        self.assertNotIn('webhook_url', self.env['wapio.session']._fields)

    def test_pdf_upload_is_persisted_before_submission_and_retry_reuses_handle(self):
        composer = self.compose()
        composer.action_queue()
        message = composer.queued_message_id.sudo()
        payload = {'session_id': self.session.remote_id, 'to': '+15555550123',
                   'kind': 'document', 'mime_type': 'application/pdf', 'file_name': 'test.pdf'}
        message.write({'state': 'preparing', 'pdf_content': base64.b64encode(b'%PDF-1.4\nsynthetic'), 'payload_json': json.dumps(payload)})
        grant = {'success': True, 'media_handle': 'test-account/test-media',
                 'upload_url': 'https://media.wapio.io/test-upload',
                 'required_headers': {'content-type': 'application/pdf'}}
        with patch('requests.request', return_value=response(grant)), patch('requests.put', return_value=response({})):
            message._cron_dispatch()
        self.assertEqual(message.state, 'ready')
        self.assertFalse(message.pdf_content)
        stable_payload = message.payload_json
        with patch('requests.request', side_effect=requests.Timeout):
            message._cron_dispatch()
        message.with_user(self.sender).action_retry()
        self.assertEqual(message.payload_json, stable_payload)
        self.assertEqual(json.loads(stable_payload)['media_handle'], 'test-account/test-media')

    def test_http_errors_missing_ids_and_redirects_never_claim_submission(self):
        for http_response in (response({'success': False}, 401), response({'success': False}, 409),
                              response({}, 302), response({'success': True, 'data': {}})):
            with self.subTest(status=http_response.status_code):
                with patch('requests.request', return_value=http_response), self.assertRaises(WapioError):
                    WapioClient('synthetic').send({'session_id': 'test', 'text': 'test'}, 'stable-id')

    def test_media_grants_cannot_forward_credentials_or_upload_to_foreign_hosts(self):
        for upload_url in ('http://media.wapio.io/a', 'https://evil.invalid/a', 'https://media.wapio.io.evil.invalid/a'):
            with self.subTest(url=upload_url):
                grant = {'success': True, 'upload_url': upload_url, 'media_handle': 'test', 'required_headers': {}}
                with patch('requests.request', return_value=response(grant)), patch('requests.put') as upload:
                    with self.assertRaises(WapioError):
                        WapioClient('synthetic').upload_pdf(b'test')
                    upload.assert_not_called()

    def test_phone_requires_country_code(self):
        self.assertEqual(international_phone('+1 (555) 555-0123'), '+15555550123')
        for phone in ('05555550123', '+0001234567', '', '+123', 'https://evil.invalid'):
            with self.subTest(phone=phone), self.assertRaises(WapioError):
                international_phone(phone)

    def test_source_actions_use_actual_lead_sale_and_invoice_records(self):
        sources = [self.env['crm.lead'].create({'name': 'Requested inquiry', 'partner_id': self.partner.id, 'phone': self.partner.phone, 'user_id': self.sender.id}),
                   self.env['sale.order'].create({'partner_id': self.partner.id, 'user_id': self.sender.id}),
                   self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.partner.id})]
        for source in sources:
            with self.subTest(model=source._name):
                composer = self.env['wapio.compose'].with_user(self.sender).with_context(
                    active_model=source._name, active_id=source.id).create({
                        'session_id': self.session.id, 'consent': True, 'text': 'Requested update'})
                composer.action_queue()
                self.assertEqual(composer.queued_message_id.recipient, self.partner.phone)
                self.assertEqual(composer.source_model, source._name)
        sale_report, report_format = self.env['ir.actions.report']._render_qweb_html(
            'sale.action_report_saleorder', sources[1].ids)
        self.assertEqual(report_format, 'html')
        self.assertIn(b'Quotation', sale_report)

    def test_pdf_fallback_is_rejected_instead_of_uploading_html(self):
        order = self.env['sale.order'].create({'partner_id': self.partner.id, 'user_id': self.sender.id})
        composer = self.env['wapio.compose'].with_user(self.sender).with_context(
            active_model='sale.order', active_id=order.id).create({
                'session_id': self.session.id, 'consent': True, 'text': 'Requested quotation', 'include_pdf': True})
        # Odoo's real test-mode renderer returns HTML without wkhtmltopdf.
        with self.assertRaisesRegex(UserError, 'did not generate a PDF'):
            composer.action_queue()
        self.assertFalse(composer.queued_message_id)
