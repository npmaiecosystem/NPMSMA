from npmai import Ollama
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from flask import Flask,session,request,render_template,url_for,redirect
import requests
import json
import time
import os
import io

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ac7b18cb163c8dc0640da6f661b2731e021b695d6b3019933e0920e44d6b8666")

@app.route("/")
def index():
  return render_template("NPM_Youtube_Automation.html")

def local_video(file_path,thumbnail):
  files=file_path
  thumbnail=thumbnail
  HF_API="https://sonuramashish22028704-npmeduai.hf.space/ingestion"
  res=requests.post(HF_API,files,timeout=600)
  response=str(res)
  text=response
  llm = Ollama(model="mistral:7b",temperature=0.5)
  descriptionp = PromptTemplate(
            input_variables=["video_d"],
            template="""Hey you are a social media manager and you have to write the description about a video that you are going to uplaod note:note: please only write description no reply of contexts and you have these informations:{video_d}"""
            )
  hashtagsp = PromptTemplate(
            input_variables=["video_c"],
            template="""Hey you are a social media manager and you have to write the hastags that can be used in this video to rank and viral note: please only write hashtags no reply of contexts and you have these informations:{video_c}"""
            )
  titlep = PromptTemplate(
            input_variables=["t"],
            template="""Hey you are a social media manager and you have to write the title as per above contex{t} and de not respond for anything just generate a short title"""
            )
  descriptionr = descriptionp.format(
            video_d=text
            )

  hashtagsr = hashtagsp.format(
            video_c=text
            )

  titler = titlep.format(
            t="t"
            )

  resultd = llm.invoke(descriptionr)
  resulth = llm.invoke(hashtagsr)
  resultt = llm.invoke(titler)

  return resultd,resulth,resultt,thumbnail,files

@app.route("/login")
def login():
        flow = Flow.from_client_secrets_file(
            "credentials.json",
            scopes=["https://www.googleapis.com/auth/youtube.force-ssl"]
            )
        flow.redirect_uri = url_for('https://npmyt.onrender.com/callback', _external=True)

        authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
        session['state'] = state
        return redirect(authorization_url)

@app.route("/callback")
def callback():
        flow = Flow.from_client_secrets_file(
            "credentials.json",
            scopes=["https://www.googleapis.com/auth/youtube.force-ssl"],
            state=session['state']
            )
        flow.redirect_uri = url_for('callback', _external=True)
        flow.fetch_token(authorization_response=request.url)

        creds = flow.credentials
        session['user_creds'] = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
            }
        return "Authenticated! You can now trigger your AI upload logic."

def build_service():
        """Helper to create the 'youtube' object from saved session tokens"""
        if 'user_creds' not in session:
            return None
        creds = Credentials(**session['user_creds'])
        return build("youtube", "v3", credentials=creds)

class Youtube_Video_Upload:
    def __init__(self,file_path,description,tags,title,thumbnail_path):
        self.file_path=file_path
        self.description=description
        self.tags=tags
        self.title=title
        self.thumbnail_path=thumbnail_path

    def upload_video(self):
        self.youtube = build_service()

        self.body = {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": "22"
                },
            "status": {"privacyStatus": "public"}
            }
        self.media = MediaFileUpload(self.file_path, chunksize=-1, resumable=True)

        self.request = self.youtube.videos().insert(
            part="snippet,status",
            body=self.body,
            media_body=self.media
            )

        print("\nVideo is getting uploaded...")

        self.response = None
        while self.response is None:
            self.status, self.response = self.request.next_chunk()
            if self.status:
                print("Uploaded:", int(self.status.progress() * 100), "%")

        print("\nUpload Complete!")
        print("Video ID:", self.response["id"])

        self.thumbnail=self.youtube.thumbnails().set(
            videoId=self.response["id"],
            media_body=MediaFileUpload(self.thumbnail_path)
            ).execute()


@app.route("/send",methods=["POST"])
def send():
  resultdr,resulthr,resulttr,thumbnailr,filesr=local_video(request.form.get("video"),request.form.get("thumbnail"))
  
  uploader= Youtube_Video_Upload(
      file_path=filesr,
      description=resultdr,
      tags=[resulthr],
      title=resulttr,
      thumbnail_path=thumbnailr
      )
  uploader.upload_video()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
