from flask import Flask, jsonify, render_template, request
from main import process_message

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "A message is required."}), 400

    try:
        response = process_message(message.strip())
        return jsonify({"reply": response})
    except Exception:
        app.longer.exception("Failed to process chat message")
        return jsonify({"error": "Ennoic couldn't process that message."}), 500

if __name__ == "__main__":
    app.run(debug=True)