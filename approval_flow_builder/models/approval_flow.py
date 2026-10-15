from odoo import api, models, fields
from odoo.exceptions import AccessError, UserError


class ApprovalFlow(models.Model):
    _name = 'approval.flow'
    _description = 'Reusable Purchase Order Approval Flow Template'

    name = fields.Char(
        string='Flow Name',
        required=True
    )

    # NOTE: purchase_order_id removed. A flow is a reusable policy/template,
    # not tied to any single PO. The link to a specific PO now lives on
    # approval.flow.request, which is the live, per-document instance.

    step_ids = fields.One2many(
        'approval.flow.step',
        'flow_id',
        string='Approval Steps'
    )

    active = fields.Boolean(
        string='Active',
        default=True
    )

    step_count = fields.Integer(
        string='Steps',
        compute='_compute_step_count'
    )

    @api.depends('step_ids')
    def _compute_step_count(self):
        for flow in self:
            flow.step_count = len(flow.step_ids)

    # ------------------------------------------------------------------
    # Visual builder (Owl client action: approval_flow_builder.flow_builder)
    # ------------------------------------------------------------------

    def action_open_builder(self):
        """Open the drag-and-drop builder for this flow."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'approval_flow_builder.flow_builder',
            'name': 'Flow Builder: %s' % self.name,
            'params': {'flow_id': self.id},
            'context': {'active_id': self.id},
        }

    def _check_builder_access(self):
        if not self.env.user.has_group('approval_flow_builder.group_approval_flow_administrator'):
            raise AccessError('Only Approval Flow Administrators can edit approval flows.')

    @api.model
    def _builder_currency(self):
        """Currency for amount conditions: Configuration default, else company currency."""
        value = self.env['ir.config_parameter'].sudo().get_param('approval_flow_builder.default_currency_id')
        if value and value.isdigit():
            currency = self.env['res.currency'].sudo().browse(int(value)).exists()
            if currency:
                return currency
        return self.env.company.currency_id

    @api.model
    def builder_load(self, flow_id):
        """Everything the builder needs in one call: the flow, its steps,
        and the employees / groups that can be picked as approvers."""
        self._check_builder_access()
        flow = self.browse(flow_id).exists()
        if not flow:
            raise UserError('This approval flow no longer exists.')
        currency = self._builder_currency()
        steps = []
        for step in flow.step_ids.sorted(lambda s: (s.sequence, s.id)):
            steps.append({
                'id': step.id,
                'type': 'group' if step.group_id else 'employee',
                'employee_id': step.employee_id.id or False,
                'group_id': step.group_id.id or False,
                'name': step.sudo().employee_id.name or step.sudo().group_id.display_name or '',
                'amount': step.amount_threshold or 0.0,
            })
        # Employees without a linked user are returned too, so the builder can
        # show them greyed out ("No user linked") instead of hiding them.
        employees = [
            {'id': e.id, 'name': e.name, 'job_title': e.job_title or '', 'has_user': bool(e.user_id)}
            for e in self.env['hr.employee'].sudo().search([], order='name')
        ]
        Groups = self.env['res.groups'].sudo()
        group_domain = [('share', '=', False)] if 'share' in Groups._fields else []
        groups = Groups.search_read(group_domain, ['display_name'], order='name')
        return {
            'flow': {'id': flow.id, 'name': flow.name, 'active': flow.active},
            'steps': steps,
            'employees': employees,
            'groups': [{'id': g['id'], 'name': g['display_name']} for g in groups],
            'currency': {
                'symbol': currency.symbol,
                'position': currency.position,
            },
            'in_progress_count': self.env['approval.flow.request'].sudo().search_count(
                [('flow_id', '=', flow.id), ('state', '=', 'in_progress')]),
        }

    @api.model
    def builder_save(self, flow_id, flow_name, steps):
        """Save the whole chain in one transaction.
        steps: ordered list of {id|False, type, employee_id, group_id, amount}.
        Steps missing from the list are archived (kept for the audit log)."""
        self._check_builder_access()
        flow = self.browse(flow_id).exists()
        if not flow:
            raise UserError('This approval flow no longer exists.')
        if not steps:
            raise UserError('Add at least one approver before saving.')
        if flow_name and flow_name.strip() and flow_name.strip() != flow.name:
            flow.name = flow_name.strip()

        existing = flow.step_ids
        kept_ids = {s.get('id') for s in steps if s.get('id')}
        unknown = kept_ids - set(existing.ids)
        if unknown:
            raise UserError('Some steps do not belong to this flow. Reload the builder and try again.')

        removed = existing.filtered(lambda st: st.id not in kept_ids)
        if removed:
            busy = self.env['approval.flow.request'].sudo().search([
                ('state', '=', 'in_progress'),
                ('current_step_id', 'in', removed.ids),
            ])
            if busy:
                raise UserError(
                    'You cannot remove an approver who currently has a pending approval (%s). '
                    'Finish or reject those requests first.' % ', '.join(busy.mapped('name'))
                )
            removed.write({'active': False})

        currency = self._builder_currency()
        for index, data in enumerate(steps, start=1):
            is_group = data.get('type') == 'group'
            vals = {
                'sequence': index * 10,
                'employee_id': False if is_group else data.get('employee_id') or False,
                'group_id': (data.get('group_id') or False) if is_group else False,
                'amount_threshold': float(data.get('amount') or 0.0),
            }
            if data.get('id'):
                step = existing.browse(data['id'])
                if not step.currency_id:
                    vals['currency_id'] = currency.id
                step.write(vals)
            else:
                vals.update({'flow_id': flow.id, 'currency_id': currency.id})
                self.env['approval.flow.step'].create(vals)
        return self.builder_load(flow.id)

