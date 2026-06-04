import logging
import os
import base64
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TwilioCallHandler(models.Model):
    """Handle in-browser Twilio calls with recording and transcription"""
    _name = "twilio.call.handler"
    _description = "Twilio Call Handler"

    call_id = fields.Many2one("realestate.call", string="Call", required=True, ondelete="cascade")
    recording_data = fields.Binary(string="Recording Data", attachment=True)
    recording_filename = fields.Char(string="Recording Filename")
    transcript = fields.Text(string="Transcript")
    transcript_status = fields.Selection([
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ], string="Transcript Status", default="pending")

    def save_recording(self, recording_data, filename):
        """Save recording binary data to attachment"""
        try:
            call_recording_dir = self._get_recording_directory()
            if not os.path.exists(call_recording_dir):
                os.makedirs(call_recording_dir, mode=0o755)

            filepath = os.path.join(call_recording_dir, filename)
            with open(filepath, "wb") as f:
                f.write(recording_data)

            # Also save as attachment
            attachment = self.env["ir.attachment"].create({
                "name": filename,
                "type": "binary",
                "datas": base64.b64encode(recording_data),
                "res_model": "realestate.call",
                "res_id": self.call_id.id,
                "mimetype": "audio/wav" if filename.endswith(".wav") else "audio/mpeg",
            })

            # Update call record
            self.call_id.write({
                "recording_path": filepath,
                "recording_attachment_id": attachment.id,
                "recording_format": "wav" if filename.endswith(".wav") else "mp3",
            })

            _logger.info(f"Recording saved: {filepath}")
            return filepath

        except Exception as e:
            _logger.error(f"Failed to save recording: {str(e)}")
            raise UserError(_("Failed to save recording: %s") % str(e))

    @staticmethod
    def _get_recording_directory():
        """Get or create call recording directory"""
        addons_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        recording_dir = os.path.join(addons_path, "call_recordings")
        return recording_dir

    def transcribe_with_gemini(self):
        """Use Gemini API to transcribe the recording"""
        self.ensure_one()

        if not self.recording_filename:
            raise UserError(_("No recording file to transcribe"))

        try:
            import google.generativeai as genai
        except ImportError:
            raise UserError(_("Google Generative AI SDK not installed. Run: pip install google-generativeai"))

        try:
            # Get Gemini API key from settings
            api_key = self.env["ir.config_parameter"].sudo().get_param("real_estate_twilio.gemini_api_key")
            if not api_key:
                raise UserError(_("Gemini API key not configured in settings"))

            genai.configure(api_key=api_key)
            
            # Get recording file path
            call_recording_dir = self._get_recording_directory()
            filepath = os.path.join(call_recording_dir, self.recording_filename)

            if not os.path.exists(filepath):
                raise UserError(_("Recording file not found: %s") % filepath)

            # Upload file to Gemini
            self.transcript_status = "processing"
            self.env.cr.commit()

            with open(filepath, "rb") as audio_file:
                audio_data = audio_file.read()

            # Use Gemini to transcribe
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            # Create message with audio file
            audio_part = genai.upload_file(filepath, mime_type="audio/wav")
            
            prompt = "Transcribe this audio call. Provide a clear text transcription of what was said."
            response = model.generate_content([prompt, audio_part])

            transcript_text = response.text

            # Save transcript
            self.write({
                "transcript": transcript_text,
                "transcript_status": "completed",
            })

            self.call_id.write({
                "transcript_text": transcript_text,
                "transcript_source": "google",
                "ai_analyzed_at": fields.Datetime.now(),
            })

            _logger.info(f"Transcription completed for call {self.call_id.id}")
            return transcript_text

        except Exception as e:
            _logger.error(f"Transcription failed: {str(e)}")
            self.write({"transcript_status": "failed"})
            raise UserError(_("Transcription failed: %s") % str(e))

    def analyze_call_sentiment(self):
        """Analyze call sentiment using Gemini"""
        self.ensure_one()

        if not self.transcript:
            raise UserError(_("No transcript available for analysis"))

        try:
            import google.generativeai as genai
        except ImportError:
            raise UserError(_("Google Generative AI SDK not installed"))

        try:
            api_key = self.env["ir.config_parameter"].sudo().get_param("real_estate_twilio.gemini_api_key")
            if not api_key:
                raise UserError(_("Gemini API key not configured"))

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")

            prompt = f"""Analyze this real estate call transcript and determine the lead status.
            
Transcript: {self.transcript}

Provide a JSON response with:
- status: "hot" (interested), "not_interested" (not interested), or "unknown"
- reason: Brief explanation

Example: {{"status": "hot", "reason": "Customer interested in viewing property"}}
"""

            response = model.generate_content(prompt)
            response_text = response.text

            # Parse response
            import json
            try:
                analysis = json.loads(response_text)
                status = analysis.get("status", "unknown")
                reason = analysis.get("reason", "")
            except json.JSONDecodeError:
                status = "unknown"
                reason = response_text

            self.call_id.write({
                "ai_lead_status": status,
                "ai_reason": reason,
                "ai_analyzed_at": fields.Datetime.now(),
                "ai_raw_response": response_text,
            })

            _logger.info(f"Call analysis completed: {status}")
            return status

        except Exception as e:
            _logger.error(f"Sentiment analysis failed: {str(e)}")
            raise UserError(_("Analysis failed: %s") % str(e))
