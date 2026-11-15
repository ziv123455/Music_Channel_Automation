from flask import Flask, jsonify, render_template
from visualizer import bp as visualizer_blueprint
import shutil

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 380 * 1024 * 1024
app.register_blueprint(visualizer_blueprint)

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify(ready=bool(shutil.which("ffmpeg")))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
