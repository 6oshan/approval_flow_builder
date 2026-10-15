from odoo import models, fields, api
from odoo.tools import float_compare
from odoo.exceptions import UserError

# Changing these on a Purchase Order changes what was (or is being) approved.
APPROVAL_LOCKED_FIELDS = {'order_line', 'partner_id', 'currency_id', 'company_id', 'approval_flow_id'}


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _default_approval_flow(self):
        flow_id = self.env['ir.config_parameter'].sudo().get_param('approval_flow_builder.default_flow_id')
        if flow_id and flow_id.isdigit():
            flow = self.env['approval.flow'].sudo().browse(int(flow_id)).exists()
            if flow.active:
                return flow.id
        return False

    approval_flow_id = fields.Many2one(
        'approval.flow',
        string='Approval Flow',
        domain=[('active', '=', True)],
        copy=False,
        default=_default_approval_flow,
    )

    approval_request_ids = fields.One2many(
        'approval.flow.request',
        'purchase_order_id',
        string='Approval Requests'
    )

    approval_request_count = fields.Integer(
        string='Approvals',
        compute='_compute_approval_request_count'
    )

    approval_needed = fields.Boolean(
        string='Approval Needed',
        compute='_compute_approval_needed',
        help='Whether this order must be approved before it can be confirmed '
             '(minimum amount and flow rules from Configuration).',
    )

    approval_state = fields.Selection([
        ('none', 'No Approval Needed'),
        ('in_progress', 'Approval In Progress'),
        ('approved', 'Approved'),
        ('rejected', 'Approval Rejected'),
    ], string='Approval Status', compute='_compute_approval_state', store=True)

    @api.depends('approval_request_ids.state')
    def _compute_approval_state(self):
        for po in self:
            latest = po.approval_request_ids.sorted('id', reverse=True)[:1]
            if not latest or latest.state == 'cancelled':
                po.approval_state = 'none'
            else:
                po.approval_state = latest.state

    @api.depends('amount_total', 'approval_flow_id', 'currency_id', 'company_id', 'date_order')
    def _compute_approval_needed(self):
        for po in self:
            po.approval_needed = po._approval_required()

    def _compute_approval_request_count(self):
        for po in self:
            po.approval_request_count = len(po.sudo().approval_request_ids)

    # ------------------------------------------------------------------
    # Approval policy
    # ------------------------------------------------------------------

    def _approval_policy(self):
        """(require approval above a minimum?, minimum amount) from Configuration."""
        ICP = self.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('approval_flow_builder.require_approval') in ('True', 'true', '1')
        try:
            min_amount = float(ICP.get_param('approval_flow_builder.min_amount') or 0.0)
        except ValueError:
            min_amount = 0.0
        return enabled, min_amount

    def _amount_in_company_currency(self):
        self.ensure_one()
        if self.currency_id and self.company_id.currency_id and self.company_id.currency_id != self.currency_id:
            return self.currency_id._convert(
                self.amount_total, self.company_id.currency_id, self.company_id,
                (self.date_order or fields.Datetime.now()).date(),
            )
        return self.amount_total

    def _approval_required(self):
        """Does this order need an approved request before it can be confirmed?

        * "Require approval" ON in Configuration: the minimum amount decides.
          Above the minimum -> approval needed. At or below it -> no approval,
          even if the default flow is pre-filled on the order. Only a flow that
          was picked by hand (different from the default) still forces approval.
        * "Require approval" OFF: approval is needed when a flow is selected.
        """
        self.ensure_one()
        enabled, min_amount = self._approval_policy()
        if not enabled:
            return bool(self.approval_flow_id)
        if self._amount_in_company_currency() > min_amount:
            return True
        return bool(self.approval_flow_id) and self.approval_flow_id.id != self._default_approval_flow()

    def _latest_approval_request(self):
        """Latest request, read as superuser so record rules can never hide it."""
        self.ensure_one()
        return self.sudo().approval_request_ids.sorted('id', reverse=True)[:1]

    # ------------------------------------------------------------------
    # Lock the order while it is being approved / after approval
    # ------------------------------------------------------------------

    def write(self, vals):
        locked = APPROVAL_LOCKED_FIELDS.intersection(vals) and not self.env.context.get('afb_skip_lock')
        reset_candidates = self.browse()
        old_amounts = {}
        old_totals = {po.id: po.amount_total for po in self} if locked else {}
        if locked:
            for po in self:
                if po.state not in ('draft', 'sent'):
                    continue
                if po.approval_state == 'in_progress':
                    raise UserError(
                        'Purchase Order %s is waiting for approval and cannot be changed. '
                        'Withdraw the approval request first (Approvals button), '
                        'edit the order, then submit it again.' % po.name
                    )
                if po.approval_state == 'approved':
                    reset_candidates |= po
                    old_amounts[po.id] = (po.amount_total, po.partner_id.id, po.currency_id.id)
        res = super().write(vals)
        if locked:
            self._check_approved_amount(old_totals)
        # An approved order that changes must be approved again.
        for po in reset_candidates:
            if (po.amount_total, po.partner_id.id, po.currency_id.id) != old_amounts[po.id]:
                request = po._latest_approval_request()
                if request.state == 'approved':
                    request.write({'state': 'cancelled'})
                    po.sudo().message_post(
                        body='The order was changed after approval, so the approval was reset. '
                             'Please submit it for approval again.',
                        message_type='comment', subtype_xmlid='mail.mt_note',
                    )
        return res

    def _check_approved_amount(self, old_totals=None):
        """A confirmed order cannot grow above what was approved.

        * Approved order: the total cannot go above the approved amount.
        * Order confirmed without approval (it was at or below the minimum):
          it cannot grow into an amount that would need approval.
        Reducing an order is always allowed."""
        old_totals = old_totals or {}
        for po in self:
            if po.state not in ('purchase', 'done'):
                continue
            rounding = po.currency_id.rounding or 0.01
            old = old_totals.get(po.id)
            if old is not None and float_compare(po.amount_total, old, precision_rounding=rounding) <= 0:
                continue  # not an increase
            request = po._latest_approval_request()
            if request.state == 'approved':
                if not request.approved_amount:
                    continue  # approved before amounts were recorded
                if float_compare(po.amount_total, request.approved_amount, precision_rounding=rounding) > 0:
                    raise UserError(
                        'Purchase Order %s was approved for %s and cannot be increased to %s.\n\n'
                        'To order more, cancel the order, set it back to draft, '
                        'and submit it for approval again.' % (
                            po.name,
                            po.currency_id.format(request.approved_amount),
                            po.currency_id.format(po.amount_total),
                        )
                    )
            elif po._approval_required():
                raise UserError(
                    'Purchase Order %s was confirmed without approval because it did not need one. '
                    'At %s it now needs approval.\n\n'
                    'To order more, cancel the order, set it back to draft, '
                    'and submit it for approval.' % (po.name, po.currency_id.format(po.amount_total))
                )

    def button_draft(self):
        """Setting a cancelled order back to draft always needs a fresh approval."""
        res = super().button_draft()
        for po in self:
            request = po._latest_approval_request()
            if request.state == 'approved':
                request.write({'state': 'cancelled'})
                po.sudo().message_post(
                    body='Order set back to draft: the previous approval was reset. '
                         'Submit it for approval again before confirming.',
                    message_type='comment', subtype_xmlid='mail.mt_note',
                )
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_view_approval_requests(self):
        """Smart button: open this PO's approval requests."""
        self.ensure_one()
        requests = self.sudo().approval_request_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Approvals',
            'res_model': 'approval.flow.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', requests.ids)],
        }
        if len(requests) == 1:
            action.update({'view_mode': 'form', 'res_id': requests.id})
        return action

    def action_open_approval_builder(self):
        """Open the selected approval flow in the visual builder (SRS FR-01)."""
        self.ensure_one()
        if not self.approval_flow_id:
            raise UserError('Select an Approval Flow first.')
        return self.approval_flow_id.action_open_builder()

    def action_submit_for_approval(self):
        messages = []
        for po in self:
            if po.state not in ('draft', 'sent'):
                raise UserError('Only RFQs (draft or sent) can be submitted for approval.')
            # The submitter must be allowed to edit this order.
            po.check_access('write')
            latest = po._latest_approval_request()
            if latest.state == 'in_progress':
                raise UserError('This Purchase Order already has an approval request in progress.')
            if latest.state == 'approved':
                raise UserError('This Purchase Order is already approved.')
            if po._approval_policy()[0] and not po._approval_required():
                raise UserError(
                    '%s does not need approval (its total is not above the approval minimum). '
                    'Click "Confirm Order" to confirm it directly.' % po.name
                )

            if not po.approval_flow_id:
                default_flow = po._default_approval_flow()
                if not default_flow:
                    raise UserError(
                        'Please select an Approval Flow before submitting '
                        'this Purchase Order for approval.'
                    )
                po.approval_flow_id = default_flow

            # Requests are created by the system (users have no create right on them).
            request = self.env['approval.flow.request'].sudo().create({
                'purchase_order_id': po.id,
                'flow_id': po.approval_flow_id.id,
            })
            request.action_start()
            if request.state == 'approved':
                messages.append('%s needs no approval at this amount and is approved.' % po.name)
            else:
                messages.append('%s submitted. Waiting for: %s.' % (
                    po.name, request.current_step_id.display_name))

        # Toast after submitting; closing the action reloads the PO form.
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Submitted for Approval',
                'message': ' '.join(messages),
                'type': 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def button_confirm(self):
        for po in self:
            if po.state not in ('draft', 'sent'):
                continue
            latest = po._latest_approval_request()
            if latest.state == 'in_progress':
                raise UserError(
                    'Purchase Order %s cannot be confirmed until its approval request is fully approved.' % po.name
                )
            if po._approval_required() and latest.state != 'approved':
                if latest.state == 'rejected':
                    raise UserError(
                        'Purchase Order %s was rejected during approval. '
                        'Edit it if needed and submit it for approval again.' % po.name
                    )
                raise UserError(
                    'Purchase Order %s needs approval before it can be confirmed. '
                    'Click "Submit for Approval".' % po.name
                )
        return super().button_confirm()


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # Lines can also be changed directly (not through the order form),
    # so the approved-amount check runs here too.
    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['purchase.order'].browse(
            {vals['order_id'] for vals in vals_list if vals.get('order_id')})
        old_totals = {o.id: o.amount_total for o in orders}
        lines = super().create(vals_list)
        lines.order_id._check_approved_amount(old_totals)
        return lines

    def write(self, vals):
        check = {'product_qty', 'price_unit', 'tax_ids', 'discount', 'product_id'}.intersection(vals)
        old_totals = {o.id: o.amount_total for o in self.order_id} if check else {}
        res = super().write(vals)
        if check:
            self.order_id._check_approved_amount(old_totals)
        return res
