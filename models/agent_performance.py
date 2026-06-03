from odoo import _, fields, models, tools


class RealEstateAgentPerformance(models.Model):
    _name = "realestate.agent.performance"
    _description = "Real Estate Agent Performance"
    _auto = False
    _rec_name = "agent_id"
    _order = "total_calls desc, total_leads desc"

    agent_id = fields.Many2one("res.users", string="Agent", readonly=True)
    total_leads = fields.Integer(string="Total Leads", readonly=True)
    total_calls = fields.Integer(string="Total Calls", readonly=True)
    answered_calls = fields.Integer(string="Answered Calls", readonly=True)
    missed_calls = fields.Integer(string="Missed Calls", readonly=True)
    total_talk_time = fields.Integer(string="Total Talk Time", readonly=True)
    total_talk_time_display = fields.Char(string="Total Talk Time", compute="_compute_total_talk_time_display")
    converted_leads = fields.Integer(string="Converted Leads", readonly=True)
    commission_earned = fields.Monetary(string="Commission Earned", readonly=True)
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW realestate_agent_performance AS (
                SELECT
                    u.id AS id,
                    u.id AS agent_id,
                    COALESCE(lead_stats.total_leads, 0) AS total_leads,
                    COALESCE(call_stats.total_calls, 0) AS total_calls,
                    COALESCE(call_stats.answered_calls, 0) AS answered_calls,
                    COALESCE(call_stats.missed_calls, 0) AS missed_calls,
                    COALESCE(call_stats.total_talk_time, 0) AS total_talk_time,
                    COALESCE(lead_stats.converted_leads, 0) AS converted_leads,
                    COALESCE(commission_stats.commission_earned, 0.0) AS commission_earned,
                    company.currency_id AS currency_id
                FROM res_users u
                LEFT JOIN res_company company ON company.id = u.company_id
                LEFT JOIN (
                    SELECT
                        user_id AS agent_id,
                        COUNT(*) AS total_leads,
                        COUNT(*) FILTER (WHERE won_status = 'won') AS converted_leads
                    FROM crm_lead
                    WHERE user_id IS NOT NULL
                    GROUP BY user_id
                ) lead_stats ON lead_stats.agent_id = u.id
                LEFT JOIN (
                    SELECT
                        agent_id,
                        COUNT(*) AS total_calls,
                        COUNT(*) FILTER (WHERE status IN ('answered', 'completed')) AS answered_calls,
                        COUNT(*) FILTER (WHERE status = 'missed') AS missed_calls,
                        COALESCE(SUM(duration), 0) AS total_talk_time
                    FROM realestate_call
                    GROUP BY agent_id
                ) call_stats ON call_stats.agent_id = u.id
                LEFT JOIN (
                    SELECT
                        agent_id,
                        COALESCE(SUM(commission_amount), 0.0) AS commission_earned
                    FROM agent_commission
                    GROUP BY agent_id
                ) commission_stats ON commission_stats.agent_id = u.id
                WHERE u.active IS TRUE AND COALESCE(u.share, FALSE) IS FALSE
            )
        """)

    def _format_seconds(self, seconds):
        seconds = int(seconds or 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return "%02d:%02d:%02d" % (hours, minutes, seconds)

    def _compute_total_talk_time_display(self):
        for performance in self:
            performance.total_talk_time_display = self._format_seconds(performance.total_talk_time)

    def action_open_assigned_leads(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Assigned Leads"),
            "res_model": "crm.lead",
            "view_mode": "list,form,kanban,activity",
            "domain": [("user_id", "=", self.agent_id.id)],
            "context": {"default_user_id": self.agent_id.id, "default_assigned_agent_id": self.agent_id.id},
        }

    def action_open_calls(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Calls"),
            "res_model": "realestate.call",
            "view_mode": "list,form,graph,pivot",
            "domain": [("agent_id", "=", self.agent_id.id)],
            "context": {"default_agent_id": self.agent_id.id},
        }

    def action_open_followups(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Follow-up Tasks"),
            "res_model": "mail.activity",
            "view_mode": "list,form",
            "domain": [("res_model", "=", "crm.lead"), ("user_id", "=", self.agent_id.id)],
            "context": {"default_res_model": "crm.lead", "default_user_id": self.agent_id.id},
        }

    def action_open_commissions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Commissions"),
            "res_model": "agent.commission",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.agent_id.id)],
            "context": {"default_agent_id": self.agent_id.id},
        }
