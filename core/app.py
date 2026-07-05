from fastapi import Request, FastAPI, BackgroundTasks, UploadFile, File, Form
from moviepy.editor import VideoFileClip
from supabase import create_client
from typing import Annotated
from npmai import Ollama,Rag
import requests
import asyncio
import uuid
import os

app = FastAPI()

SUPABASE_URL= os.environ["SUPABASE_URL"]
SUPABASE_KEY= os.environ["SUPABASE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.post("/")
def health():
    return {"app":"healthy"}

@app.post("/data-entry")
def data_reciever(
    video_path: UploadFile,
    auth_code_yt=None,
    auth_code_fb=None,
    auth_code_ig=None,
    auth_code_tk=None,
    auth_code_ld=None,
    auth_code_td=None,
    thumbnail= None
):

    
    if not video_path:
        return {"response":"Upload Video without Video and you cannot successfully hit API"}
        
    HF_API="https://sonuramashish22028704-npmeduai.hf.space/ingestion"
    res=requests.post(HF_API,files,timeout=1200)
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

    final_text= f"{resultt}\n{resultd}\n{resulth}"

    read_file = video_path.read()
    supa_storage = (
        supabase.storage
        .from_("NPMSMAVIDEODB")
        .upload(
            file=read_file,
            path=f"for_urls/{video_path.filename}",
            file_options={"upsert": "false"}
        )
    )
    
    video_public_url = (
        supabase.storage
        .from_("NPMSMAVIDEODB")
        .get_public_url(f"for_urls/{video_path.filename}"
                       )
    )

    if auth_code_fb is not None:
        BackgroundTasks.add_task(
            facebook,
            auth_code=auth_code_fb,
            video_path=video_path,
            title=resultt,
            description=f"{resultd}\n{resulth}"
        )
    
    if auth_code_ig is not None:
        BackgroundTasks.add_task(
            instagram,
            auth_code=auth_code_ig,
            video_path=video_public_url,
            caption= final_text
        )
        
    if auth_code_ld is not None:
        BackgroundTasks.add_task(
            linkedin,
            auth_code=auth_code_ld,
            supabase_url=video_public_url,
            title= f"{resultt}\n{resultd}\n{resulth}"
        )
        
    if auth_code_td is not None:
        BackgroundTasks.add_task(
            thread,
            auth_code=auth_code_td,
            video_url=video_public_url,
            text= final_text
        )
        
    if auth_code_tk is not None:
        BackgroundTasks.add_task(
            tiktok,
            auth_code=auth_code_tk,
            supabase_video_url=video_public_url,
            title=final_text
        )
        
    if auth_code_yt is not None:
        if thumbnail is not None:
            BackgroundTasks.add_task(youtube,video_path,auth_code=auth_code_yt)
        else:
            BackgroundTasks.add_task(youtube,video_path,auth_code=auth_code_yt)
        


"""https://www.facebook.com?
  client_id=YOUR_APP_ID
  &redirect_uri=YOUR_REDIRECT_URI
  &scope=pages_show_list,pages_read_engagement,pages_manage_posts,publish_video
  &response_type=code
"""
def facebook(auth_code,video_path,title,description):
    clip = VideoFileClip(video_path)
    width, height = clip.size
    clip.close()
    vertical = height > width 
    
    url_1 = "https://graph.facebook.com"
    params_1 = {
        "client_id": "APP_ID",
        "client_secret": "APP_SECRET",
        "redirect_uri": "REDIRECT_URI",
        "code": auth_code
    }
    
    res_1 = requests.get(url_1, params=params_1).json()
    short_token = res_1.get('access_token')

    params_2 = {
        "grant_type": "fb_exchange_token",
        "client_id": "APP_ID",
        "client_secret": "APP_SECRET",
        "fb_exchange_token": short_token
    }
    
    res_2 = requests.get(url_1, params=params_2).json() 
    long_user_token = res_2.get('access_token')

    accounts_url = "https://graph.facebook.com"
  
    params_3 = {
        "access_token": long_user_token,
    }
    
    res_3 = requests.get(accounts_url, params=params_3).json()

    pages_found = []
    for page in res_3.get('data', []):
        page_info = {
            "name": page['name'],
            "id": page['id'],
            "access_token": page['access_token']
        }
        
        pages_found.append(page_info)
    
    file_name = os.path.basename(video_path)
    file_size = os.path.getsize(video_path)
    file_type = "video/mp4"
    
    page_acc_token_key = pages_found[0]
    acc_token = page_acc_token_key["access_token"]

    page_id = page_acc_token_key["id"]

    init_url = f"https://graph.facebook.com/{APP_ID}/uploads"
    init_payload = {
        'file_name': file_name,
        'file_length': file_size,
        'file_type': file_type,
        'access_token': acc_token
    }
    
    init_res = requests.post(init_url, data=init_payload).json()
    session_id = init_res.get('id') 

    upload_url = f"https://graph.facebook.com/{session_id}"
    headers = {
        "Authorization": f"OAuth {acc_token}",
        "file_offset": "0" 
    }
    
    with open(video_path, 'rb') as video:
      upload_res = requests.post(upload_url, headers=headers, data=video).json()
    
    file_handle = upload_res.get('h') 

    is_reel = vertical
    
    if is_reel:
        publish_url_r = f"https://graph.facebook.com/{page_id}/video_reels"
        
        publish_payload = {
            "access_token": acc_token,
            "upload_phase": "finish",
            "video_state": "PUBLISHED",
            "description": description, 
            "video_id": file_handle
        }
        
        final_res = requests.post(publish_url_r, json=publish_payload).json()
        return final_res

    else:
        publish_url = f"https://graph-video.facebook.com/{page_id}/videos"
        publish_payload = {
            'access_token': acc_token,
            'title': title,
            'description': description,
            'fbuploader_video_file_chunk': file_handle
        }
        
        final_res = requests.post(publish_url, data=publish_payload).json()
        return final_res


"""https://www.facebook.com?
  client_id=YOUR_APP_ID
  &redirect_uri=YOUR_REDIRECT_URI
  &scope=instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement
  &response_type=code
"""
def instagram(auth_code,caption,video_path):
    url = "https://api.instagram.com/oauth/access_token"
    payload = {
        "client_id": "INSTA_APP_ID",
        "client_secret": "INSTA_APP_SECRET",
        "grant_type": "authorization_code",
        "redirect_uri": "REDIRECT_URI",
        "code": auth_code
    }
    res = requests.post(url, data=payload).json()
    short_token = res.get('access_token')
    user_id = res.get("user_id")
    
    INSTA_GRAPH_URL = "https://graph.instagram.com/v23.0"
    
    container_url = f"{INSTA_GRAPH_URL}/{ig_user_id}/media"
    
    payload = {
        "media_type": "REELS", 
        "video_url": video_path,
        "caption": caption,
        "access_token": token
    }
    
    container_res = requests.post(container_url, data=payload).json()
    creation_id = container_res.get('id')
    
    asyncio.sleep(30) 
    
    publish_url = f"{INSTA_GRAPH_URL}/{ig_user_id}/media_publish"
    
    publish_payload = {
        "creation_id": creation_id,
        "access_token": token
    }
    
    final_res = requests.post(publish_url, data=publish_payload).json()
    return final_res

"""https://www.linkedin.com?
  response_type=code
  &client_id=YOUR_CLIENT_ID
  &redirect_uri=YOUR_REDIRECT_URI
  &scope=w_member_social,openid,profile
"""
def linkedin(auth_code,supabase_url,title):
    url = "https://www.linkedin.com/oauth/v2/accessToken"
    payload = {
        "grant_type": "authorization_code",
        "code": auth_code, 
        "client_id": "YOUR_LINKEDIN_CLIENT_ID",
        "client_secret": "YOUR_LINKEDIN_CLIENT_SECRET",
        "redirect_uri": "https://yourplatform.com"
    }

    res = requests.post(url, data=payload).json()
    token = res.get('access_token')

    url = "https://api.linkedin.com/v2/userinfo" 
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(url, headers=headers).json()
    person_urn = f"urn:li:person:{res['sub']}" 

    register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers = {"Authorization": f"Bearer {token}", "X-Restli-Protocol-Version": "2.0.0"}
    
    register_payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
            "owner": person_urn,
            "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}]
        }
    }
    reg_res = requests.post(register_url, headers=headers, json=register_payload).json()
    
    upload_url = reg_res['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
    asset_id = reg_res['value']['asset']

    video_stream = requests.get(supabase_url, stream=True).raw
    requests.put(upload_url, data=video_stream, headers={"Authorization": f"Bearer {token}"})

    post_url = "https://api.linkedin.com/v2/ugcPosts"
    post_payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "media":asset_id,
                "shareCommentary": {"text": title},
                "shareMediaCategory": "VIDEO"
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    }
    post = requests.post(post_url, headers=headers, json=post_payload).json()
    return post

"""https://www.threads.net?
  client_id=YOUR_APP_ID
  &redirect_uri=YOUR_REDIRECT_URI
  &scope=threads_basic,threads_content_publish
  &response_type=code
"""
def thread(auth_code,video_url, text):
    THREADS_AUTH_URL = "https://graph.threads.net/oauth/access_token"
    THREADS_BASE_URL = "https://graph.threads.net/v1.0"
    
    params = {
        "client_id": "THREADS_APP_ID",
        "client_secret": "THREADS_APP_SECRET",
        "grant_type": "authorization_code",
        "redirect_uri": "REDIRECT_URI",
        "code": auth_code
    }
    
    res = requests.post(THREADS_AUTH_URL, data=params).json()
    token = res.get('access_token')
    threads_user_id = res.get("user_id")
    
    container_url = f"{THREADS_BASE_URL}/{threads_user_id}/threads"
    payload = {
        "media_type": "VIDEO",
        "video_url": video_url,
        "text": text,
        "access_token": token
    }
    container_res = requests.post(container_url, data=payload).json()
    container_id = container_res.get('id')

    asyncio.sleep(30)

    publish_url = f"{THREADS_BASE_URL}/{threads_user_id}/threads_publish"
    final_res = requests.post(publish_url, data={
        "creation_id": container_id,
        "access_token": token
    })
    return final_res.json()

"""https://www.tiktok.com?
  client_key=YOUR_CLIENT_KEY
  &scope=user.info.basic,video.upload,video.publish
  &redirect_uri=YOUR_REDIRECT_URI
  &response_type=code
"""
def tiktok(auth_code,supabase_video_url,title):
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    payload = {
        "client_key": "TIKTOK_CLIENT_KEY",
        "client_secret": "TIKTOK_CLIENT_SECRET",
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": "REDIRECT_URI"
    }
    
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    res = requests.post(url, data=payload, headers=headers).json()

    user_id = res.get("open_id")
    access_token = res.get("access_token")

    url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    
    # TikTok's JSON structure is very strict
    payload = {
        "source": "PULL_FROM_URL",
        "video_url": supabase_video_url,
        "post_info": {
            "title": title,
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False,
            "video_cover_timestamp_ms": 1000
        }
    }

    response = requests.post(url, json=payload, headers=headers)
    
    return response.json()
