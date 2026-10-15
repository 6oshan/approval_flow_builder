from odoo import api, models, fields
from odoo.exceptions import AccessError


class ApprovalFlowLog(models.Model):
    _name = 'approval.flow.log'
    _description = 'Approval Decision Log'
    _order = 'create_date desc'

    request_id = fields.Many2one(
        'approval.flow.request',
        string='Approval Request',
        required=True,
        ondelete='cascade'
    )

    step_id = fields.Many2one(
        'approval.flow.step',
        string='Step'
    )

    user_id = fields.Many2one(
        'res.users',
        string='Decided By',
        required=True
    )

    decision = fields.Selection([
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('withdrawn', 'Withdrawn'),
    ], string='Decision', required=True)

    note = fields.Text(
        string='Reason / Note',
        help='Reason given when rejecting, or extra information about the entry.')

    # Audit Log screen (SRS FR-12): show which PO / flow each decision belongs to.
    purchase_order_id = fields.Many2one(
        related='request_id.purchase_order_id', string='Purchase Order', store=True)
    flow_id = fields.Many2one(
        related='request_id.flow_id', string='Approval Flow', store=True)

    # create_date (built into every Odoo model) already gives us the timestamp.

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError('Approval logs can only be created by approval actions.')
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError('Approval logs are immutable.')

    def unlink(self):
        raise AccessError('Approval logs cannot be deleted.')
