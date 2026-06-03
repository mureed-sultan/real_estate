from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = "crm.lead"

    realestate_customer_name = fields.Char(string="Customer Name", index="trigram", tracking=True)
    mobile_number = fields.Char(string="Mobile Number", tracking=True)
    whatsapp_number = fields.Char(string="WhatsApp Number", tracking=True)
    property_type = fields.Selection([
        ("apartment", "Apartment"),
        ("villa", "Villa"),
        ("house", "House"),
        ("plot", "Plot"),
        ("commercial", "Commercial"),
        ("office", "Office"),
        ("shop", "Shop"),
        ("warehouse", "Warehouse"),
        ("other", "Other"),
    ], string="Property Type", tracking=True)
    property_location = fields.Char(string="Property Location", index="trigram", tracking=True)
    budget = fields.Monetary(string="Budget", currency_field="company_currency", tracking=True)
    realestate_notes = fields.Text(string="Notes")
    assigned_agent_id = fields.Many2one(
        "res.users",
        string="Assigned Agent",
        related="user_id",
        readonly=False,
        store=True,
        domain="[('share', '=', False)]",
    )
    realestate_call_ids = fields.One2many("realestate.call", "lead_id", string="Calls")
    realestate_call_count = fields.Integer(string="Calls", compute="_compute_realestate_call_stats")
    realestate_total_call_duration = fields.Integer(string="Total Talk Time", compute="_compute_realestate_call_stats")
    realestate_total_call_duration_display = fields.Char(string="Total Talk Time", compute="_compute_realestate_call_stats")
    realestate_followup_count = fields.Integer(string="Follow-ups", compute="_compute_realestate_followup_count")
    realestate_commission_ids = fields.One2many("agent.commission", "lead_id", string="Commissions")
    realestate_commission_count = fields.Integer(string="Commissions", compute="_compute_realestate_commission_count")
    ai_lead_status = fields.Selection([
        ("hot", "Hot"),
        ("not_interested", "Not Interested"),
        ("unknown", "Unknown"),
    ], string="AI Lead Status", default="unknown", tracking=True)
    ai_reason = fields.Text(string="AI Reason", tracking=True)
    ai_last_call_id = fields.Many2one("realestate.call", string="Last AI Call", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("assigned_agent_id") and not vals.get("user_id"):
                vals["user_id"] = vals["assigned_agent_id"]
            if not vals.get("user_id"):
                vals["user_id"] = self.env.uid
            if vals.get("realestate_customer_name"):
                vals.setdefault("contact_name", vals["realestate_customer_name"])
                vals.setdefault("name", vals["realestate_customer_name"])
            if vals.get("mobile_number"):
                vals.setdefault("phone", vals["mobile_number"])
            if vals.get("budget") and not vals.get("expected_revenue"):
                vals["expected_revenue"] = vals["budget"]
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if vals.get("assigned_agent_id") and "user_id" not in vals:
            vals["user_id"] = vals["assigned_agent_id"]
        if vals.get("realestate_customer_name"):
            vals.setdefault("contact_name", vals["realestate_customer_name"])
            vals.setdefault("name", vals["realestate_customer_name"])
        if vals.get("mobile_number") and "phone" not in vals:
            vals["phone"] = vals["mobile_number"]
        if vals.get("budget") and "expected_revenue" not in vals:
            vals["expected_revenue"] = vals["budget"]
        return super().write(vals)

    @api.onchange("realestate_customer_name")
    def _onchange_realestate_customer_name(self):
        for lead in self:
            if lead.realestate_customer_name:
                lead.contact_name = lead.realestate_customer_name
                if not lead.name or lead.name == _("New"):
                    lead.name = lead.realestate_customer_name

    @api.onchange("mobile_number")
    def _onchange_mobile_number(self):
        for lead in self:
            if lead.mobile_number and not lead.phone:
                lead.phone = lead.mobile_number

    @api.onchange("budget")
    def _onchange_budget(self):
        for lead in self:
            if lead.budget and not lead.expected_revenue:
                lead.expected_revenue = lead.budget

    def _format_seconds(self, seconds):
        seconds = int(seconds or 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return "%02d:%02d:%02d" % (hours, minutes, seconds)

    def _compute_realestate_call_stats(self):
        data = self.env["realestate.call"].read_group(
            [("lead_id", "in", self.ids)],
            ["lead_id", "duration:sum"],
            ["lead_id"],
        )
        stats = {
            row["lead_id"][0]: {
                "count": row["lead_id_count"],
                "duration": row.get("duration") or 0,
            }
            for row in data
            if row.get("lead_id")
        }
        for lead in self:
            lead_stats = stats.get(lead.id, {})
            duration = lead_stats.get("duration", 0)
            lead.realestate_call_count = lead_stats.get("count", 0)
            lead.realestate_total_call_duration = duration
            lead.realestate_total_call_duration_display = self._format_seconds(duration)

    def _compute_realestate_followup_count(self):
        for lead in self:
            lead.realestate_followup_count = len(lead.activity_ids)

    def _compute_realestate_commission_count(self):
        data = self.env["agent.commission"].read_group(
            [("lead_id", "in", self.ids)],
            ["lead_id"],
            ["lead_id"],
        )
        counts = {row["lead_id"][0]: row["lead_id_count"] for row in data if row.get("lead_id")}
        for lead in self:
            lead.realestate_commission_count = counts.get(lead.id, 0)

    def _realestate_customer_number(self):
        self.ensure_one()
        return self.mobile_number or self.phone

    def action_realestate_call_customer(self):
        self.ensure_one()
        customer_number = self._realestate_customer_number()
        if not customer_number:
            raise UserError(_("Please set a mobile number before starting a call."))

        call = self.env["realestate.call"].create({
            "lead_id": self.id,
            "customer_name": self.realestate_customer_name or self.contact_name or self.partner_name or self.name,
            "customer_number": customer_number,
            "agent_id": self.user_id.id or self.env.uid,
            "status": "queued",
        })
        self.env["realestate.asterisk.service"].originate_call(call)
        return {
            "type": "ir.actions.act_window",
            "name": _("Call"),
            "res_model": "realestate.call",
            "res_id": call.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_realestate_calls(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Call History"),
            "res_model": "realestate.call",
            "view_mode": "list,form",
            "domain": [("lead_id", "=", self.id)],
            "context": {"default_lead_id": self.id},
        }

    def action_view_realestate_followups(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Follow-up Tasks"),
            "res_model": "mail.activity",
            "view_mode": "list,form",
            "domain": [("res_model", "=", "crm.lead"), ("res_id", "=", self.id)],
            "context": {
                "default_res_model": "crm.lead",
                "default_res_id": self.id,
                "default_user_id": self.user_id.id or self.env.uid,
            },
        }

    def action_view_realestate_commissions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Commissions"),
            "res_model": "agent.commission",
            "view_mode": "list,form",
            "domain": [("lead_id", "=", self.id)],
            "context": {"default_lead_id": self.id, "default_agent_id": self.user_id.id},
        }
