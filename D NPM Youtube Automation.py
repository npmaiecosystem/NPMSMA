from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from langchain_core.prompts import PromptTemplate
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload
from googleapiclient.discovery import build
from PySide6.QtWidgets import QProgressBar
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QWidget
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QThread
from PySide6.QtCore import Signal
from moviepy import VideoFileClip
from npmai import Ollama
import whisper
import torch
import sys
import os

##########################################################################################READY FOR AUTOMATION######################################################################################

def app_dir():
    # Works for normal run + PyInstaller exe
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def app_path(filename):
    return os.path.join(app_dir(), filename)

########################E#######################################################
class AutomationWorker(QThread):
    log = Signal(str)
    progress = Signal(int)
    finished = Signal(str)

    def __init__(self, video_path, thumbnail_path):
        super().__init__()
        self.video_path = video_path
        self.thumbnail_path = thumbnail_path

    def run(self):
        self.log.emit("Extracting audio...")
        clip = VideoFileClip(self.video_path)
        audio = clip.audio
        clip.close()
        temp_audio=app_path("temp.wav")
        audio.write_audiofile(temp_audio,logger=None)
        
        self.log.emit("Transcribing audio (Whisper)...")
        os.environ["XDG_CACHE_HOME"]=app_dir()
        model = whisper.load_model("tiny")
        result = model.transcribe(temp_audio)
        text = result["text"]
        self.log.emit(text)

        if os.path.exists(temp_audio):
            os.remove(temp_audio)
        
        self.log.emit("Generating AI metadata...")
        llm = Ollama(
            model="llama3.2",
            temperature=0.8
            )

        desc_prompt = PromptTemplate(
            input_variables=["d"],
            template="Write a YouTube description for this video: {d}"
        )
        hash_prompt = PromptTemplate(
            input_variables=["d"],
            template="Generate YouTube hashtags for this video: {d}"
        )
        title_prompt = PromptTemplate(
            input_variables=["d"],
            template="Generate a short viral YouTube title for this content:{d}"
        )

        description = llm.invoke(desc_prompt.format(d=text))
        hashtags = llm.invoke(hash_prompt.format(d=text))
        title = llm.invoke(title_prompt.format(d=text))
        tags = [t.strip("#") for t in hashtags.split() if t.startswith("#")]
        self.log.emit("Starting YouTube upload...")
        try:
            video_id = upload_video(
                file_path=self.video_path,
                description=description,
                tags=tags,
                title=title,
                thumbnail_path=self.thumbnail_path,
                log=self.log.emit,
                progress=self.progress.emit
                )
            self.finished.emit(f"Upload complete | Video ID: {video_id}")
        except FileNotFoundError as e:
            self.log.emit(str(e))
            return
        
#######################################################################################UPLOAD#######################################################################################################

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

CLIENT_SECRETS_FILE = app_path("client_secrets.json")
TOKEN_FILE = app_path("token.json")

def get_youtube_service(log):
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        log("Loaded existing YouTube credentials")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("Refreshing credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(
                    "client_secrets.json not found.\n"
                    "Place it in the same folder as the app and restart."
                )

            log("Opening browser for YouTube login...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
            log("Credentials saved")

    return build("youtube", "v3", credentials=creds)


def upload_video(
    file_path,
    description,
    tags,
    title,
    thumbnail_path,
    log,
    progress
):
    youtube = get_youtube_service(log)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22"
        },
        "status": {"privacyStatus": "public"}
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    log("Uploading video...")
    response = None

    while response is None:
        status, response = request.next_chunk()
        if status:
            progress(int(status.progress() * 100))

    log("Video uploaded successfully")

    youtube.thumbnails().set(
        videoId=response["id"],
        media_body=thumbnail_path
    ).execute()

    log("Thumbnail uploaded")
    return response["id"]

####################################################################################DESKTOP APP#####################################################################################################

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NPM YouTube Automation ")

        self.layout = QVBoxLayout()

        self.video_label = QLabel("No video selected")
        self.thumb_label = QLabel("No thumbnail selected")

        self.video_btn = QPushButton("Select Video")
        self.thumb_btn = QPushButton("Select Thumbnail")
        self.start_btn = QPushButton("Start Automation")

        self.progress_bar = QProgressBar()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        self.layout.addWidget(self.video_label)
        self.layout.addWidget(self.video_btn)
        self.layout.addWidget(self.thumb_label)
        self.layout.addWidget(self.thumb_btn)
        self.layout.addWidget(self.start_btn)
        self.layout.addWidget(self.progress_bar)
        self.layout.addWidget(self.log_box)

        self.setLayout(self.layout)

        self.video_btn.clicked.connect(self.select_video)
        self.thumb_btn.clicked.connect(self.select_thumbnail)
        self.start_btn.clicked.connect(self.start)

        self.video_path = None
        self.thumbnail_path = None

    def select_video(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "", "Video Files (*.mp4 *.mov)"
        )
        if file:
            self.video_path = file
            self.video_label.setText(f"Video: {file}")

    def select_thumbnail(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Select Thumbnail", "", "Image Files (*.jpg *.png)"
        )
        if file:
            self.thumbnail_path = file
            self.thumb_label.setText(f"Thumbnail: {file}")

    def start(self):
        if not self.video_path or not self.thumbnail_path:
            self.log_box.append("Select video and thumbnail first")
            return

        self.worker = AutomationWorker(
            self.video_path,
            self.thumbnail_path
        )
        self.worker.log.connect(self.log_box.append)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(self.log_box.append)
        self.worker.start()

#######################################################################################################MAIN#########################################################################################
app = QApplication(sys.argv)
window = App()
window.resize(600, 500)
window.show()
sys.exit(app.exec())
