from odoo import api, models, fields
from odoo.exceptions import UserError, ValidationError


class ApprovalFlowStep(models.Model):
    _name = 'approval.flow.step'
    _description = 'Approval Flow Step'
    _order = 'sequence, id'

    @api.depends('employee_id.name', 'group_id.name', 'sequence')
    def _compute_display_name(self):
        for step in self:
            step.display_name = (
                step.employee_id.name
                or step.group_id.name
                or 'Step %s' % step.sequence
            )

    flow_id = fields.Many2one(
        'approval.flow',
        string='Approval Flow',
        required=True,
        ondelete='cascade'
    )

    sequence = fields.Integer(
        string='Sequence',
        required=True,
        default=1
    )

    employee_id = fields.Many2one(
        'hr.employee',
        string='Approver Employee'
    )

    group_id = fields.Many2one(
        'res.groups',
        string='Approver Group'
    )

    amount_threshold = fields.Monetary(
        string='Amount Greater Than'
    )

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency'
    )

    # Removing a step from a flow archives it, so past decisions in the
    # audit log keep pointing to the step they were made on.
    active = fields.Boolean(
        string='Active',
        default=True
    )

    # flow_id is always set, so this also runs when a step is created without any approver.
    @api.constrains('flow_id', 'employee_id', 'group_id')
    def _check_single_approver(self):
        for step in self:
            if bool(step.employee_id) == bool(step.group_id):
                raise ValidationError(
                    'Each approval step needs exactly one approver: '
                    'either an Employee or a Group.'
                )
            if step.employee_id and not step.employee_id.sudo().user_id:
                raise ValidationError(
                    'Employee "%s" is not linked to a user, so they cannot approve. '
                    'Link the employee to a user first.' % step.employee_id.name
                )

    def unlink(self):
        """Never lose audit history: a step used by past decisions or requests
        is archived instead of deleted. A step that is waiting on someone
        right now cannot be removed at all."""
        Request = self.env['approval.flow.request'].sudo()
        busy = Request.search([('state', '=', 'in_progress'), ('current_step_id', 'in', self.ids)])
        if busy:
            raise UserError(
                'You cannot remove an approver who currently has a pending approval (%s). '
                'Finish, reject or withdraw those requests first.' % ', '.join(busy.mapped('name'))
            )
        used_ids = set(self.env['approval.flow.log'].sudo().search([('step_id', 'in', self.ids)]).step_id.ids)
        used_ids |= set(Request.search([('current_step_id', 'in', self.ids)]).current_step_id.ids)
        used = self.filtered(lambda s: s.id in used_ids)
        if used:
            used.write({'active': False})
        return super(ApprovalFlowStep, self - used).unlink()

