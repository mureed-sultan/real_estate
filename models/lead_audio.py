from odoo import _, api, fields, models


class LeadAudioAnalysis(models.Model):
    _name = "lead.audio.analysis"
    _description = "AI Analysis per Audio File"
    _rec_name = "attachment_id"

    lead_id = fields.Many2one("crm.lead", string="Lead", required=True, ondelete="cascade")
    attachment_id = fields.Many2one("ir.attachment", string="Audio File", required=True, ondelete="cascade")
    ai_lead_status = fields.Selection([
        ("hot", "Hot"),
        ("not_interested", "Not Interested"),
        ("unknown", "Unknown"),
    ], string="AI Lead Status", default="unknown")
    ai_reason = fields.Text(string="AI Reason")
    ai_transcript = fields.Text(string="AI Transcript")
    ai_last_call_id = fields.Many2one("realestate.call", string="Last AI Call", readonly=True)
    create_date = fields.Datetime(string="Analyzed On", default=fields.Datetime.now)