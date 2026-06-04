from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    realestate_ami_host = fields.Char(
        string="AMI Host",
        config_parameter="real_estate_asterisk.ami_host",
    )
    realestate_ami_port = fields.Integer(
        string="AMI Port",
        default=5038,
        config_parameter="real_estate_asterisk.ami_port",
    )
    realestate_ami_username = fields.Char(
        string="AMI Username",
        config_parameter="real_estate_asterisk.ami_username",
    )
    realestate_ami_secret = fields.Char(
        string="AMI Secret",
        config_parameter="real_estate_asterisk.ami_secret",
    )
    realestate_endpoint_template = fields.Char(
        string="Agent Endpoint Template",
        default="PJSIP/{extension}",
        config_parameter="real_estate_asterisk.endpoint_template",
        help="Use {extension} as the placeholder, for example PJSIP/{extension} or SIP/{extension}.",
    )
    realestate_outbound_context = fields.Char(
        string="Outbound Context",
        default="realestate-outbound",
        config_parameter="real_estate_asterisk.outbound_context",
    )
    realestate_originate_timeout = fields.Integer(
        string="Originate Timeout",
        default=30,
        config_parameter="real_estate_asterisk.originate_timeout",
    )
    realestate_default_caller_id = fields.Char(
        string="Default Caller ID",
        config_parameter="real_estate_asterisk.default_caller_id",
    )
    realestate_recording_dir = fields.Char(
        string="Recording Directory",
        config_parameter="real_estate_asterisk.recording_dir",
        help="Absolute directory where Asterisk writes call recordings. Leave empty to use Odoo's data directory.",
    )
    realestate_recording_format = fields.Selection(
        [("wav", "WAV"), ("mp3", "MP3")],
        string="Recording Format",
        default="wav",
        config_parameter="real_estate_asterisk.recording_format",
    )
    realestate_webhook_token = fields.Char(
        string="Webhook Token",
        config_parameter="real_estate_asterisk.webhook_token",
        help="Shared token required by the optional Asterisk event endpoint.",
    )
    realestate_default_commission_percent = fields.Float(
        string="Default Commission %",
        default=1.0,
        config_parameter="real_estate_commission.default_percent",
    )
    realestate_stale_call_minutes = fields.Integer(
        string="Stale Call Minutes",
        default=180,
        config_parameter="real_estate_asterisk.stale_call_minutes",
    )
    realestate_gemini_api_key = fields.Char(
        string="Gemini API Key",
        config_parameter="real_estate_ai.gemini_api_key",
    )
    realestate_gemini_model = fields.Char(
        string="Gemini Model",
        default="gemini-2.5-flash",
        config_parameter="real_estate_ai.gemini_model",
    )
    realestate_stt_provider = fields.Selection(
        [
            ("whisper", "OpenAI Whisper (Local)"),
            ("google", "Google Cloud Speech-to-Text"),
        ],
        string="Speech-to-Text Provider",
        default="whisper",
        config_parameter="real_estate_ai.stt_provider",
    )
    realestate_google_api_key = fields.Char(
        string="Google Cloud Speech API Key",
        config_parameter="real_estate_ai.google_api_key",
    )
    realestate_whisper_model = fields.Selection(
        [
            ("tiny", "Tiny"),
            ("base", "Base"),
            ("small", "Small"),
            ("medium", "Medium"),
            ("large", "Large"),
        ],
        string="Whisper Model",
        default="base",
        config_parameter="real_estate_ai.whisper_model",
    )
    
    # Twilio Settings
    realestate_twilio_account_sid = fields.Char(
        string="Twilio Account SID",
        config_parameter="real_estate_twilio.account_sid",
    )
    realestate_twilio_auth_token = fields.Char(
        string="Twilio Auth Token",
        config_parameter="real_estate_twilio.auth_token",
        password=True,
    )
    realestate_twilio_from_number = fields.Char(
        string="Twilio From Number",
        config_parameter="real_estate_twilio.from_number",
        help="Phone number in E.164 format (e.g., +12125552368)",
    )
    realestate_twilio_enabled = fields.Boolean(
        string="Enable Twilio VoIP",
        config_parameter="real_estate_twilio.enabled",
        help="Enable Twilio integration for click-to-call functionality",
    )
