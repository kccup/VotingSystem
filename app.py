from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import qrcode
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'banana-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*") # Enables real-time updates

# Create static folder if it doesn't exist
if not os.path.exists('static'):
    os.makedirs('static')

# Fake database to store votes (you can use SQLite or Firebase instead)
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

@app.route("/get_votes")
def get_votes():
    return jsonify(votes)

@app.route("/generate_qr")
def generate_qr():
    url = request.host_url
    qr = qrcode.make(url)
    qr_path = os.path.join("static", "qrcode.png")
    qr.save("static/qrcode.png")  # Make sure the path exists
    return jsonify({"qr_code": "/" + qr_path})  # Add leading slash for proper URL

@app.route("/reset", methods=["POST"])
def reset_votes():
    global votes
    votes = {"Option A": 0, "Option B": 0, "Option C": 0}
    socketio.emit("update_votes", votes)
    return jsonify({"success": True, "votes": votes})

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)