from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, TransactionCase
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install', 'approval_flow_builder')
class TestApprovalFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        approver = 'base.group_user,purchase.group_purchase_user,approval_flow_builder.group_approval_flow_approver'
        cls.requester = new_test_user(env, 'afb_requester', groups='base.group_user,purchase.group_purchase_user')
        cls.manager = new_test_user(env, 'afb_manager', groups=approver)
        cls.finance = new_test_user(env, 'afb_finance', groups=approver)
        cls.outsider = new_test_user(env, 'afb_outsider', groups=approver)
        cls.buyer_boss = new_test_user(env, 'afb_buyer_boss', groups='base.group_user,purchase.group_purchase_manager')
        cls.flow_admin = new_test_user(
            env, 'afb_flow_admin',
            groups='base.group_user,purchase.group_purchase_user,approval_flow_builder.group_approval_flow_administrator')

        Employee = env['hr.employee']
        cls.emp_manager = Employee.create({'name': 'Manager', 'user_id': cls.manager.id})
        cls.emp_finance = Employee.create({'name': 'Finance Manager', 'user_id': cls.finance.id})
        cls.emp_no_user = Employee.create({'name': 'No Login'})

        # Group step with an implied group: "Finance Lead" implies "Finance Team".
        cls.team = env['res.groups'].create({'name': 'AFB Finance Team'})
        cls.lead_group = env['res.groups'].create({'name': 'AFB Finance Lead', 'implied_ids': [(4, cls.team.id)]})
        cls.lead = new_test_user(env, 'afb_lead', groups=approver)
        cls.lead.group_ids = [(4, cls.lead_group.id)]

        cls.vendor = env['res.partner'].create({'name': 'AFB Vendor'})
        cls.product = env['product.product'].create({'name': 'AFB Desk', 'type': 'consu'})

        cls.flow = env['approval.flow'].create({
            'name': 'Test Flow',
            'step_ids': [
                (0, 0, {'sequence': 10, 'employee_id': cls.emp_manager.id}),
                (0, 0, {'sequence': 20, 'group_id': cls.team.id, 'amount_threshold': 10000}),
            ],
        })
        cls.step_manager, cls.step_team = cls.flow.step_ids.sorted('sequence')

    # ---------------------------------------------------------------- helpers
    def _po(self, amount, flow=None, user=None):
        user = user or self.requester
        return self.env['purchase.order'].with_user(user).create({
            'partner_id': self.vendor.id,
            'approval_flow_id': (flow or self.flow).id if flow is not False else False,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': amount})],
        })

    def _submit(self, po, user=None):
        po.with_user(user or self.requester).action_submit_for_approval()
        return po.sudo().approval_request_ids.sorted('id')[-1]

    def _set_param(self, key, value):
        self.env['ir.config_parameter'].sudo().set_param('approval_flow_builder.%s' % key, value)

    # ------------------------------------------------------------------ tests
    def test_01_sequential_employee_then_implied_group(self):
        po = self._po(50000)
        req = self._submit(po)
        self.assertEqual(req.current_step_id, self.step_manager)
        self.assertEqual(po.approval_state, 'in_progress')
        # Activity for the current approver only.
        self.assertEqual(req.activity_ids.user_id, self.manager)

        req.with_user(self.manager).action_approve()
        self.assertEqual(req.current_step_id, self.step_team)
        # Lead is a team member only through the implied group (all_user_ids).
        self.assertIn(self.lead, req.activity_ids.user_id)
        self.assertNotIn(self.manager, req.activity_ids.user_id)

        req.with_user(self.lead).action_approve()
        self.assertEqual(req.state, 'approved')
        self.assertFalse(req.activity_ids)
        self.assertEqual(po.approval_state, 'approved')
        self.assertEqual(sorted(req.log_ids.mapped('decision')), ['approved', 'approved', 'submitted'])
        self.assertEqual(req.log_ids.mapped('purchase_order_id'), po)  # Audit Log field
        po.with_user(self.requester).button_confirm()
        self.assertEqual(po.state, 'purchase')

    def test_02_past_approver_keeps_read_access(self):
        po = self._po(50000)
        req = self._submit(po)
        req.with_user(self.manager).action_approve()
        req.with_user(self.lead).action_approve()
        # The original access error: Manager must still read the request and full log.
        as_manager = req.with_user(self.manager)
        self.assertEqual(as_manager.state, 'approved')
        self.assertEqual(len(as_manager.log_ids), 3)  # submitted + 2 approvals

    def test_03_unauthorized_user_cannot_act_or_see(self):
        req = self._submit(self._po(50000))
        with self.assertRaises(UserError):
            req.with_user(self.outsider).action_approve()
        with self.assertRaises(UserError):
            req.with_user(self.requester).action_approve()
        found = self.env['approval.flow.request'].with_user(self.outsider).search([('id', '=', req.id)])
        self.assertFalse(found)

    def test_04_reject_stops_and_notifies_requester(self):
        po = self._po(50000)
        req = self._submit(po)
        req.with_user(self.manager).action_reject()
        self.assertEqual(req.state, 'rejected')
        self.assertEqual(po.approval_state, 'rejected')
        self.assertFalse(req.activity_ids)
        msg = po.message_ids.filtered(lambda m: 'rejected' in (m.body or ''))[:1]
        self.assertIn(self.requester.partner_id, msg.partner_ids)
        with self.assertRaises(UserError):
            po.with_user(self.requester).button_confirm()
        # Resubmit after rejection is allowed.
        req2 = self._submit(po)
        self.assertEqual(req2.state, 'in_progress')

    def test_05_confirm_blocked_while_pending_for_any_user(self):
        po = self._po(50000)
        self._submit(po)
        # A purchase manager who is not involved must not bypass the pending approval (C2).
        with self.assertRaises(UserError):
            po.with_user(self.buyer_boss).button_confirm()

    def test_06_policy_requires_approval_above_amount(self):
        self._set_param('require_approval', 'True')
        self._set_param('min_amount', '1000')
        big = self._po(5000, flow=False)
        with self.assertRaises(UserError):
            big.with_user(self.requester).button_confirm()
        small = self._po(500, flow=False)
        small.with_user(self.requester).button_confirm()
        self.assertEqual(small.state, 'purchase')

    def test_07_flow_selected_means_approval_required(self):
        po = self._po(50000)
        with self.assertRaises(UserError):
            po.with_user(self.requester).button_confirm()

    def test_08_order_locked_during_approval_and_reset_after_change(self):
        po = self._po(50000)
        req = self._submit(po)
        line = po.order_line[0]
        with self.assertRaises(UserError):
            po.with_user(self.requester).write({'order_line': [(1, line.id, {'price_unit': 1})]})
        # Withdraw, edit, resubmit.
        req.with_user(self.requester).action_withdraw()
        self.assertEqual(req.state, 'cancelled')
        self.assertEqual(po.approval_state, 'none')
        self.assertFalse(req.activity_ids)
        po.with_user(self.requester).write({'order_line': [(1, line.id, {'price_unit': 60000})]})
        req2 = self._submit(po)
        req2.with_user(self.manager).action_approve()
        req2.with_user(self.lead).action_approve()
        self.assertEqual(po.approval_state, 'approved')
        # Changing the amount after approval resets it; confirming then needs a new approval.
        po.with_user(self.requester).write({'order_line': [(1, line.id, {'price_unit': 90000})]})
        self.assertEqual(req2.state, 'cancelled')
        self.assertEqual(po.approval_state, 'none')
        with self.assertRaises(UserError):
            po.with_user(self.requester).button_confirm()

    def test_09_withdraw_only_by_requester_or_admin(self):
        req = self._submit(self._po(50000))
        with self.assertRaises(UserError):
            req.with_user(self.manager).action_withdraw()
        req.with_user(self.flow_admin).action_withdraw()
        self.assertEqual(req.state, 'cancelled')

    def test_10_requests_cannot_be_created_or_edited_directly(self):
        po = self._po(50000)
        with self.assertRaises(AccessError):
            self.env['approval.flow.request'].with_user(self.requester).create(
                {'purchase_order_id': po.id, 'flow_id': self.flow.id})
        req = self._submit(po)
        with self.assertRaises(AccessError):
            req.with_user(self.manager).write({'state': 'approved'})
        log = req.sudo().log_ids
        self.assertEqual(log.mapped('decision'), ['submitted'])
        req.with_user(self.manager).action_approve()
        with self.assertRaises(AccessError):
            req.sudo().log_ids.write({'decision': 'rejected'})

    def test_11_amount_threshold_skips_step(self):
        po = self._po(5000)  # below the team's 10,000 threshold
        req = self._submit(po)
        req.with_user(self.manager).action_approve()
        self.assertEqual(req.state, 'approved')
        self.assertEqual(len(req.log_ids), 2)  # submitted + 1 approval

    def test_12_next_step_after_flow_edit_does_not_restart(self):
        third = self.env['approval.flow.step'].create({
            'flow_id': self.flow.id, 'sequence': 30, 'employee_id': self.emp_finance.id})
        po = self._po(50000)
        req = self._submit(po)
        req.with_user(self.manager).action_approve()
        self.assertEqual(req.current_step_id, self.step_team)
        # Raise the current step's threshold so it no longer applies: the flow must
        # continue with the next step (Finance), not jump back to step 1.
        self.step_team.amount_threshold = 999999
        req.with_user(self.lead).action_approve()
        self.assertEqual(req.current_step_id, third)

    def test_13_step_constraints_and_archiving(self):
        with self.assertRaises(ValidationError):
            self.env['approval.flow.step'].create({'flow_id': self.flow.id})
        with self.assertRaises(ValidationError):
            self.env['approval.flow.step'].create({'flow_id': self.flow.id, 'employee_id': self.emp_no_user.id})
        req = self._submit(self._po(50000))
        with self.assertRaises(UserError):
            self.step_manager.unlink()  # waiting on Manager right now
        req.with_user(self.manager).action_approve()
        req.with_user(self.lead).action_approve()
        self.step_manager.unlink()
        self.assertTrue(self.step_manager.exists())
        self.assertFalse(self.step_manager.active)
        self.assertEqual(req.log_ids.filtered(lambda l: l.decision == 'approved').sorted('id')[0].step_id, self.step_manager)

    def test_14_builder_load_and_save(self):
        Flow = self.env['approval.flow'].with_user(self.flow_admin)
        data = Flow.builder_load(self.flow.id)
        self.assertEqual([s['id'] for s in data['steps']], [self.step_manager.id, self.step_team.id])
        no_user = [e for e in data['employees'] if e['id'] == self.emp_no_user.id]
        self.assertEqual(no_user[0]['has_user'], False)
        # Reorder, drop the team step, add Finance with a condition.
        data = Flow.builder_save(self.flow.id, 'Renamed Flow', [
            {'id': False, 'type': 'employee', 'employee_id': self.emp_finance.id, 'amount': 2500},
            {'id': self.step_manager.id, 'type': 'employee', 'employee_id': self.emp_manager.id, 'amount': 0},
        ])
        self.assertEqual(self.flow.name, 'Renamed Flow')
        self.assertEqual([s['name'] for s in data['steps']], ['Finance Manager', 'Manager'])
        self.assertEqual(data['steps'][0]['amount'], 2500)
        self.assertFalse(self.step_team.active)
        with self.assertRaises(AccessError):
            self.env['approval.flow'].with_user(self.manager).builder_load(self.flow.id)

    def test_15_default_flow_from_configuration(self):
        # The admin who installs the module is an Approval Flow Administrator,
        # so the app menu is visible straight after installation.
        admin_group = self.env.ref('approval_flow_builder.group_approval_flow_administrator')
        self.assertIn(self.env.ref('base.user_admin'), admin_group.user_ids)
        other = self.env['approval.flow'].create({
            'name': 'Default Flow', 'step_ids': [(0, 0, {'employee_id': self.emp_finance.id})]})
        self._set_param('default_flow_id', str(other.id))
        # Created without choosing a flow: the Configuration default is pre-selected.
        po = self.env['purchase.order'].with_user(self.requester).create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 100})],
        })
        self.assertEqual(po.approval_flow_id, other)

    def test_16_email_toggle_off_logs_note_without_partner(self):
        self._set_param('email_notify', 'False')
        po = self._po(500)
        req = self._submit(po)
        req.with_user(self.manager).action_reject()
        msg = po.message_ids.filtered(lambda m: 'rejected' in (m.body or ''))[:1]
        self.assertFalse(msg.partner_ids)
        self.assertTrue(msg.subtype_id.internal)

    def _approved_and_confirmed(self, amount=50000):
        po = self._po(amount)
        req = self._submit(po)
        req.with_user(self.manager).action_approve()
        req.with_user(self.lead).action_approve()
        po.with_user(self.requester).button_confirm()
        return po, req

    def test_17_confirmed_order_cannot_grow_above_approved_amount(self):
        po, req = self._approved_and_confirmed(50000)
        self.assertEqual(po.state, 'purchase')
        self.assertEqual(req.approved_amount, po.amount_total)
        line = po.order_line[0]
        # Increase through the order form -> blocked.
        with self.assertRaises(UserError):
            po.with_user(self.requester).write({'order_line': [(1, line.id, {'product_qty': 120})]})
        # Increase by editing the line directly -> blocked.
        with self.assertRaises(UserError):
            line.with_user(self.requester).write({'price_unit': 999999})
        # Adding a new line -> blocked.
        with self.assertRaises(UserError):
            po.with_user(self.requester).write({'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10})]})
        # Reducing is allowed.
        po.with_user(self.requester).write({'order_line': [(1, line.id, {'price_unit': 40000})]})
        self.assertEqual(po.approval_state, 'approved')

    def test_18_set_to_draft_resets_approval(self):
        po, req = self._approved_and_confirmed(50000)
        po.with_user(self.buyer_boss).button_cancel()
        po.with_user(self.buyer_boss).button_draft()
        self.assertEqual(po.state, 'draft')
        self.assertEqual(req.state, 'cancelled')
        self.assertEqual(po.approval_state, 'none')
        po.with_user(self.requester).write({'order_line': [(1, po.order_line[0].id, {'price_unit': 90000})]})
        with self.assertRaises(UserError):
            po.with_user(self.requester).button_confirm()
        req2 = self._submit(po)
        self.assertEqual(req2.state, 'in_progress')


    def _po_default_flow(self, amount):
        """PO created without choosing a flow, so the Configuration default is filled in."""
        return self.env['purchase.order'].with_user(self.requester).create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': amount})],
        })

    def test_19_below_minimum_needs_no_approval_even_with_default_flow(self):
        self._set_param('require_approval', 'True')
        self._set_param('min_amount', '10000')
        self._set_param('default_flow_id', str(self.flow.id))
        small = self._po_default_flow(5000)
        self.assertEqual(small.approval_flow_id, self.flow)
        self.assertFalse(small.approval_needed)
        # Submitting an order that needs no approval is refused with a clear message.
        with self.assertRaises(UserError):
            small.with_user(self.requester).action_submit_for_approval()
        small.with_user(self.requester).button_confirm()
        self.assertEqual(small.state, 'purchase')
        big = self._po_default_flow(50000)
        self.assertTrue(big.approval_needed)
        with self.assertRaises(UserError):
            big.with_user(self.requester).button_confirm()
        # A different flow picked by hand still forces approval, whatever the amount.
        other = self.env['approval.flow'].create({
            'name': 'Special', 'step_ids': [(0, 0, {'employee_id': self.emp_finance.id})]})
        special = self._po(5000, flow=other)
        with self.assertRaises(UserError):
            special.with_user(self.requester).button_confirm()

    def test_20_confirmed_without_approval_cannot_grow_above_minimum(self):
        self._set_param('require_approval', 'True')
        self._set_param('min_amount', '10000')
        self._set_param('default_flow_id', str(self.flow.id))
        po = self._po_default_flow(5000)
        po.with_user(self.requester).button_confirm()
        line = po.order_line[0]
        # Still below the minimum (incl. taxes): allowed.
        line.with_user(self.requester).write({'price_unit': 8000})
        # Above the minimum: blocked.
        with self.assertRaises(UserError):
            line.with_user(self.requester).write({'price_unit': 20000})
        # Reducing is allowed.
        line.with_user(self.requester).write({'price_unit': 3000})
        self.assertEqual(line.price_unit, 3000)

    def test_21_self_approval_setting(self):
        # The manager submits a PO whose first step is the manager himself.
        po = self._po(500, user=self.manager)
        req = self._submit(po, user=self.manager)
        self.assertEqual(req.current_step_id, self.step_manager)
        # Blocked by default: the step goes to the Approval Flow Administrators.
        self.assertFalse(req.with_user(self.manager).can_approve)
        with self.assertRaises(UserError):
            req.with_user(self.manager).action_approve()
        self.assertTrue(req.with_user(self.flow_admin).can_approve)
        self.assertIn(req, self.env['approval.flow.request'].with_user(self.flow_admin).search(
            [('waiting_for_me', '=', True)]))
        self.assertNotIn(req, self.env['approval.flow.request'].with_user(self.manager).search(
            [('waiting_for_me', '=', True)]))
        self.assertIn(self.flow_admin, req.sudo().activity_ids.user_id)
        self.assertNotIn(self.manager, req.sudo().activity_ids.user_id)
        req.with_user(self.flow_admin).action_approve()
        self.assertEqual(req.state, 'approved')

        # With "Allow self-approval" on, the manager may approve his own request.
        self._set_param('allow_self_approval', 'True')
        po2 = self._po(500, user=self.manager)
        req2 = self._submit(po2, user=self.manager)
        self.assertTrue(req2.with_user(self.manager).can_approve)
        req2.with_user(self.manager).action_approve()
        self.assertEqual(req2.state, 'approved')

    def test_22_submitted_entry_and_reject_reason(self):
        po = self._po(50000)
        req = self._submit(po)
        submitted = req.sudo().log_ids
        self.assertEqual(submitted.decision, 'submitted')
        self.assertEqual(submitted.user_id, self.requester)
        self.assertFalse(submitted.step_id)

        # The Reject button opens the reason popup; only the current approver may open it.
        with self.assertRaises(UserError):
            req.with_user(self.outsider).action_open_reject_wizard()
        action = req.with_user(self.manager).action_open_reject_wizard()
        self.assertEqual(action['res_model'], 'approval.flow.reject.wizard')
        Wizard = self.env['approval.flow.reject.wizard'].with_user(self.manager).with_context(action['context'])
        with self.assertRaises(UserError):
            Wizard.create({'reason': '   '}).action_confirm()
        Wizard.create({'reason': 'Price too high, get another quote'}).action_confirm()

        self.assertEqual(req.state, 'rejected')
        log = req.sudo().log_ids.filtered(lambda l: l.decision == 'rejected')
        self.assertEqual(log.note, 'Price too high, get another quote')
        msg = po.message_ids.filtered(lambda m: 'rejected' in (m.body or ''))[:1]
        self.assertIn('Price too high', msg.body)
        self.assertIn(self.requester.partner_id, msg.partner_ids)

    def test_23_auto_approved_submission_is_logged(self):
        flow = self.env['approval.flow'].create({
            'name': 'Big only', 'step_ids': [(0, 0, {'employee_id': self.emp_manager.id, 'amount_threshold': 1000000})]})
        req = self._submit(self._po(500, flow=flow))
        self.assertEqual(req.state, 'approved')
        log = req.sudo().log_ids
        self.assertEqual(log.decision, 'submitted')
        self.assertIn('approved automatically', log.note)
