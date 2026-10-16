import base64
import json
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..client import WapioClient, WapioError


class WapioMessage(models.Model):
    _name = 'wapio.message'
    _description = 'Wapio Outgoing Message'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(default=lambda self: str(uuid.uuid4()), required=True, copy=False, readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True)
    owner_id = fields.Many2one('res.users', required=True, readonly=True)
    session_id = fields.Many2one('wapio.session', required=True, readonly=True, check_company=True)
    recipient = fields.Char(required=True, readonly=True)
    payload_json = fields.Text(required=True, readonly=True, groups='base.group_system')
    pdf_content = fields.Binary(attachment=False, readonly=True, groups='base.group_system')
    state = fields.Selection([('preparing', 'Preparing PDF'), ('ready', 'Queued'),
                              ('submitted', 'Submitted'), ('failed', 'Needs attention')], required=True, readonly=True)
    message_id = fields.Char(readonly=True)
    error = fields.Text(readonly=True)
    _key_unique = models.Constraint('UNIQUE(name)', 'Message ID must be unique.')

    def action_retry(self):
        self.check_access('read')
        for message in self:
            if not self.env.user.has_group('wapio_whatsapp.group_wapio_user'):
                raise AccessError('Wapio sender permission is required.')
            if message.state != 'failed':
                raise UserError('Only failed messages can be retried.')
            message.sudo().write({'state': 'preparing' if message.sudo().pdf_content else 'ready', 'error': False})
        return True

    @api.model
    def _cron_dispatch(self):
        # PDF preparation and sending are separate cron transactions: the media
        # handle must be durable before a send can reach the remote API.
        messages = self.search([('state', 'in', ['preparing', 'ready'])], limit=5, order='id')
        for message in messages:
            self.env.cr.execute('SELECT id FROM wapio_message WHERE id=%s FOR UPDATE SKIP LOCKED', [message.id])
            if not self.env.cr.fetchone():
                continue
            message.invalidate_recordset()
            if message.state not in ('preparing', 'ready'):
                continue
            try:
                message._dispatch_one()
            except WapioError as error:
                message.write({'state': 'failed', 'error': str(error)})

    def _dispatch_one(self):
        self.ensure_one()
        client = WapioClient(self.session_id.connection_id.api_key)
        payload = json.loads(self.payload_json)
        if self.state == 'preparing':
            payload['media_handle'] = client.upload_pdf(base64.b64decode(self.pdf_content))
            self.write({'payload_json': json.dumps(payload), 'pdf_content': False, 'state': 'ready'})
            return
        message_id = client.send(payload, 'odoo:' + self.name)
        self.write({'state': 'submitted', 'message_id': message_id, 'error': False})
