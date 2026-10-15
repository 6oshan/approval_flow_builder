from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    afb_require_approval = fields.Boolean(
        string='Require Approval',
        config_parameter='approval_flow_builder.require_approval',
        help='Purchase Orders above the minimum amount cannot be confirmed '
             'without a fully approved approval request.',
    )
    afb_min_amount = fields.Float(
        string='Approval Minimum Amount',
        config_parameter='approval_flow_builder.min_amount',
        help='Approval is required when the Purchase Order total is greater than this amount. '
             '0 means every Purchase Order needs approval.',
    )
    afb_default_flow_id = fields.Many2one(
        'approval.flow',
        string='Default Approval Flow',
        config_parameter='approval_flow_builder.default_flow_id',
        help='Used for new Purchase Orders and when a Purchase Order is submitted without a flow.',
    )
    afb_default_currency_id = fields.Many2one(
        'res.currency',
        string='Default Currency for Amount Conditions',
        config_parameter='approval_flow_builder.default_currency_id',
        help='Currency used for new amount conditions in the flow builder. '
             'Defaults to the company currency.',
    )
    afb_email_notify = fields.Boolean(
        string='Email Notifications',
        config_parameter='approval_flow_builder.email_notify',
        help='Notify approvers when a request reaches them, and requesters when it is '
             'approved or rejected (inbox / email, depending on each user\'s preference). '
             'In-app activities are always created.',
    )
    afb_allow_self_approval = fields.Boolean(
        string='Allow Self-Approval',
        config_parameter='approval_flow_builder.allow_self_approval',
        help='When off, the person who submitted a Purchase Order cannot approve it. '
             'If they are the only approver of a step, the Approval Flow Administrators decide instead.',
    )
