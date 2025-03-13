from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO
from functools import wraps
import qrcode
import os
import sqlite3

app = Flask(__name__)
app.config['SECRET_KEY'] = 'banana-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Admin credentials - in a real app you'd store these securely, hashed in a database
ADMIN_USERNAME = "admin"  
ADMIN_PASSWORD = "minion123"

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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
    # Only initialize with default options if table is empty
    c.execute("SELECT COUNT(*) FROM votes")
    count = c.fetchone()[0]
    if count == 0:
        # Initial example options
        for option in ["Option A", "Option B", "Option C"]:
            c.execute("INSERT INTO votes(option_name, total_votes) VALUES(?, ?)", (option, 0))
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

# Protect reset votes route
@app.route("/reset", methods=["POST"])
@login_required
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

@app.route("/contestants", methods=["GET"])
def get_contestants():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT option_name FROM votes ORDER BY option_name")
    contestants = [row[0] for row in c.fetchall()]
    conn.close()
    return jsonify({"contestants": contestants})

@app.route("/contestants/add", methods=["POST"])
def add_contestant():
    data = request.get_json()
    name = data.get("name")
    if not name or not name.strip():
        return jsonify({"success": False, "error": "Invalid contestant name"}), 400
        
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO votes(option_name, total_votes) VALUES(?, 0)", (name,))
        conn.commit()
        c.execute("SELECT option_name, total_votes FROM votes")
        updated_votes = dict(c.fetchall())
        conn.close()
        socketio.emit("update_votes", updated_votes)
        return jsonify({"success": True, "votes": updated_votes})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Contestant already exists"}), 400

@app.route("/contestants/remove", methods=["POST"])
def remove_contestant():
    data = request.get_json()
    name = data.get("name")
    if not name:
        return jsonify({"success": False, "error": "Invalid contestant name"}), 400
        
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("DELETE FROM votes WHERE option_name = ?", (name,))
    conn.commit()
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    socketio.emit("update_votes", updated_votes)
    return jsonify({"success": True, "votes": updated_votes})

# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin'))
        else:
            error = "Invalid credentials. Please try again."
    return render_template("login.html", error=error)

# Logout route
@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('home'))

# Protect admin route with login_required decorator
@app.route("/admin")
@login_required
def admin():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT option_name, total_votes FROM votes ORDER BY option_name")
    votes = dict(c.fetchall())
    conn.close()
    return render_template("admin.html", votes=votes)

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)