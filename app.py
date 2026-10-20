from flask import Flask, jsonify, render_template, request
from main import process_message_with_metadata

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    message = data.get("message")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "A message is required."}), 400

    try:
        result = process_message_with_metadata(message.strip(), data.get("history"))
        return jsonify(result)
    except Exception:
        app.logger.exception("Failed to process chat message")
        return jsonify({"error": "Ennoic couldn't process that message."}), 500

if __name__ == "__main__":
    app.run(debug=True)
