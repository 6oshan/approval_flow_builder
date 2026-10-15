from odoo import fields, models
from odoo.exceptions import UserError


class ApprovalRejectWizard(models.TransientModel):
    """Asks the approver why a request is rejected (shown when clicking Reject)."""
    _name = 'approval.flow.reject.wizard'
    _description = 'Reject Approval Request'

    request_ids = fields.Many2many('approval.flow.request', string='Approval Requests', required=True)
    reason = fields.Text(string='Reason', required=True)

    def action_confirm(self):
        self.ensure_one()
        if not (self.reason or '').strip():
            raise UserError('Please enter a reason for the rejection.')
        return self.request_ids.action_reject(reason=self.reason)
