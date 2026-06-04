import logging
import re
from urllib.parse import quote

from odoo import _, fields, models
from odoo.exceptions import UserError

try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None

_logger = logging.getLogger(__name__)


class RealEstateTwilioService(models.AbstractModel):
    _name = "realestate.twilio.service"
    _description = "Real Estate Twilio VoIP Service"

    def _get_param(self, key, default=False):
        return self.env["ir.config_parameter"].sudo().get_param(key, default)

    def _get_twilio_client(self):
        """Initialize and return Twilio client"""
        if not TwilioClient:
            raise UserError(_("Twilio SDK is not installed. Install it with: pip install twilio"))

        account_sid = self._get_param("real_estate_twilio.account_sid")
        auth_token = self._get_param("real_estate_twilio.auth_token")
        
        if not account_sid or not auth_token:
            raise UserError(_("Twilio Account SID and Auth Token must be configured in settings."))
        
        return TwilioClient(account_sid, auth_token)

    def _sanitize_number(self, number):
        """Sanitize phone number to E.164 format"""
        # Remove all non-numeric characters except +
        sanitized = re.sub(r"[^\d+]", "", number or "")
        
        # If it doesn't start with +, add country code (default +1 for US/Canada)
        if sanitized and not sanitized.startswith("+"):
            sanitized = "+1" + sanitized
        
        return sanitized

    def make_call(self, call):
        """Initiate a call using Twilio"""
        call.ensure_one()
        
        if not call.agent_id.twilio_phone_number:
            raise UserError(
                _("The assigned agent must have a Twilio phone number configured in their profile.")
            )
        
        twilio_from = self._get_param("real_estate_twilio.from_number")
        if not twilio_from:
            raise UserError(_("Twilio 'From' phone number must be configured in settings."))
        
        # Sanitize phone numbers
        customer_number = self._sanitize_number(call.customer_number)
        agent_number = call.agent_id.twilio_phone_number
        twilio_from_sanitized = self._sanitize_number(twilio_from)
        
        if not customer_number:
            raise UserError(_("Invalid customer phone number."))
        
        try:
            client = self._get_twilio_client()
            
            # Build webhook URLs
            base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            status_callback = f"{base_url}/twilio/call-status/{call.id}"
            recording_callback = f"{base_url}/twilio/recording/{call.id}"
            
            # Create the call through Twilio
            twilio_call = client.calls.create(
                to=customer_number,
                from_=twilio_from_sanitized,
                url=f"{base_url}/twilio/call-handler/{call.id}?agent_number={quote(agent_number)}",
                status_callback=status_callback,
                status_callback_method="POST",
                record=True,
                recording_status_callback=recording_callback,
                recording_status_callback_method="POST",
            )
            
            # Store the Twilio SID for tracking
            call.write({
                "twilio_call_sid": twilio_call.sid,
                "status": "ringing",
                "start_time": fields.Datetime.now(),
            })
            
            _logger.info(f"Twilio call initiated: {twilio_call.sid} for call {call.id}")
            return twilio_call
            
        except Exception as e:
            _logger.error(f"Twilio call failed: {str(e)}")
            call.write({"status": "failed"})
            raise UserError(_("Failed to initiate call: %s") % str(e))

    def end_call(self, call):
        """End a call using Twilio"""
        call.ensure_one()
        
        if not call.twilio_call_sid:
            raise UserError(_("No Twilio call ID found for this call."))
        
        try:
            client = self._get_twilio_client()
            call_instance = client.calls(call.twilio_call_sid).fetch()
            
            # Twilio automatically ends calls, we just update status
            if call_instance.status not in ["completed", "failed", "no-answer"]:
                call.write({"status": "completed"})
            
            _logger.info(f"Twilio call {call.twilio_call_sid} ended")
            
        except Exception as e:
            _logger.error(f"Error ending Twilio call: {str(e)}")
            raise UserError(_("Failed to end call: %s") % str(e))

    def handle_call_status(self, call, status, duration=None):
        """Handle status updates from Twilio webhook"""
        call.ensure_one()
        
        status_map = {
            "initiated": "queued",
            "ringing": "ringing",
            "answered": "answered",
            "completed": "completed",
            "failed": "failed",
            "no-answer": "missed",
            "busy": "failed",
            "canceled": "cancelled",
        }
        
        mapped_status = status_map.get(status, status)
        
        vals = {"status": mapped_status}
        if duration:
            vals["duration"] = int(duration)
        
        call.write(vals)
        _logger.info(f"Call {call.id} status updated to {mapped_status}")

    def fetch_recording(self, call):
        """Fetch and save recording from Twilio"""
        call.ensure_one()
        
        if not call.twilio_call_sid:
            return False
        
        try:
            client = self._get_twilio_client()
            
            # Get recordings for this call
            recordings = client.recordings.stream(limit=10)
            
            for recording in recordings:
                if recording.call_sid == call.twilio_call_sid:
                    # Download the recording
                    recording_url = recording.uri.replace(".json", ".mp3")
                    account_sid = self._get_param("real_estate_twilio.account_sid")
                    auth_token = self._get_param("real_estate_twilio.auth_token")
                    
                    full_url = f"https://api.twilio.com{recording_url}"
                    
                    import urllib.request
                    req = urllib.request.Request(full_url)
                    req.add_header("Authorization", f"Basic {self._encode_auth(account_sid, auth_token)}")
                    
                    with urllib.request.urlopen(req) as response:
                        recording_data = response.read()
                    
                    # Save as attachment
                    attachment = self.env["ir.attachment"].create({
                        "name": f"recording_{call.id}.mp3",
                        "type": "binary",
                        "datas": recording_data,
                        "res_model": "realestate.call",
                        "res_id": call.id,
                    })
                    
                    call.write({
                        "recording_attachment_id": attachment.id,
                        "recording_format": "mp3",
                    })
                    
                    return attachment
            
        except Exception as e:
            _logger.error(f"Failed to fetch Twilio recording: {str(e)}")
        
        return False

    @staticmethod
    def _encode_auth(account_sid, auth_token):
        """Encode Twilio credentials for HTTP Basic Auth"""
        import base64
        credentials = f"{account_sid}:{auth_token}"
        return base64.b64encode(credentials.encode()).decode()
