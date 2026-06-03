import json
import mimetypes
import os

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import content_disposition, request


class RealEstateRecordingController(http.Controller):

    @http.route(
        ["/real_estate/call/<int:call_id>/recording/play", "/real_estate/call/<int:call_id>/recording/download"],
        type="http",
        auth="user",
    )
    def recording(self, call_id, **kwargs):
        call = request.env["realestate.call"].browse(call_id).exists()
        if not call:
            return request.not_found()
        try:
            call.check_access("read")
        except AccessError:
            return request.not_found()

        path = call._ensure_recording_available()
        filename = os.path.basename(path)
        download = request.httprequest.path.endswith("/download")
        mimetype = mimetypes.guess_type(path)[0] or call.recording_mimetype or "application/octet-stream"
        headers = [
            ("Content-Type", mimetype),
            ("Content-Disposition", content_disposition(filename, disposition_type="attachment" if download else "inline")),
        ]
        with open(path, "rb") as recording_file:
            return request.make_response(recording_file.read(), headers=headers)

    @http.route("/real_estate/asterisk/event", type="http", auth="public", methods=["POST"], csrf=False)
    def asterisk_event(self, **kwargs):
        expected_token = request.env["ir.config_parameter"].sudo().get_param("real_estate_asterisk.webhook_token")
        supplied_token = request.httprequest.headers.get("X-RealEstate-Token") or kwargs.get("token")
        if not expected_token or supplied_token != expected_token:
            return request.make_json_response({"error": "unauthorized"}, status=403)

        payload = dict(kwargs)
        content_type = request.httprequest.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                payload.update(json.loads(request.httprequest.data.decode("utf-8") or "{}"))
            except json.JSONDecodeError:
                return request.make_json_response({"error": "invalid-json"}, status=400)

        call = request.env["realestate.call"].sudo()._update_from_asterisk_payload(payload)
        return request.make_json_response({"ok": True, "call_id": call.id if call else False})
