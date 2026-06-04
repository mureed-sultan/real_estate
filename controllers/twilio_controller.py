import json
import logging
from werkzeug.exceptions import BadRequest

from odoo import http, _
from odoo.exceptions import ValidationError

try:
    from twilio.request_validator import RequestValidator
except ImportError:
    RequestValidator = None

_logger = logging.getLogger(__name__)


class TwilioController(http.Controller):
    """Handle Twilio webhooks and call management"""

    def _validate_twilio_request(self, **kwargs):
        """Validate Twilio request signature"""
        if not RequestValidator:
            _logger.warning("Twilio SDK not installed, skipping signature validation")
            return True

        auth_token = http.request.env["ir.config_parameter"].sudo().get_param(
            "real_estate_twilio.auth_token"
        )
        
        if not auth_token:
            _logger.warning("Twilio Auth Token not configured")
            return False

        validator = RequestValidator(auth_token)
        
        # Get request URL
        url = http.request.url
        
        # Get POST/GET data
        data = http.request.params.to_dict() if http.request.method == "POST" else {}
        
        # Get signature from request headers
        signature = http.request.headers.get("X-Twilio-Signature", "")
        
        # Validate
        return validator.validate(url, data, signature)

    @http.route("/twilio/call-status/<int:call_id>", type="http", auth="public", csrf=False, methods=["POST"])
    def handle_call_status(self, call_id, **kwargs):
        """Handle call status updates from Twilio webhook"""
        try:
            # Validate request
            if not self._validate_twilio_request(**kwargs):
                _logger.warning(f"Invalid Twilio signature for call {call_id}")
                return http.Response("Invalid signature", status=403)

            call = http.request.env["realestate.call"].browse(call_id)
            if not call.exists():
                return http.Response("Call not found", status=404)

            # Get status from webhook data
            status = http.request.params.get("CallStatus", "")
            duration = http.request.params.get("CallDuration", "")
            
            # Handle the status update
            twilio_service = http.request.env["realestate.twilio.service"]
            twilio_service.handle_call_status(call, status, duration)

            _logger.info(f"Call {call_id} status updated to {status}")
            return http.Response("OK", status=200)

        except Exception as e:
            _logger.error(f"Error handling call status: {str(e)}")
            return http.Response("Error processing request", status=500)

    @http.route("/twilio/recording/<int:call_id>", type="http", auth="public", csrf=False, methods=["POST"])
    def handle_recording(self, call_id, **kwargs):
        """Handle recording status updates from Twilio webhook"""
        try:
            if not self._validate_twilio_request(**kwargs):
                _logger.warning(f"Invalid Twilio signature for recording {call_id}")
                return http.Response("Invalid signature", status=403)

            call = http.request.env["realestate.call"].browse(call_id)
            if not call.exists():
                return http.Response("Call not found", status=404)

            # Fetch and save the recording
            twilio_service = http.request.env["realestate.twilio.service"]
            twilio_service.fetch_recording(call)

            _logger.info(f"Recording fetched for call {call_id}")
            return http.Response("OK", status=200)

        except Exception as e:
            _logger.error(f"Error handling recording: {str(e)}")
            return http.Response("Error processing request", status=500)

    @http.route("/twilio/call-handler/<int:call_id>", type="http", auth="public", csrf=False)
    def handle_call_flow(self, call_id, **kwargs):
        """Generate TwiML response for call flow"""
        try:
            call = http.request.env["realestate.call"].browse(call_id)
            if not call.exists():
                twiml = '<Response><Say>Call not found</Say></Response>'
                return http.Response(twiml, content_type="application/xml")

            agent_number = http.request.params.get("agent_number", "")
            
            if not agent_number:
                twiml = '<Response><Say>Agent number not provided</Say></Response>'
                return http.Response(twiml, content_type="application/xml")

            # Generate TwiML to connect to agent
            twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting you to your agent</Say>
    <Dial recordingStatusCallback="{http.request.base_url}/twilio/recording/{call_id}">
        <Number>{agent_number}</Number>
    </Dial>
</Response>'''
            
            return http.Response(twiml, content_type="application/xml")

        except Exception as e:
            _logger.error(f"Error handling call flow: {str(e)}")
            twiml = '<Response><Say>An error occurred</Say></Response>'
            return http.Response(twiml, content_type="application/xml")

    @http.route("/twilio/call-api/<int:call_id>/make", type="http", auth="user", methods=["POST"])
    def make_call_api(self, call_id, **kwargs):
        """API endpoint to initiate a call via Twilio"""
        try:
            call = http.request.env["realestate.call"].browse(call_id)
            if not call.exists():
                return http.Response(
                    json.dumps({"status": "error", "message": "Call not found"}),
                    content_type="application/json",
                    status=404
                )

            # Initiate call using Twilio service
            twilio_service = http.request.env["realestate.twilio.service"]
            twilio_service.make_call(call)

            return http.Response(
                json.dumps({"status": "success", "message": "Call initiated"}),
                content_type="application/json"
            )

        except ValidationError as e:
            return http.Response(
                json.dumps({"status": "error", "message": str(e)}),
                content_type="application/json",
                status=400
            )
        except Exception as e:
            _logger.error(f"Error making call: {str(e)}")
            return http.Response(
                json.dumps({"status": "error", "message": str(e)}),
                content_type="application/json",
                status=500
            )

    @http.route("/twilio/call-api/<int:call_id>/end", type="http", auth="user", methods=["POST"])
    def end_call_api(self, call_id, **kwargs):
        """API endpoint to end a call via Twilio"""
        try:
            call = http.request.env["realestate.call"].browse(call_id)
            if not call.exists():
                return http.Response(
                    json.dumps({"status": "error", "message": "Call not found"}),
                    content_type="application/json",
                    status=404
                )

            # End call using Twilio service
            twilio_service = http.request.env["realestate.twilio.service"]
            twilio_service.end_call(call)

            return http.Response(
                json.dumps({"status": "success", "message": "Call ended"}),
                content_type="application/json"
            )

        except ValidationError as e:
            return http.Response(
                json.dumps({"status": "error", "message": str(e)}),
                content_type="application/json",
                status=400
            )
        except Exception as e:
            _logger.error(f"Error ending call: {str(e)}")
            return http.Response(
                json.dumps({"status": "error", "message": str(e)}),
                content_type="application/json",
                status=500
            )
