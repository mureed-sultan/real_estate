import json
import logging

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

try:
    from twilio.request_validator import RequestValidator
except Exception:
    RequestValidator = None

_logger = logging.getLogger(__name__)


class TwilioController(http.Controller):

    # -------------------------------------------------
    # SAFE VALIDATION (NO CRASH)
    # -------------------------------------------------
    def _validate_twilio_request(self):
        try:
            if not RequestValidator:
                _logger.warning("Twilio SDK not installed")
                return True

            auth_token = request.env["ir.config_parameter"].sudo().get_param(
                "real_estate_twilio.auth_token"
            )

            if not auth_token:
                _logger.warning("Missing Twilio auth token")
                return False

            validator = RequestValidator(auth_token)

            url = request.httprequest.url

            # SAFE: always use form dict
            data = dict(request.httprequest.form)

            signature = request.httprequest.headers.get("X-Twilio-Signature", "")

            return validator.validate(url, data, signature)

        except Exception as e:
            _logger.exception("Twilio validation crash prevented")
            return False

    # -------------------------------------------------
    # CALL STATUS WEBHOOK
    # -------------------------------------------------
    @http.route("/twilio/call-status/<int:call_id>", type="http", auth="public", csrf=False, methods=["POST"])
    def handle_call_status(self, call_id, **kwargs):

        try:
            if not self._validate_twilio_request():
                return http.Response("Invalid signature", status=403)

            call = request.env["realestate.call"].sudo().browse(call_id)
            if not call.exists():
                return http.Response("Not found", status=404)

            status = request.httprequest.form.get("CallStatus")
            duration = request.httprequest.form.get("CallDuration")

            request.env["realestate.twilio.service"].sudo().handle_call_status(
                call, status, duration
            )

            return http.Response("OK")

        except Exception:
            _logger.exception("Call status webhook failed")
            return http.Response("Error", status=500)

    # -------------------------------------------------
    # RECORDING WEBHOOK
    # -------------------------------------------------
    @http.route("/twilio/recording/<int:call_id>", type="http", auth="public", csrf=False, methods=["POST"])
    def handle_recording(self, call_id, **kwargs):

        try:
            if not self._validate_twilio_request():
                return http.Response("Invalid signature", status=403)

            call = request.env["realestate.call"].sudo().browse(call_id)
            if not call.exists():
                return http.Response("Not found", status=404)

            request.env["realestate.twilio.service"].sudo().fetch_recording(call)

            return http.Response("OK")

        except Exception:
            _logger.exception("Recording webhook failed")
            return http.Response("Error", status=500)

    # -------------------------------------------------
    # TWIML CALL FLOW
    # -------------------------------------------------
    @http.route("/twilio/call-handler/<int:call_id>", type="http", auth="public", csrf=False)
    def handle_call_flow(self, call_id, **kwargs):

        call = request.env["realestate.call"].sudo().browse(call_id)

        if not call.exists():
            return request.make_response(
                "<Response><Say>Call not found</Say></Response>",
                headers=[("Content-Type", "application/xml")]
            )

        agent_number = request.httprequest.values.get("agent_number")

        if not agent_number:
            return request.make_response(
                "<Response><Say>No agent number</Say></Response>",
                headers=[("Content-Type", "application/xml")]
            )

        base_url = request.httprequest.url_root.rstrip("/")

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting call</Say>
    <Dial recordingStatusCallback="{base_url}/twilio/recording/{call_id}">
        <Number>{agent_number}</Number>
    </Dial>
</Response>"""

        return request.make_response(
            twiml,
            headers=[("Content-Type", "application/xml")]
        )

    # -------------------------------------------------
    # MAKE CALL API
    # -------------------------------------------------
    @http.route("/twilio/call-api/<int:call_id>/make", type="http", auth="user", methods=["POST"])
    def make_call_api(self, call_id, **kwargs):

        try:
            call = request.env["realestate.call"].sudo().browse(call_id)
            if not call.exists():
                return request.make_response(
                    json.dumps({"error": "not found"}),
                    headers=[("Content-Type", "application/json")],
                    status=404
                )

            request.env["realestate.twilio.service"].sudo().make_call(call)

            return request.make_response(
                json.dumps({"status": "ok"}),
                headers=[("Content-Type", "application/json")]
            )

        except Exception:
            _logger.exception("Make call failed")
            return request.make_response(
                json.dumps({"error": "server error"}),
                headers=[("Content-Type", "application/json")],
                status=500
            )

    # -------------------------------------------------
    # END CALL API
    # -------------------------------------------------
    @http.route("/twilio/call-api/<int:call_id>/end", type="http", auth="user", methods=["POST"])
    def end_call_api(self, call_id, **kwargs):

        try:
            call = request.env["realestate.call"].sudo().browse(call_id)
            if not call.exists():
                return request.make_response(
                    json.dumps({"error": "not found"}),
                    headers=[("Content-Type", "application/json")],
                    status=404
                )

            request.env["realestate.twilio.service"].sudo().end_call(call)

            return request.make_response(
                json.dumps({"status": "ok"}),
                headers=[("Content-Type", "application/json")]
            )

        except Exception:
            _logger.exception("End call failed")
            return request.make_response(
                json.dumps({"error": "server error"}),
                headers=[("Content-Type", "application/json")],
                status=500
            )