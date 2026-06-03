import json
import mimetypes
import os
import urllib.error
import urllib.request
import wave
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RealEstateCall(models.Model):
    _name = "realestate.call"
    _description = "Real Estate Call"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_time desc, id desc"

    lead_id = fields.Many2one("crm.lead", string="Lead", required=True, ondelete="cascade", index=True, tracking=True)
    customer_name = fields.Char(string="Customer Name", required=True, tracking=True)
    customer_number = fields.Char(string="Customer Number", required=True, tracking=True)
    agent_id = fields.Many2one("res.users", string="Agent", required=True, default=lambda self: self.env.user, index=True, tracking=True)
    start_time = fields.Datetime(string="Start Time", default=fields.Datetime.now, tracking=True)
    end_time = fields.Datetime(string="End Time", tracking=True)
    duration = fields.Integer(string="Duration", help="Duration in seconds", tracking=True)
    duration_display = fields.Char(string="Duration", compute="_compute_duration_display")
    status = fields.Selection([
        ("queued", "Queued"),
        ("ringing", "Ringing"),
        ("answered", "Answered"),
        ("completed", "Completed"),
        ("missed", "Missed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ], string="Status", default="queued", required=True, index=True, tracking=True)
    recording_path = fields.Char(string="Recording Path", tracking=True)
    recording_format = fields.Selection([("wav", "WAV"), ("mp3", "MP3")], string="Recording Format", default="wav")
    recording_exists = fields.Boolean(string="Recording Exists", compute="_compute_recording_exists")
    recording_mimetype = fields.Char(string="Recording MIME Type", compute="_compute_recording_mimetype")
    transcript_text = fields.Text(string="Transcript", tracking=True)
    transcript_source = fields.Selection([
        ("manual", "Manual"),
        ("whisper", "Whisper"),
    ], string="Transcript Source", default="manual")
    ai_lead_status = fields.Selection([
        ("hot", "Hot"),
        ("not_interested", "Not Interested"),
        ("unknown", "Unknown"),
    ], string="AI Lead Status", default="unknown", tracking=True)
    ai_reason = fields.Text(string="AI Reason", tracking=True)
    ai_analyzed_at = fields.Datetime(string="AI Analyzed At", readonly=True)
    ai_raw_response = fields.Text(string="AI Raw Response", readonly=True)
    ami_action_id = fields.Char(string="AMI Action ID", index=True, copy=False)
    asterisk_unique_id = fields.Char(string="Asterisk Unique ID", index=True, copy=False)
    company_id = fields.Many2one("res.company", string="Company", related="lead_id.company_id", store=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("lead_id"):
                lead = self.env["crm.lead"].browse(vals["lead_id"])
                vals.setdefault("customer_name", lead.realestate_customer_name or lead.contact_name or lead.partner_name or lead.name)
                vals.setdefault("customer_number", lead.mobile_number or lead.phone)
                vals.setdefault("agent_id", lead.user_id.id or self.env.uid)
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if {"start_time", "end_time"} & set(vals) and "duration" not in vals:
            for call in self.filtered(lambda item: item.start_time and item.end_time):
                duration = max(0, int((call.end_time - call.start_time).total_seconds()))
                if call.duration != duration:
                    super(RealEstateCall, call).write({"duration": duration})
        return result

    def _format_seconds(self, seconds):
        seconds = int(seconds or 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return "%02d:%02d:%02d" % (hours, minutes, seconds)

    def _compute_duration_display(self):
        for call in self:
            call.duration_display = self._format_seconds(call.duration)

    def _recording_file_exists(self):
        self.ensure_one()
        service = self.env["realestate.asterisk.service"]
        return bool(
            self.recording_path
            and service.is_recording_path_allowed(self.recording_path)
            and os.path.isfile(os.path.realpath(os.path.expanduser(self.recording_path)))
        )

    def _compute_recording_exists(self):
        for call in self:
            call.recording_exists = call._recording_file_exists()

    def _compute_recording_mimetype(self):
        for call in self:
            mimetype, _encoding = mimetypes.guess_type(call.recording_path or "")
            call.recording_mimetype = mimetype or "application/octet-stream"

    def _ensure_recording_available(self):
        self.ensure_one()
        if not self._recording_file_exists():
            raise UserError(_("The recording file is not available on the server."))
        return os.path.realpath(os.path.expanduser(self.recording_path))

    def action_play_recording(self):
        self.ensure_one()
        self._ensure_recording_available()
        return {
            "type": "ir.actions.act_url",
            "url": "/real_estate/call/%s/recording/play" % self.id,
            "target": "new",
        }

    def action_download_recording(self):
        self.ensure_one()
        self._ensure_recording_available()
        return {
            "type": "ir.actions.act_url",
            "url": "/real_estate/call/%s/recording/download" % self.id,
            "target": "self",
        }

    def action_transcribe_recording(self):
        self.ensure_one()
        path = self._ensure_recording_available()
        try:
            import librosa
            import whisper
        except ImportError as error:
            raise UserError(_("Install optional Python packages openai-whisper and librosa on the Odoo server to use local transcription.")) from error

        model_name = self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.whisper_model", "base") or "base"
        try:
            model = whisper.load_model(model_name)
            audio_array, _sample_rate = librosa.load(path, sr=16000)
            result = model.transcribe(audio_array, fp16=False)
        except Exception as error:
            raise UserError(_("Whisper transcription failed: %s") % error) from error

        transcript = (result or {}).get("text", "").strip()
        self.write({
            "transcript_text": transcript,
            "transcript_source": "whisper",
        })
        return True

    def _gemini_api_key(self):
        return self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.gemini_api_key")

    def _gemini_model(self):
        return self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.gemini_model", "gemini-2.5-flash") or "gemini-2.5-flash"

    def _prepare_ai_prompt(self):
        self.ensure_one()
        return """
Analyze this real estate sales call transcript and classify the lead.
Respond ONLY with a valid JSON object matching this structure:
{
    "status": "hot" or "not_interested",
    "reason": "a short one-sentence explanation in Roman Urdu or English"
}

Transcript:
%s
""" % (self.transcript_text or "")

    def action_analyze_transcript(self):
        for call in self:
            call._analyze_transcript()
        return True

    def _analyze_transcript(self):
        self.ensure_one()
        if not self.transcript_text:
            raise UserError(_("Add or generate a transcript before running AI analysis."))
        api_key = self._gemini_api_key()
        if not api_key:
            raise UserError(_("Configure the Gemini API key in CRM Settings before running AI analysis."))

        model = self._gemini_model()
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s" % (model, api_key)
        payload = {
            "contents": [{"parts": [{"text": self._prepare_ai_prompt()}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        request_data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        http_request = urllib.request.Request(endpoint, data=request_data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(http_request, timeout=45) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise UserError(_("Gemini analysis failed (%s): %s") % (error.code, body)) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise UserError(_("Gemini analysis failed: %s") % error) from error

        try:
            raw_text = response_data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(raw_text)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as error:
            raise UserError(_("Gemini returned an unexpected response: %s") % response_data) from error

        status = result.get("status")
        if status not in ("hot", "not_interested"):
            status = "unknown"
        values = {
            "ai_lead_status": status,
            "ai_reason": result.get("reason"),
            "ai_analyzed_at": fields.Datetime.now(),
            "ai_raw_response": json.dumps(response_data, indent=2),
        }
        self.write(values)
        self.lead_id.write({
            "ai_lead_status": status,
            "ai_reason": result.get("reason"),
            "ai_last_call_id": self.id,
        })
        return result

    def _wav_duration(self, path):
        try:
            with wave.open(path, "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                if not frame_rate:
                    return 0
                return int(wav_file.getnframes() / float(frame_rate))
        except (wave.Error, OSError):
            return 0

    def _recording_duration(self):
        self.ensure_one()
        if not self._recording_file_exists():
            return 0
        path = os.path.realpath(os.path.expanduser(self.recording_path))
        if path.lower().endswith(".wav"):
            return self._wav_duration(path)
        return 0

    @api.model
    def _cron_finalize_stale_calls(self):
        stale_minutes = int(self.env["ir.config_parameter"].sudo().get_param("real_estate_asterisk.stale_call_minutes", 180) or 180)
        now = fields.Datetime.now()
        stale_before = now - timedelta(minutes=stale_minutes)
        calls = self.sudo().search([
            ("status", "in", ["queued", "ringing", "answered"]),
            ("start_time", "!=", False),
        ])
        for call in calls:
            values = {}
            recording_duration = call._recording_duration()
            if recording_duration:
                values["duration"] = recording_duration
                values.setdefault("status", "completed")
                if not call.end_time:
                    values["end_time"] = call.start_time + timedelta(seconds=recording_duration)
            elif call._recording_file_exists() and call.status in ("queued", "ringing"):
                values["status"] = "answered"
            elif call.start_time < stale_before and call.status in ("queued", "ringing"):
                values.update({"status": "missed", "end_time": now, "duration": 0})

            if values:
                call.write(values)

    @api.model
    def _cron_analyze_pending_transcripts(self):
        if not self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.gemini_api_key"):
            return
        calls = self.sudo().search([
            ("transcript_text", "!=", False),
            ("ai_lead_status", "=", "unknown"),
        ], limit=25)
        for call in calls:
            try:
                call._analyze_transcript()
            except UserError:
                continue

    @api.model
    def _update_from_asterisk_payload(self, payload):
        call = self.browse()
        call_id = payload.get("call_id") or payload.get("realestate_call_id") or payload.get("REALESTATE_CALL_ID")
        if call_id:
            call = self.sudo().browse(int(call_id)).exists()
        if not call and payload.get("unique_id"):
            call = self.sudo().search([("asterisk_unique_id", "=", payload["unique_id"])], limit=1)
        if not call and payload.get("ami_action_id"):
            call = self.sudo().search([("ami_action_id", "=", payload["ami_action_id"])], limit=1)
        if not call:
            return self.browse()

        values = {}
        status = (payload.get("status") or payload.get("event") or "").lower()
        status_map = {
            "answered": "answered",
            "bridge": "answered",
            "bridgeenter": "answered",
            "up": "answered",
            "completed": "completed",
            "hangup": "completed",
            "missed": "missed",
            "noanswer": "missed",
            "busy": "missed",
            "failed": "failed",
            "failure": "failed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }
        if status in status_map:
            values["status"] = status_map[status]
        if payload.get("unique_id"):
            values["asterisk_unique_id"] = payload["unique_id"]
        if payload.get("recording_path"):
            service = self.env["realestate.asterisk.service"]
            if service.is_recording_path_allowed(payload["recording_path"]):
                values["recording_path"] = payload["recording_path"]
        if payload.get("duration"):
            values["duration"] = int(float(payload["duration"]))
        if payload.get("start_time"):
            values["start_time"] = fields.Datetime.to_datetime(payload["start_time"])
        if payload.get("end_time"):
            values["end_time"] = fields.Datetime.to_datetime(payload["end_time"])
        elif values.get("status") in ("completed", "missed", "failed", "cancelled"):
            values["end_time"] = fields.Datetime.now()

        call.write(values)
        return call
