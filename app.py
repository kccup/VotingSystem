from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import qrcode
import os

app = Flask(__name__)
socketio = SocketIO(app) # Enables real-time updates

# Fake database to store votes (you can use SQlite or Firebase instead)
votes = {"Option A": 0, "Option B": 0, "Option C": 0}

@app.route("/")
def home():
    return render_template("index.html", votes=votes)

@app.route("/vote", methods=["POST"])
def vote():
    data = request.get_json()
    option = data.get("option")

    if option in votes:
        votes[option] += 1
        socketio.emit("update_votes", votes) # Send real-time updates to clients
        return jsonify({"success": True, "votes": votes})
    
    return jsonify({"success": False, "error": "Invalid option"}), 400

@app.route("/generate_qr")
def generate_qr():
    url = request.host_url
    qr = qrcode.make(url)
    qr_path = os.path.join("static", "qrcode.png")
    qr.save(qr_path)
    return jsonify({"qr_code": qr_path})

if __name__ == "__main__":
    socketio.run(app, debug=True)