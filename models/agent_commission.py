from odoo import api, fields, models


class AgentCommission(models.Model):
    _name = "agent.commission"
    _description = "Agent Commission"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    agent_id = fields.Many2one("res.users", string="Agent", required=True, default=lambda self: self.env.user, index=True, tracking=True)
    lead_id = fields.Many2one("crm.lead", string="Lead", required=True, ondelete="cascade", index=True, tracking=True)
    property_sale_amount = fields.Monetary(string="Property Sale Amount", required=True, tracking=True)
    commission_percent = fields.Float(string="Commission Percent", required=True, tracking=True)
    commission_amount = fields.Monetary(string="Commission Amount", compute="_compute_commission_amount", store=True, tracking=True)
    currency_id = fields.Many2one("res.currency", string="Currency", related="lead_id.company_currency", readonly=True)
    company_id = fields.Many2one("res.company", string="Company", related="lead_id.company_id", store=True, readonly=True)

    @api.depends("property_sale_amount", "commission_percent")
    def _compute_commission_amount(self):
        for commission in self:
            commission.commission_amount = (commission.property_sale_amount or 0.0) * (commission.commission_percent or 0.0) / 100.0

    @api.onchange("lead_id")
    def _onchange_lead_id(self):
        for commission in self:
            if commission.lead_id:
                commission.agent_id = commission.lead_id.user_id
                commission.property_sale_amount = commission.lead_id.expected_revenue or commission.lead_id.budget
                commission.commission_percent = (
                    commission.lead_id.user_id.realestate_commission_percent
                    or float(self.env["ir.config_parameter"].sudo().get_param("real_estate_commission.default_percent", 1.0))
                )

    @api.model_create_multi
    def create(self, vals_list):
        default_percent = float(self.env["ir.config_parameter"].sudo().get_param("real_estate_commission.default_percent", 1.0) or 1.0)
        for vals in vals_list:
            lead = self.env["crm.lead"].browse(vals.get("lead_id")) if vals.get("lead_id") else self.env["crm.lead"]
            if lead:
                vals.setdefault("agent_id", lead.user_id.id)
                vals.setdefault("property_sale_amount", lead.expected_revenue or lead.budget)
            agent = self.env["res.users"].browse(vals.get("agent_id")) if vals.get("agent_id") else self.env.user
            vals.setdefault("commission_percent", agent.realestate_commission_percent or default_percent)
        return super().create(vals_list)
