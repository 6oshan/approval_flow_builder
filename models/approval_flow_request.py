from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError
from markupsafe import Markup
from odoo.tools.misc import format_amount


class ApprovalFlowRequest(models.Model):
    _name = 'approval.flow.request'
    _description = 'Live Approval Request for a Submitted Purchase Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    # Users only have read access to requests, so allow chatter posts with read access.
    _mail_post_access = 'read'

    # Readable record name (breadcrumbs, activities, notifications) instead of "approval.flow.request,12".
    name = fields.Char(
        string='Reference',
        related='purchase_order_id.name',
        store=True,
    )

    purchase_order_id = fields.Many2one(
        'purchase.order',
        string='Purchase Order',
        required=True,
        ondelete='cascade'
    )

    flow_id = fields.Many2one(
        'approval.flow',
        string='Approval Flow',
        required=True
    )

    current_step_id = fields.Many2one(
        'approval.flow.step',
        string='Current Step',
        tracking=True,
    )

    state = fields.Selection([
        ('in_progress', 'In Progress'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Withdrawn'),
    ], string='Status', default='in_progress', required=True, tracking=True)

    log_ids = fields.One2many(
        'approval.flow.log',
        'request_id',
        string='Decision Log'
    )

    # ---- Display fields for the UI (read-only, computed as sudo) ----
    partner_id = fields.Many2one(
        related='purchase_order_id.partner_id', string='Vendor')
    amount_total = fields.Monetary(
        related='purchase_order_id.amount_total', string='Amount',
        currency_field='currency_id')
    currency_id = fields.Many2one(
        related='purchase_order_id.currency_id', string='Currency')
    requester_id = fields.Many2one(
        'res.users', related='create_uid', string='Requested By')
    waiting_since = fields.Datetime(
        string='Waiting Since', compute='_compute_waiting_since',
        help='When the request reached its current step.')
    can_approve = fields.Boolean(
        string='Can Approve', compute='_compute_can_approve',
        help='True when the current user is the approver of the current step.')
    approved_amount = fields.Monetary(
        string='Approved Amount', currency_field='currency_id', readonly=True, copy=False,
        help='Purchase Order total at the moment it was fully approved. A confirmed order '
             'cannot be increased above this amount without a new approval.')
    waiting_for_me = fields.Boolean(
        string='Waiting for Me', compute='_compute_waiting_for_me', search='_search_waiting_for_me',
        help='In progress and the current user can approve the current step.')
    can_withdraw = fields.Boolean(
        string='Can Withdraw', compute='_compute_can_withdraw',
        help='The requester (or an Approval Flow Administrator) can withdraw a pending request.')

    @api.depends('log_ids.create_date', 'create_date')
    def _compute_waiting_since(self):
        for request in self:
            dates = request.sudo().log_ids.mapped('create_date')
            request.waiting_since = max(dates) if dates else request.create_date

    @api.depends_context('uid')
    @api.depends('state', 'current_step_id')
    def _compute_can_approve(self):
        for request in self:
            request.can_approve = (
                request.state == 'in_progress' and request._current_user_can_act()
            )

    @api.depends_context('uid')
    @api.depends('state', 'current_step_id')
    def _compute_waiting_for_me(self):
        for request in self:
            request.waiting_for_me = request.state == 'in_progress' and request._current_user_can_act()

    def _search_waiting_for_me(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return NotImplemented
        pending = self.sudo().search([('state', '=', 'in_progress')])
        mine = pending.with_env(self.env).filtered(lambda r: r._current_user_can_act())
        positive = (operator == '=') == value
        return [('id', 'in' if positive else 'not in', mine.ids)]

    @api.depends_context('uid')
    @api.depends('state', 'create_uid')
    def _compute_can_withdraw(self):
        is_admin = self.env.user.has_group('approval_flow_builder.group_approval_flow_administrator')
        for request in self:
            request.can_withdraw = request.state == 'in_progress' and (
                is_admin or request.create_uid == self.env.user
            )

    @api.model_create_multi
    def create(self, vals_list):
        """Allow only a clean request for a Purchase Order the user can edit."""
        protected_fields = {'state', 'current_step_id', 'log_ids'}
        for vals in vals_list:
            if protected_fields.intersection(vals):
                raise AccessError(
                    'Approval request workflow fields are managed by the approval process.'
                )
            purchase_order_id = vals.get('purchase_order_id')
            flow_id = vals.get('flow_id')
            if not purchase_order_id or not flow_id:
                raise AccessError('Approval requests must be created from a Purchase Order.')
            purchase_order = self.env['purchase.order'].browse(purchase_order_id).exists()
            if not purchase_order:
                raise AccessError('The Purchase Order does not exist.')
            purchase_order.check_access('write')
            if purchase_order.approval_flow_id.id != flow_id:
                raise AccessError('The selected approval flow must match the Purchase Order.')
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(
                'Approval requests can only be changed through approval actions.'
            )
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError('Approval requests cannot be deleted.')
        return super().unlink()

    def _applicable_steps(self):
        """Steps that actually apply to this request's PO amount, in order.
        A step's amount_threshold means: this approver is only required
        if the PO total exceeds that amount. A threshold of 0 always applies.
        The PO total is converted to the step's currency before comparing."""
        self.ensure_one()
        request = self.sudo()
        po = request.purchase_order_id
        date = (po.date_order or fields.Datetime.now()).date()
        steps = request.flow_id.step_ids.sorted(lambda s: (s.sequence, s.id))

        def applies(step):
            threshold = step.amount_threshold or 0.0
            if not threshold:
                return True
            amount = po.amount_total
            if step.currency_id and po.currency_id and step.currency_id != po.currency_id:
                amount = po.currency_id._convert(amount, step.currency_id, po.company_id, date)
            return amount > threshold

        return steps.filtered(applies)

    def _next_step(self):
        """The applicable step that comes after the current one (or empty)."""
        self.ensure_one()
        steps = self._applicable_steps()
        current = self.sudo().current_step_id
        if current in steps:
            remaining = steps[list(steps).index(current) + 1:]
        else:
            # The current step no longer applies (e.g. the flow was edited):
            # continue with the steps ordered after it instead of starting over.
            key = (current.sequence, current.id)
            remaining = steps.filtered(lambda s: (s.sequence, s.id) > key)
        return remaining[:1]

    def action_start(self):
        """Move to the first applicable step, or auto-approve if none apply."""
        for request in self:
            if request.state != 'in_progress' or request.current_step_id:
                raise UserError('This approval request has already been started.')
            steps = request._applicable_steps()
            self.env['approval.flow.log'].sudo().create({
                'request_id': request.id,
                'user_id': request.sudo().create_uid.id or self.env.user.id,
                'decision': 'submitted',
                'note': False if steps else 'No approver applies at this amount: approved automatically.',
            })
            if not steps:
                request.sudo().write({
                    'state': 'approved',
                    'current_step_id': False,
                    'approved_amount': request.sudo().purchase_order_id.amount_total,
                })
            else:
                request.sudo().write({
                    'current_step_id': steps[0].id,
                    'state': 'in_progress',
                })
                request._notify_current_approvers()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _get_step_approver_users(self, step):
        """Users who can act on a step: the employee's user, or the group's members."""
        step = step.sudo()
        if step.employee_id:
            return step.employee_id.user_id
        if step.group_id:
            # all_user_ids also includes users who get the group through implied groups.
            return step.group_id.all_user_ids.filtered(lambda u: not u.share and u.active)
        return self.env['res.users']

    @api.model
    def _self_approval_allowed(self):
        value = self.env['ir.config_parameter'].sudo().get_param('approval_flow_builder.allow_self_approval')
        return value in ('True', 'true', '1')

    def _eligible_approvers(self, step=None):
        """Users who may decide the given (default: current) step of this request.

        Without "Allow self-approval", the person who submitted the request is
        removed. If that leaves nobody (the requester is the step's only
        approver), the Approval Flow Administrators decide instead. If the
        requester is also the only administrator, they keep the step so the
        request can never get stuck."""
        self.ensure_one()
        step = (step or self.sudo().current_step_id).sudo()
        users = self._get_step_approver_users(step)
        if not step or self._self_approval_allowed():
            return users
        requester = self.sudo().create_uid
        others = users - requester
        if others:
            return others
        admins = self.env.ref('approval_flow_builder.group_approval_flow_administrator').sudo().all_user_ids
        admins = admins.filtered(lambda u: not u.share and u.active) - requester
        return admins or users

    def _notify_current_approvers(self):
        """Create a To-Do activity for each approver of the current step.
        It appears in their Activities (clock) menu with a counter, and
        Odoo also sends them an inbox/email notification (unless email
        notifications are disabled in the Configuration screen)."""
        notify = self._email_notifications_enabled()
        for request in self.sudo().with_context(mail_activity_quick_update=not notify):
            step = request.current_step_id
            if request.state != 'in_progress' or not step:
                continue
            po = request.purchase_order_id
            amount = format_amount(self.env, po.amount_total, po.currency_id)
            for user in request._eligible_approvers(step):
                request.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary='Approve %s (%s)' % (po.name, amount),
                    note='Purchase Order %s for %s is waiting for your approval.' % (po.name, amount),
                    user_id=user.id,
                )

    def _close_approval_activities(self, feedback):
        """Mark the pending approval activities of these requests as done."""
        activities = self.sudo().activity_ids
        if activities:
            activities.action_feedback(feedback=feedback)

    @api.model
    def _email_notifications_enabled(self):
        value = self.env['ir.config_parameter'].sudo().get_param('approval_flow_builder.email_notify')
        return value in ('True', 'true', '1')

    def _notify_requester(self, body):
        """Post on the Purchase Order chatter and notify the person who submitted it.
        With email notifications disabled, the message is logged as an internal note."""
        notify = self._email_notifications_enabled()
        for request in self.sudo():
            requester = request.create_uid
            if notify:
                request.purchase_order_id.message_post(
                    body=body,
                    partner_ids=requester.partner_id.ids if requester else [],
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )
            else:
                request.purchase_order_id.message_post(
                    body=body, message_type='comment', subtype_xmlid='mail.mt_note',
                )

    def _current_user_can_act(self):
        self.ensure_one()
        if not self.sudo().current_step_id:
            return False
        return self.env.user in self._eligible_approvers()

    def action_approve(self):
        messages = []
        for request in self:
            if request.state != 'in_progress':
                raise UserError('This request is not awaiting approval.')
            if not request._current_user_can_act():
                raise UserError('You are not the approver for the current step.')

            self.env['approval.flow.log'].sudo().create({
                'request_id': request.id,
                'step_id': request.current_step_id.id,
                'user_id': self.env.user.id,
                'decision': 'approved',
            })
            request._close_approval_activities('Approved by %s' % self.env.user.name)

            next_step = request._next_step()
            po_name = request.sudo().purchase_order_id.display_name
            if next_step:
                request.sudo().write({
                    'current_step_id': next_step.id,
                })
                request._notify_current_approvers()
                messages.append(
                    '%s approved. Sent to next approver: %s.' % (po_name, next_step.display_name)
                )
            else:
                request.sudo().write({
                    'state': 'approved',
                    'current_step_id': False,
                    'approved_amount': request.sudo().purchase_order_id.amount_total,
                })
                request._notify_requester('%s has been fully approved.' % po_name)
                messages.append('%s is fully approved.' % po_name)

        # Success popup (toast) shown after the approval is saved.
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Approved',
                'message': ' '.join(messages),
                'type': 'success',
                'sticky': False,
                # Close the toast action so the form reloads and shows the new status.
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_open_reject_wizard(self):
        """Reject button: ask for a reason first."""
        for request in self:
            if request.state != 'in_progress' or not request._current_user_can_act():
                raise UserError('You are not the approver for the current step.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reject Approval Request',
            'res_model': 'approval.flow.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_ids': [(6, 0, self.ids)]},
        }

    def action_reject(self, reason=None):
        reason = (reason or '').strip() or False
        rejected_names = []
        for request in self:
            if request.state != 'in_progress':
                raise UserError('This request is not awaiting approval.')
            if not request._current_user_can_act():
                raise UserError('You are not the approver for the current step.')

            self.env['approval.flow.log'].sudo().create({
                'request_id': request.id,
                'step_id': request.current_step_id.id,
                'user_id': self.env.user.id,
                'decision': 'rejected',
                'note': reason,
            })
            request._close_approval_activities(
                'Rejected by %s%s' % (self.env.user.name, (': %s' % reason) if reason else ''))
            request.sudo().write({'state': 'rejected'})
            # Notify the requester of the rejection (with the reason).
            body = '%s was rejected by %s at step: %s' % (
                request.sudo().purchase_order_id.name,
                self.env.user.name,
                request.current_step_id.display_name or '',
            )
            if reason:
                body = Markup('%s<br/><b>Reason:</b> %s') % (body, reason)
            request._notify_requester(body)
            rejected_names.append(request.sudo().purchase_order_id.display_name)

        # Result popup (toast) shown after the rejection is saved.
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Rejected',
                'message': '%s rejected. The requester has been notified.' % ', '.join(rejected_names),
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_withdraw(self):
        """The requester (or an administrator) withdraws a pending request,
        for example to correct the Purchase Order and submit it again."""
        names = []
        for request in self:
            if request.state != 'in_progress':
                raise UserError('Only requests that are in progress can be withdrawn.')
            if not request.can_withdraw:
                raise UserError('Only the requester or an Approval Flow Administrator can withdraw this request.')
            self.env['approval.flow.log'].sudo().create({
                'request_id': request.id,
                'step_id': request.sudo().current_step_id.id,
                'user_id': self.env.user.id,
                'decision': 'withdrawn',
            })
            request._close_approval_activities('Withdrawn by %s' % self.env.user.name)
            request.sudo().write({'state': 'cancelled'})
            po = request.sudo().purchase_order_id
            po.message_post(
                body='Approval request withdrawn by %s. The order can be edited and submitted again.' % self.env.user.name,
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
            names.append(po.name)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Withdrawn',
                'message': '%s withdrawn. You can edit the order and submit it again.' % ', '.join(names),
                'type': 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
