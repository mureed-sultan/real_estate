from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    asterisk_extension = fields.Char(string="Asterisk Extension")
    asterisk_caller_id = fields.Char(string="Asterisk Caller ID")
    realestate_commission_percent = fields.Float(string="Real Estate Commission %", default=1.0)
