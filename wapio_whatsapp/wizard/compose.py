import base64
import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..client import international_phone, WapioError

SOURCE_REPORTS = {'sale.order': 'sale.action_report_saleorder',
                  'account.move': 'account.account_invoices'}
SOURCE_MODELS = ('res.partner', 'crm.lead', 'sale.order', 'account.move')


class WapioCompose(models.TransientModel):
    _name = 'wapio.compose'
    _description = 'Compose Wapio Message'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    session_id = fields.Many2one('wapio.session', required=True, check_company=True,
                                domain="[('company_id', '=', company_id), ('status', '=', 'connected')]")
    source_model = fields.Selection([(name, name) for name in SOURCE_MODELS], required=True)
    source_id = fields.Integer(required=True)
    recipient = fields.Char(required=True)
    text = fields.Text(required=True)
    include_pdf = fields.Boolean(string='Attach quotation / invoice PDF')
    consent = fields.Boolean(string='This recipient expects this message', required=True)
    queued_message_id = fields.Many2one('wapio.message', readonly=True)

    @api.model
    def default_get(self, requested_fields):
        defaults = super().default_get(requested_fields)
        model_name, record_id = self.env.context.get('active_model'), self.env.context.get('active_id')
        if model_name not in SOURCE_MODELS or not record_id:
            raise UserError('Open Wapio from a contact, lead, quotation or customer invoice.')
        source = self.env[model_name].browse(record_id).exists()
        source.check_access('read')
        partner = source if model_name == 'res.partner' else source.partner_id
        company = source.company_id or self.env.company
        defaults.update(source_model=model_name, source_id=record_id, company_id=company.id,
                        recipient=(source.phone if model_name == 'crm.lead' else partner.phone) or '',
                        text='Hello, here is your requested update.')
        return defaults

    def _source(self):
        self.ensure_one()
        if self.source_model not in SOURCE_MODELS:
            raise UserError('Unsupported source record.')
        source = self.env[self.source_model].browse(self.source_id).exists()
        if not source:
            raise UserError('The source record no longer exists.')
        source.check_access('read')
        if source.company_id and source.company_id != self.company_id:
            raise AccessError('The source belongs to another company.')
        if self.company_id not in self.env.companies:
            raise AccessError('Select an allowed company.')
        return source

    def _pdf(self, source):
        if self.source_model not in SOURCE_REPORTS:
            raise UserError('PDF attachments are available only for quotations and customer invoices.')
        if self.source_model == 'account.move' and source.move_type not in ('out_invoice', 'out_refund'):
            raise UserError('Select a customer invoice or credit note.')
        pdf_bytes, report_format = self.env['ir.actions.report']._render_qweb_pdf(
            SOURCE_REPORTS[self.source_model], res_ids=source.ids)
        if report_format != 'pdf' or not pdf_bytes.startswith(b'%PDF-'):
            raise UserError('Odoo did not generate a PDF. Check the server PDF renderer.')
        if len(pdf_bytes) > 16_777_216:
            raise UserError('The PDF exceeds the connector limit of 16 MiB.')
        return base64.b64encode(pdf_bytes)

    def action_queue(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('wapio_whatsapp.group_wapio_user'):
            raise AccessError('Wapio sender permission is required.')
        self.env.cr.execute('SELECT id FROM wapio_compose WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.queued_message_id:
            return self._queued_action()
        source = self._source()
        self.session_id.check_access('read')
        if self.session_id.company_id != self.company_id or self.session_id.status != 'connected':
            raise UserError('Select a connected session in the source company.')
        if not self.consent or not self.text.strip() or len(self.text) > 65536:
            raise UserError('Confirm the recipient expects this message and enter up to 65,536 characters.')
        try:
            recipient = international_phone(self.recipient)
        except WapioError as error:
            raise UserError(str(error)) from None
        self._queue_message(source, recipient)
        return self._queued_action()

    def _queue_message(self, source, recipient):
        payload = {'session_id': self.session_id.remote_id, 'to': recipient}
        pdf_content = self._pdf(source) if self.include_pdf else False
        if pdf_content:
            payload.update(kind='document', caption=self.text, mime_type='application/pdf',
                           file_name=f'{self.source_model.replace(".", "-")}-{self.source_id}.pdf')
        else:
            payload['text'] = self.text
        self.queued_message_id = self.env['wapio.message'].sudo().create({
            'company_id': self.company_id.id, 'owner_id': self.env.uid,
            'session_id': self.session_id.id, 'recipient': recipient,
            'payload_json': json.dumps(payload), 'pdf_content': pdf_content,
            'state': 'preparing' if pdf_content else 'ready',
        })

    def _queued_action(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'wapio.message',
                'res_id': self.queued_message_id.id, 'view_mode': 'form', 'target': 'current'}
