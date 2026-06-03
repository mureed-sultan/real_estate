import logging
import os
import re
import socket
import uuid

from odoo import _, fields, models, tools
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class RealEstateAsteriskService(models.AbstractModel):
    _name = "realestate.asterisk.service"
    _description = "Real Estate Asterisk AMI Service"

    def _get_param(self, key, default=False):
        return self.env["ir.config_parameter"].sudo().get_param(key, default)

    def _get_int_param(self, key, default):
        value = self._get_param(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _sanitize_number(self, number):
        return re.sub(r"[^0-9+*#]", "", number or "")

    def _recording_format(self):
        recording_format = (self._get_param("real_estate_asterisk.recording_format", "wav") or "wav").lower()
        return recording_format if recording_format in ("wav", "mp3") else "wav"

    def _recording_root(self):
        configured = self._get_param("real_estate_asterisk.recording_dir")
        default_root = os.path.join(tools.config["data_dir"], "real_estate_voip", "recordings")
        return os.path.realpath(os.path.expanduser(configured or default_root))

    def is_recording_path_allowed(self, path):
        if not path:
            return False
        real_path = os.path.realpath(os.path.expanduser(path))
        root = self._recording_root()
        return real_path == root or real_path.startswith(root + os.sep)

    def prepare_recording_path(self, call):
        call.ensure_one()
        recording_root = self._recording_root()
        start_time = fields.Datetime.to_datetime(call.start_time or fields.Datetime.now())
        safe_number = re.sub(r"[^0-9A-Za-z]+", "", call.customer_number or "customer") or "customer"
        filename = "lead-%s-call-%s-%s.%s" % (
            call.lead_id.id or "none",
            call.id,
            safe_number[-16:],
            self._recording_format(),
        )
        return os.path.join(recording_root, start_time.strftime("%Y"), start_time.strftime("%m"), start_time.strftime("%d"), filename)

    def _ami_read_message(self, sock):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", errors="replace")

    def _ami_send_action(self, sock, headers):
        payload = "".join("%s: %s\r\n" % (key, value) for key, value in headers) + "\r\n"
        sock.sendall(payload.encode("utf-8"))
        return self._ami_read_message(sock)

    def _ami_response_value(self, response, key):
        key = key.lower()
        for line in response.splitlines():
            if ":" not in line:
                continue
            line_key, line_value = line.split(":", 1)
            if line_key.strip().lower() == key:
                return line_value.strip()
        return False

    def originate_call(self, call):
        call.ensure_one()
        host = self._get_param("real_estate_asterisk.ami_host")
        username = self._get_param("real_estate_asterisk.ami_username")
        secret = self._get_param("real_estate_asterisk.ami_secret")
        context = self._get_param("real_estate_asterisk.outbound_context", "realestate-outbound")
        endpoint_template = self._get_param("real_estate_asterisk.endpoint_template", "PJSIP/{extension}")
        port = self._get_int_param("real_estate_asterisk.ami_port", 5038)
        timeout = self._get_int_param("real_estate_asterisk.originate_timeout", 30)

        if not host or not username or not secret:
            raise UserError(_("Asterisk AMI host, username, and secret must be configured in CRM settings."))
        if not call.agent_id.asterisk_extension:
            raise UserError(_("The assigned agent must have an Asterisk extension configured on the user profile."))

        customer_number = self._sanitize_number(call.customer_number)
        if not customer_number:
            raise UserError(_("The customer number is empty or invalid."))

        recording_path = call.recording_path or self.prepare_recording_path(call)
        os.makedirs(os.path.dirname(recording_path), exist_ok=True)
        action_id = "realestate-%s-%s" % (call.id, uuid.uuid4().hex[:8])
        channel = endpoint_template.format(
            extension=call.agent_id.asterisk_extension,
            agent=call.agent_id.asterisk_extension,
            user=call.agent_id.login,
        )
        caller_id = call.agent_id.asterisk_caller_id or self._get_param("real_estate_asterisk.default_caller_id") or call.agent_id.name

        call.write({
            "ami_action_id": action_id,
            "customer_number": customer_number,
            "recording_path": recording_path,
            "recording_format": self._recording_format(),
            "start_time": call.start_time or fields.Datetime.now(),
            "status": "queued",
        })

        variables = [
            ("Variable", "REALESTATE_CALL_ID=%s" % call.id),
            ("Variable", "REALESTATE_LEAD_ID=%s" % call.lead_id.id),
            ("Variable", "REALESTATE_AGENT_ID=%s" % call.agent_id.id),
            ("Variable", "REALESTATE_RECORDING_PATH=%s" % recording_path),
            ("Variable", "MIXMONITOR_FILENAME=%s" % recording_path),
        ]
        originate_headers = [
            ("Action", "Originate"),
            ("ActionID", action_id),
            ("Channel", channel),
            ("Context", context),
            ("Exten", customer_number),
            ("Priority", "1"),
            ("CallerID", caller_id),
            ("Timeout", str(timeout * 1000)),
            ("Async", "true"),
            ("Account", "realestate-%s" % call.id),
        ] + variables

        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                self._ami_read_message(sock)
                login_response = self._ami_send_action(sock, [
                    ("Action", "Login"),
                    ("Username", username),
                    ("Secret", secret),
                    ("Events", "off"),
                ])
                if self._ami_response_value(login_response, "Response") != "Success":
                    raise UserError(_("Asterisk AMI login failed: %s") % (self._ami_response_value(login_response, "Message") or login_response))

                originate_response = self._ami_send_action(sock, originate_headers)
                if self._ami_response_value(originate_response, "Response") != "Success":
                    raise UserError(_("Asterisk originate failed: %s") % (self._ami_response_value(originate_response, "Message") or originate_response))

                self._ami_send_action(sock, [("Action", "Logoff")])
        except UserError:
            call.write({"status": "failed", "end_time": fields.Datetime.now()})
            raise
        except OSError as error:
            _logger.exception("Asterisk AMI connection failed")
            call.write({"status": "failed", "end_time": fields.Datetime.now()})
            raise UserError(_("Could not connect to Asterisk AMI: %s") % error) from error

        call.write({"status": "ringing"})
        call.message_post(body=_("Asterisk originate request sent. Action ID: %s") % action_id)
        return True
