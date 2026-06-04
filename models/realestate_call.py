import json
import logging
import mimetypes
import os
import urllib.error
import urllib.request
import wave
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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
    recording_attachment_id = fields.Many2one("ir.attachment", string="Recording Attachment", ondelete="set null", tracking=True)
    recording_exists = fields.Boolean(string="Recording Exists", compute="_compute_recording_exists")
    recording_mimetype = fields.Char(string="Recording MIME Type", compute="_compute_recording_mimetype")
    transcript_text = fields.Text(string="Transcript", tracking=True)
    transcript_source = fields.Selection([
        ("manual", "Manual"),
        ("whisper", "Whisper (Local)"),
        ("google", "Google STT"),
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
    twilio_call_sid = fields.Char(string="Twilio Call SID", index=True, copy=False, readonly=True)
    voip_provider = fields.Selection([
        ("asterisk", "Asterisk"),
        ("twilio", "Twilio"),
        ("manual", "Manual Upload"),
    ], string="VoIP Provider", tracking=True)
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
        # Track calls whose status is being modified
        calls_to_finalize = self.env["realestate.call"]
        if "status" in vals:
            for call in self:
                if call.status != vals["status"]:
                    calls_to_finalize |= call

        result = super().write(vals)
        if {"start_time", "end_time"} & set(vals) and "duration" not in vals:
            for call in self.filtered(lambda item: item.start_time and item.end_time):
                duration = max(0, int((call.end_time - call.start_time).total_seconds()))
                if call.duration != duration:
                    super(RealEstateCall, call).write({"duration": duration})

        # Run post-call processing for completed calls
        if calls_to_finalize:
            for call in calls_to_finalize:
                call._post_call_processing()

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
            call.recording_exists = bool(call.recording_attachment_id) or call._recording_file_exists()

    def _compute_recording_mimetype(self):
        for call in self:
            if call.recording_attachment_id:
                call.recording_mimetype = call.recording_attachment_id.mimetype or "application/octet-stream"
            else:
                mimetype, _encoding = mimetypes.guess_type(call.recording_path or "")
                call.recording_mimetype = mimetype or "application/octet-stream"

    def _ensure_recording_available(self):
        self.ensure_one()
        if self.recording_attachment_id:
            return True
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

    def _convert_wav_to_mp3(self, wav_path):
        """Converts a WAV file to MP3. Returns the path to the MP3 file or False if conversion failed."""
        if not wav_path or not os.path.exists(wav_path):
            return False
        mp3_path = os.path.splitext(wav_path)[0] + ".mp3"
        if os.path.exists(mp3_path):
            return mp3_path

        # Try using pydub
        try:
            from pydub import AudioSegment
            sound = AudioSegment.from_wav(wav_path)
            sound.export(mp3_path, format="mp3")
            _logger.info("Successfully converted %s to MP3 using pydub", wav_path)
            return mp3_path
        except Exception as e:
            _logger.debug("pydub conversion failed: %s. Trying ffmpeg...", e)

        # Try using subprocess ffmpeg
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0 and os.path.exists(mp3_path):
                _logger.info("Successfully converted %s to MP3 using ffmpeg and libmp3lame", wav_path)
                return mp3_path
        except Exception as e:
            _logger.debug("ffmpeg libmp3lame conversion failed: %s. Trying fallback ffmpeg...", e)

        # Try using subprocess ffmpeg with default encoder if libmp3lame is missing
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, mp3_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0 and os.path.exists(mp3_path):
                _logger.info("Successfully converted %s to MP3 using fallback ffmpeg", wav_path)
                return mp3_path
        except Exception as e:
            _logger.warning("All WAV to MP3 conversion attempts failed for %s: %s", wav_path, e)

        return False

    def _save_recording_as_attachment(self):
        self.ensure_one()
        if not self.recording_path:
            return False

        path = os.path.realpath(os.path.expanduser(self.recording_path))
        if not os.path.exists(path):
            _logger.warning("Recording file not found on disk at path: %s", path)
            return False

        # Convert to MP3 if it's WAV
        if path.lower().endswith(".wav"):
            mp3_path = self._convert_wav_to_mp3(path)
            if mp3_path and os.path.exists(mp3_path):
                path = mp3_path

        filename = os.path.basename(path)
        mimetype = "audio/mpeg" if filename.lower().endswith(".mp3") else "audio/wav"

        with open(path, "rb") as f:
            data = f.read()

        import base64
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(data),
            "res_model": "crm.lead" if self.lead_id else "realestate.call",
            "res_id": self.lead_id.id if self.lead_id else self.id,
            "mimetype": mimetype,
        })

        self.write({
            "recording_attachment_id": attachment.id,
        })

        if self.lead_id:
            self.lead_id.message_post(
                body=_("Call recording saved: %s") % filename,
                attachment_ids=[attachment.id]
            )
        return attachment

    def _get_audio_data_and_ext(self):
        """Returns (audio_bytes, file_extension, temp_filepath, is_temp).
        temp_filepath should be cleaned up by the caller if is_temp is True.
        """
        self.ensure_one()
        if self.recording_attachment_id:
            import base64
            import tempfile
            data = base64.b64decode(self.recording_attachment_id.datas or b"")
            filename = self.recording_attachment_id.name or "recording.mp3"
            ext = os.path.splitext(filename)[1].replace(".", "") or "mp3"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix="." + ext)
            temp_file.write(data)
            temp_file.close()
            return data, ext, temp_file.name, True

        path = self._ensure_recording_available()
        # If it returned True because attachment exists but path is missing, handle gracefully
        if path is True:
            raise UserError(_("Recording path could not be resolved from attachment."))
        ext = os.path.splitext(path)[1].replace(".", "") or "wav"
        with open(path, "rb") as f:
            data = f.read()
        return data, ext, path, False

    def _transcribe_google_stt(self, audio_data, file_ext):
        api_key = self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.google_api_key")
        if not api_key:
            raise UserError(_("Google Cloud Speech API Key is not configured in settings."))

        import base64
        import json
        import urllib.request
        import urllib.error

        audio_content = base64.b64encode(audio_data).decode("utf-8")
        encoding = "MP3" if file_ext.lower() == "mp3" else "LINEAR16"

        payload = {
            "config": {
                "encoding": encoding,
                "sampleRateHertz": 16000,
                "languageCode": "en-US",
                "alternativeLanguageCodes": ["ur-PK"],
            },
            "audio": {
                "content": audio_content
            }
        }

        endpoint = "https://speech.googleapis.com/v1/speech:recognize?key=%s" % api_key
        request_data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        http_request = urllib.request.Request(endpoint, data=request_data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(http_request, timeout=60) as response:
                response_data = json.loads(response.read().decode("utf-8"))

            transcript_parts = []
            results = response_data.get("results", [])
            for result in results:
                alternatives = result.get("alternatives", [])
                if alternatives:
                    transcript_parts.append(alternatives[0].get("transcript", ""))
            return " ".join(transcript_parts).strip()
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise UserError(_("Google STT failed (%s): %s") % (error.code, body)) from error
        except Exception as error:
            raise UserError(_("Google STT failed: %s") % error) from error

    def _transcribe_recording_auto(self):
        self.ensure_one()
        provider = self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.stt_provider", "whisper")

        if provider == "google":
            data, ext, path, is_temp = self._get_audio_data_and_ext()
            try:
                transcript = self._transcribe_google_stt(data, ext)
            finally:
                if is_temp and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
            self.write({
                "transcript_text": transcript,
                "transcript_source": "google",
            })
            return True
        else:
            # Whisper local
            data, ext, path, is_temp = self._get_audio_data_and_ext()
            try:
                import librosa
                import whisper
            except ImportError as error:
                if is_temp and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
                raise UserError(_("Install optional Python packages openai-whisper and librosa on the Odoo server to use local transcription.")) from error

            model_name = self.env["ir.config_parameter"].sudo().get_param("real_estate_ai.whisper_model", "base") or "base"
            try:
                model = whisper.load_model(model_name)
                audio_array, _sample_rate = librosa.load(path, sr=16000)
                result = model.transcribe(audio_array, fp16=False)
                transcript = (result or {}).get("text", "").strip()
            except Exception as error:
                raise UserError(_("Whisper transcription failed: %s") % error) from error
            finally:
                if is_temp and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass

            self.write({
                "transcript_text": transcript,
                "transcript_source": "whisper",
            })
            return True

    def action_transcribe_recording(self):
        self.ensure_one()
        self._transcribe_recording_auto()
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
    "reason": "a short one-sentence explanation in Arabic or English"
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
        if self.lead_id:
            self.lead_id.write({
                "ai_lead_status": status,
                "ai_reason": result.get("reason"),
                "ai_transcript": self.transcript_text,
                "ai_last_call_id": self.id,
            })
        return result

    def _run_automated_transcription_and_analysis(self):
        """Runs the automated transcription and analysis pipeline without raising blocker exceptions."""
        self.ensure_one()
        try:
            # 1. Transcribe
            self._transcribe_recording_auto()
            # 2. Analyze
            if self.transcript_text:
                self._analyze_transcript()
            else:
                self.message_post(body=_("Automated analysis skipped: Transcript is empty."))
        except Exception as e:
            _logger.exception("Failed automated call transcription/analysis for call %s", self.id)
            self.message_post(body=_("Automated Call Processing failed: %s") % str(e))

    def _post_call_processing(self):
        self.ensure_one()
        # 1. Save recording as attachment (which will convert WAV to MP3 if needed)
        if self.status == "completed" and self.recording_path:
            try:
                self._save_recording_as_attachment()
            except Exception as e:
                _logger.exception("Failed to save call recording as attachment for call %s", self.id)
                self.message_post(body=_("Failed to save/convert recording to attachment: %s") % str(e))

        # 2. Trigger transcription and AI analysis automatically
        if self.status == "completed":
            self._run_automated_transcription_and_analysis()

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

    def action_open_phone_interface(self):
        """Open in-browser Twilio phone interface"""
        self.ensure_one()
        
        twilio_enabled = self.env["ir.config_parameter"].sudo().get_param("real_estate_twilio.enabled")
        if not twilio_enabled:
            raise UserError(_("Twilio VoIP is not enabled. Please enable it in settings."))
        
        if not self.agent_id.twilio_phone_number:
            raise UserError(
                _("The assigned agent must have a Twilio phone number configured in their profile.")
            )
        
        # Open phone interface in new window
        return {
            "type": "ir.actions.act_url",
            "url": f"/twilio/call/{self.id}/phone-view",
            "target": "new",
        }

    def action_twilio_make_call(self):
        """Action button to make a call via Twilio"""
        self.ensure_one()
        
        twilio_enabled = self.env["ir.config_parameter"].sudo().get_param("real_estate_twilio.enabled")
        if not twilio_enabled:
            raise UserError(_("Twilio VoIP is not enabled. Please enable it in settings."))
        
        service = self.env["realestate.twilio.service"]
        self.voip_provider = "twilio"
        service.make_call(self)
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Call Initiated"),
                "message": _("Twilio call has been initiated. Check your phone."),
                "sticky": False,
            },
        }

    def action_twilio_end_call(self):
        """Action button to end an active Twilio call"""
        self.ensure_one()
        
        if not self.twilio_call_sid:
            raise UserError(_("This call does not have a Twilio Call SID."))
        
        service = self.env["realestate.twilio.service"]
        service.end_call(self)
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Call Ended"),
                "message": _("Twilio call has been ended."),
                "sticky": False,
            },
        }