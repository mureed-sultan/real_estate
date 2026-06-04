from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    asterisk_extension = fields.Char(string="Asterisk Extension")
    asterisk_caller_id = fields.Char(string="Asterisk Caller ID")
    twilio_phone_number = fields.Char(string="Twilio Phone Number", help="Phone number in E.164 format (e.g., +12125552368)")
    realestate_commission_percent = fields.Float(string="Real Estate Commission %", default=1.0)
