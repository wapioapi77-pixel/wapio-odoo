from odoo import fields, models
from odoo.exceptions import UserError

from ..client import WapioClient, WapioError


class WapioConnection(models.Model):
    _name = 'wapio.connection'
    _description = 'Wapio Connection'
    _check_company_auto = True

    name = fields.Char(required=True, default='Wapio')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    api_key = fields.Char(required=True, groups='base.group_system', copy=False)
    session_ids = fields.One2many('wapio.session', 'connection_id')

    def action_refresh_sessions(self):
        self.ensure_one()
        self.check_access('write')
        try:
            remote_sessions = WapioClient(self.api_key).sessions()
        except WapioError as error:
            raise UserError(str(error)) from None
        self.session_ids.write({'status': 'unavailable'})
        for remote in remote_sessions:
            if not remote.get('session_id'):
                continue
            session = self.session_ids.filtered(lambda row: row.remote_id == remote['session_id'])
            values = {'name': remote.get('name') or remote['session_id'],
                      'status': remote.get('status') or 'unknown'}
            if session:
                session.write(values)
            else:
                self.env['wapio.session'].create(dict(values, connection_id=self.id,
                                                     remote_id=remote['session_id']))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'Wapio', 'message': 'Connection verified; sessions refreshed.', 'type': 'success'}}


class WapioSession(models.Model):
    _name = 'wapio.session'
    _description = 'Wapio WhatsApp Session'
    _check_company_auto = True

    name = fields.Char(required=True)
    connection_id = fields.Many2one('wapio.connection', required=True, ondelete='cascade', check_company=True)
    company_id = fields.Many2one(related='connection_id.company_id', store=True)
    remote_id = fields.Char(required=True)
    status = fields.Char(readonly=True)
    _remote_unique = models.Constraint('UNIQUE(connection_id, remote_id)', 'Session already exists on this connection.')
