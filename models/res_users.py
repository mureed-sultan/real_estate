import re
from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    asterisk_extension = fields.Char(string="Asterisk Extension")
    asterisk_caller_id = fields.Char(string="Asterisk Caller ID")
    twilio_phone_number = fields.Char(
        string="Twilio Phone Number",
        help="Phone number in E.164 format (e.g., +12125552368). Required for making Twilio calls."
    )
    realestate_commission_percent = fields.Float(string="Real Estate Commission %", default=1.0)

    def write(self, vals):
        """Validate Twilio phone number format when saving"""
        if "twilio_phone_number" in vals and vals["twilio_phone_number"]:
            phone = vals["twilio_phone_number"].strip()
            # E.164 format: + followed by 1-15 digits
            if not re.match(r"^\+[1-9]\d{1,14}$", phone):
                raise ValidationError(
                    _("Invalid Twilio phone number format. Please use E.164 format: +[country code][number]\n"
                      "Example: +12125552368")
                )
        return super().write(vals)
