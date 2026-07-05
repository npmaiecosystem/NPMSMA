from flask import Flask, request, make_response, jsonify, render_template
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)  # allow your HTML frontend to call Flask

DATA_ENTRY_URL = "https://sonuramashishnpm-npmsma.hf.space/data-entry"

@app.route("/")
def render():
    return render_template("index.html")
    
@app.route("/auth/<platform>")
def oauth_callback(platform):
    code  = request.args.get("code", "")
    state = request.args.get("state", "")
    error = request.args.get("error", "")

    if error or not code:
        html = f"""
        <html><body style="background:#0f172a;color:#ef4444;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:1rem;">
          <div style="font-size:3rem;">❌</div>
          <p>Authorization failed for {platform}. You can close this window.</p>
          <script>
            window.opener && window.opener.postMessage(
              {{ platform: '{platform}', code: null, error: '{error}', state: '{state}' }}, '*'
            );
            setTimeout(() => window.close(), 2000);
          </script>
        </body></html>
        """
    else:
        html = f"""
        <html>
        <head><style>
          body {{ background:#0f172a;color:#1ed760;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:1rem; }}
        </style></head>
        <body>
          <div style="font-size:3rem;">✅</div>
          <p>{platform.capitalize()} connected! Closing window...</p>
          <script>
            // Send auth code back to main page (parent window)
            window.opener && window.opener.postMessage(
              {{ platform: '{platform}', code: '{code}', state: '{state}' }}, '*'
            );
            setTimeout(() => window.close(), 1500);
          </script>
        </body>
        </html>
        """

    res = make_response(html)
    res.headers['Content-Type'] = 'text/html'
    return res
  
@app.route("/upload", methods=["POST"])
def upload():
    # Map frontend platform names → HuggingFace param names
    key_map = {
        "facebook":  "auth_code_fb",
        "instagram": "auth_code_ig",
        "thread":    "auth_code_td",
        "linkedin":  "auth_code_ld",
        "tiktok":    "auth_code_tk",
        "youtube":   "auth_code_yt",
    }

    forward_data = {}
    forward_files = {}

    if "video" not in request.files:
        return jsonify({"error": "No video file received"}), 400

    video = request.files["video"]
    forward_files["video_path"] = (video.filename, video.read(), video.content_type)

    if "thumbnail" in request.files:
        thumb = request.files["thumbnail"]
        forward_files["thumbnail"] = (thumb.filename, thumb.read(), thumb.content_type)

    for frontend_key, hf_key in key_map.items():
        code = request.form.get("auth_code_" + frontend_key)
        if code:
            forward_data[hf_key] = code

    if not forward_data:
        return jsonify({"error": "No platform auth codes received"}), 400

    try:
        hf_response = requests.post(
            DATA_ENTRY_URL,
            data=forward_data,
            files=forward_files,
            timeout=1200  # video upload can take time
        )
        return jsonify(hf_response.json()), hf_response.status_code

    except requests.exceptions.Timeout:
        return jsonify({"error": "HuggingFace request timed out"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000, debug=True)
