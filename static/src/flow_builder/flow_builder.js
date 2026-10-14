/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

let nextKey = 1;

/**
 * Visual, drag-and-drop builder for a linear approval chain.
 * Opened from the Approval Flow form ("Open Visual Builder").
 */
export class FlowBuilder extends Component {
    static template = "approval_flow_builder.FlowBuilder";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const params = this.props.action?.params || {};
        const context = this.props.action?.context || {};
        this.flowId = params.flow_id || context.active_id || false;

        this.state = useState({
            loading: true,
            saving: false,
            dirty: false,
            flowName: "",
            steps: [],
            employees: [],
            groups: [],
            currency: { symbol: "", position: "before" },
            inProgressCount: 0,
            selectedKey: null,
            search: "",
            dragIndex: null,
            dropIndex: null,
        });

        onWillStart(async () => {
            if (this.flowId) {
                await this.load();
            } else {
                this.state.loading = false;
            }
        });
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    async load() {
        this.state.loading = true;
        const data = await this.orm.call("approval.flow", "builder_load", [this.flowId]);
        this.applyData(data);
        this.state.loading = false;
    }

    applyData(data) {
        this.state.flowName = data.flow.name;
        this.state.employees = data.employees;
        this.state.groups = data.groups;
        this.state.currency = data.currency;
        this.state.inProgressCount = data.in_progress_count;
        this.state.steps = data.steps.map((s) => ({ ...s, key: nextKey++ }));
        this.state.selectedKey = null;
        this.state.search = "";
        this.state.dirty = false;
    }

    get selectedStep() {
        return this.state.steps.find((s) => s.key === this.state.selectedKey) || null;
    }

    get selectedIndex() {
        return this.state.steps.findIndex((s) => s.key === this.state.selectedKey);
    }

    get pickerOptions() {
        const step = this.selectedStep;
        if (!step) {
            return [];
        }
        const term = this.state.search.trim().toLowerCase();
        const source =
            step.type === "group"
                ? this.state.groups.map((g) => ({ id: g.id, name: g.name, sub: "Group", disabled: false }))
                : this.state.employees.map((e) => ({
                      id: e.id,
                      name: e.name,
                      // Employees without a linked user can't approve: shown but disabled.
                      sub: e.has_user === false ? "No user linked" : e.job_title || "Employee",
                      disabled: e.has_user === false,
                  }));
        const filtered = term
            ? source.filter((o) => o.name.toLowerCase().includes(term))
            : source;
        return filtered.slice(0, 40);
    }

    formatAmount(amount) {
        const value = Number(amount || 0).toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        });
        const { symbol, position } = this.state.currency;
        return position === "after" ? `${value} ${symbol}` : `${symbol}${value}`;
    }

    avatarUrl(step) {
        if (step.type === "employee" && step.employee_id) {
            return `/web/image/hr.employee.public/${step.employee_id}/avatar_128`;
        }
        return null;
    }

    initials(name) {
        return (name || "?")
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    markDirty() {
        this.state.dirty = true;
    }

    // ------------------------------------------------------------------
    // Step editing
    // ------------------------------------------------------------------

    addStep() {
        const step = {
            key: nextKey++,
            id: false,
            type: "employee",
            employee_id: false,
            group_id: false,
            name: "",
            amount: 0,
        };
        this.state.steps.push(step);
        this.selectStep(step.key);
        this.markDirty();
    }

    selectStep(key) {
        this.state.selectedKey = this.state.selectedKey === key ? null : key;
        this.state.search = "";
    }

    removeStep(key) {
        const index = this.state.steps.findIndex((s) => s.key === key);
        if (index >= 0) {
            this.state.steps.splice(index, 1);
            if (this.state.selectedKey === key) {
                this.state.selectedKey = null;
            }
            this.markDirty();
        }
    }

    moveStep(key, delta) {
        const index = this.state.steps.findIndex((s) => s.key === key);
        const target = index + delta;
        if (index < 0 || target < 0 || target >= this.state.steps.length) {
            return;
        }
        const [step] = this.state.steps.splice(index, 1);
        this.state.steps.splice(target, 0, step);
        this.markDirty();
    }

    setType(type) {
        const step = this.selectedStep;
        if (!step || step.type === type) {
            return;
        }
        step.type = type;
        step.employee_id = false;
        step.group_id = false;
        step.name = "";
        this.state.search = "";
        this.markDirty();
    }

    pickApprover(option) {
        const step = this.selectedStep;
        if (!step || option.disabled) {
            return;
        }
        if (step.type === "group") {
            step.group_id = option.id;
            step.employee_id = false;
        } else {
            step.employee_id = option.id;
            step.group_id = false;
        }
        step.name = option.name;
        this.state.search = "";
        this.markDirty();
    }

    onAmountInput(ev) {
        const step = this.selectedStep;
        if (!step) {
            return;
        }
        const value = parseFloat(ev.target.value);
        step.amount = Number.isFinite(value) && value > 0 ? value : 0;
        this.markDirty();
    }

    onNameInput(ev) {
        this.state.flowName = ev.target.value;
        this.markDirty();
    }

    // ------------------------------------------------------------------
    // Drag and drop (native HTML5)
    // ------------------------------------------------------------------

    onDragStart(ev, index) {
        this.state.dragIndex = index;
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(index));
    }

    onDragOver(ev, index) {
        if (this.state.dragIndex === null) {
            return;
        }
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        this.state.dropIndex = index;
    }

    onDrop(ev, index) {
        ev.preventDefault();
        const from = this.state.dragIndex;
        if (from !== null && from !== index) {
            const [step] = this.state.steps.splice(from, 1);
            this.state.steps.splice(index, 0, step);
            this.markDirty();
        }
        this.onDragEnd();
    }

    onDragEnd() {
        this.state.dragIndex = null;
        this.state.dropIndex = null;
    }

    // ------------------------------------------------------------------
    // Save / discard / close
    // ------------------------------------------------------------------

    async save() {
        const missing = this.state.steps.findIndex((s) => !s.employee_id && !s.group_id);
        if (!this.state.steps.length) {
            this.notification.add("Add at least one approver before saving.", { type: "warning" });
            return;
        }
        if (missing >= 0) {
            this.state.selectedKey = this.state.steps[missing].key;
            this.notification.add(`Step ${missing + 1} has no approver yet. Pick one in the panel.`, {
                type: "warning",
            });
            return;
        }
        this.state.saving = true;
        try {
            const payload = this.state.steps.map((s) => ({
                id: s.id || false,
                type: s.type,
                employee_id: s.employee_id || false,
                group_id: s.group_id || false,
                amount: s.amount || 0,
            }));
            const data = await this.orm.call("approval.flow", "builder_save", [
                this.flowId,
                this.state.flowName,
                payload,
            ]);
            this.applyData(data);
            this.notification.add("Approval flow saved.", { type: "success" });
        } finally {
            this.state.saving = false;
        }
    }

    async discard() {
        await this.load();
        this.notification.add("Changes discarded.", { type: "info" });
    }

    openFlowForm() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "approval.flow",
            res_id: this.flowId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("approval_flow_builder.flow_builder", FlowBuilder);
