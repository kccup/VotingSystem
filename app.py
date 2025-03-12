from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import qrcode
import os
import sqlite3

app = Flask(__name__)
app.config['SECRET_KEY'] = 'banana-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*") # Enables real-time updates

# Create static folder if it doesn't exist
if not os.path.exists('static'):
    os.makedirs('static')

def init_db():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS votes(
            option_name TEXT PRIMARY KEY,
            total_votes INTEGER NOT NULL
        )
    ''')
    # Initialize columns if empty
    for option in ["Option A", "Option B", "Option C"]:
        c.execute("INSERT OR IGNORE INTO votes(option_name, total_votes) VALUES(?, ?)", (option, 0))
    conn.commit()
    conn.close()

# Call init_db() before starting the server
init_db()

@app.route("/")
def home():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT option_name, total_votes FROM votes")
    votes = dict(c.fetchall())
    conn.close()
    return render_template("index.html", votes=votes)

@app.route("/vote", methods=["POST"])
def vote():
    data = request.get_json()
    option = data.get("option")
    if option:
        conn = sqlite3.connect('voting.db')
        c = conn.cursor()
        c.execute("UPDATE votes SET total_votes = total_votes + 1 WHERE option_name = ?", (option,))
        conn.commit()
        c.execute("SELECT option_name, total_votes FROM votes")
        updated_votes = dict(c.fetchall())
        conn.close()
        socketio.emit("update_votes", updated_votes)
        return jsonify({"success": True, "votes": updated_votes})
    return jsonify({"success": False, "error": "Invalid option"}), 400

@app.route("/get_votes")
def get_votes():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT option_name, total_votes FROM votes")
    votes = dict(c.fetchall())
    conn.close()
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
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("UPDATE votes SET total_votes = 0")
    conn.commit()
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    socketio.emit("update_votes", updated_votes)
    return jsonify({"success": True, "votes": updated_votes})

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)