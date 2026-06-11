import os
import base64
import tempfile
import whisper
import librosa
from odoo import models, fields, api
from odoo.exceptions import UserError

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # Text field jahan transcript save hoga
    transcribed_text = fields.Text(string="Audio Transcript", readonly=True)
    
    # Binary field jahan user audio file upload karega
    audio_file = fields.Binary(string="Upload Call Audio")
    audio_file_name = fields.Char(string="Audio File Name")

    def action_transcribe_audio(self):
        self.ensure_one()
        import whisper
        import librosa
        import base64
        import tempfile
        import os
        if not self.audio_file:
            raise UserError("Meharbani karke pehle audio file upload karein!")

        # 1. Binary data ko decode karna
        audio_binary_data = base64.b64decode(self.audio_file)

        # 2. Dynamic Temporary file banana (Bina hardcoded paths ke)
        # Yeh Windows/Linux dono par khud hi temporary directory utha lega
        suffix = os.path.splitext(self.audio_file_name)[1] if self.audio_file_name else '.mp3'
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio_file:
            temp_audio_file.write(audio_binary_data)
            temp_file_path = temp_audio_file.name

        try:
            # 3. Whisper Model load karna
            model = whisper.load_model("base")

            # 4. Librosa se safely audio array read karna (FFmpeg dependency bypass karne ke liye)
            audio_array, sr = librosa.load(temp_file_path, sr=16000)

            # 5. Transcribe karna
            result = model.transcribe(audio_array, fp16=False)

            # 6. Dynamic tarike se record ke andar text save karna
            self.transcribed_text = result.get("text", "").strip()

        except Exception as e:
            raise UserError(f"Transcription ke dauran masla hua: {str(e)}")

        finally:
            # 7. Kaam khatam hone par temporary file ko delete karna
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)