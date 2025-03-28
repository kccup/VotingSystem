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
    # Create votes table
    c.execute('''
        CREATE TABLE IF NOT EXISTS votes(
            option_name TEXT PRIMARY KEY,
            total_votes INTEGER NOT NULL
        )
    ''')
    
    # Create users table with voted_for column
    c.execute('''
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            has_voted INTEGER DEFAULT 0,
            voted_for TEXT DEFAULT NULL
        )
    ''')
    
    # Check if voted_for column exists, add it if not
    try:
        c.execute("SELECT voted_for FROM users LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE users ADD COLUMN voted_for TEXT DEFAULT NULL")
    
    # Only initialize with default options if votes table is empty
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
    # Redirect to access code page if not verified
    if 'event_verified' not in session:
        return redirect(url_for('event_access'))
        
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT option_name, total_votes FROM votes")
    votes = dict(c.fetchall())
    
    has_voted = False
    if 'voter_username' in session:
        username = session['voter_username']
        c.execute("SELECT has_voted FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        has_voted = user[0] == 1 if user else False
    
    conn.close()
    return render_template("index.html", votes=votes, has_voted=has_voted)

# User registration
@app.route("/voter/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password'] # In production, always hash passwords!
        
        conn = sqlite3.connect('voting.db')
        c = conn.cursor()
        
        # Check if username already exists
        c.execute("SELECT username FROM users WHERE username = ?", (username,))
        if c.fetchone() is not None:
            conn.close()
            error = "Username already taken. Please choose another."
        else:
            c.execute("INSERT INTO users(username, password, has_voted) VALUES(?, ?, 0)", 
                     (username, password))
            conn.commit()
            conn.close()
            session['voter_username'] = username
            return redirect(url_for('home'))
    
    return render_template("register.html", error=error)

# User login
@app.route("/voter/login", methods=["GET", "POST"])
def voter_login():
    error = None
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect('voting.db')
        c = conn.cursor()
        c.execute("SELECT password FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        
        if user is None or user[0] != password:
            error = "Invalid username or password."
        else:
            session['voter_username'] = username
            conn.close()
            return redirect(url_for('home'))
        
        conn.close()
    
    return render_template("voter_login.html", error=error)

# User logout
@app.route("/voter/logout")
def voter_logout():
    session.pop('voter_username', None)
    return redirect(url_for('home'))

# Modified vote route to allow changing votes
@app.route("/vote", methods=["POST"])
def vote():
    # Check if user has verified event access
    if 'event_verified' not in session:
        return jsonify({"success": False, "error": "Please enter event access code"}), 403
    
    # Check if user is logged in
    if 'voter_username' not in session:
        return jsonify({"success": False, "error": "Please login to vote"}), 401
    
    username = session['voter_username']
    
    # Get the new vote option
    data = request.get_json()
    new_option = data.get("option")
    if not new_option:
        return jsonify({"success": False, "error": "Invalid option"}), 400
    
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    
    # Check if user has already voted and for which option
    c.execute("SELECT has_voted, voted_for FROM users WHERE username = ?", (username,))
    user_vote = c.fetchone()
    
    if user_vote[0] == 1 and user_vote[1] is not None:
        # User is changing their vote
        previous_option = user_vote[1]
        
        # Only process if they're actually changing their vote
        if previous_option != new_option:
            # Decrement previous choice
            c.execute("UPDATE votes SET total_votes = total_votes - 1 WHERE option_name = ?", (previous_option,))
            
            # Increment new choice
            c.execute("UPDATE votes SET total_votes = total_votes + 1 WHERE option_name = ?", (new_option,))
            
            # Update user's choice
            c.execute("UPDATE users SET voted_for = ? WHERE username = ?", (new_option, username))
    else:
        # Mark user as voted and record their choice
        c.execute("UPDATE users SET has_voted = 1, voted_for = ? WHERE username = ?", (new_option, username))
        
        # Increment new choice
        c.execute("UPDATE votes SET total_votes = total_votes + 1 WHERE option_name = ?", (new_option,))
    
    conn.commit()
    
    # Get updated votes
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    
    socketio.emit("update_votes", updated_votes)
    return jsonify({"success": True, "votes": updated_votes})

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
    # Get the host's IP address instead of using localhost
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    # Create URL with the IP address
    base_url = f"http://{local_ip}:5000"
    
    # Create QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(base_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save the image
    if not os.path.exists('static'):
        os.makedirs('static')
    
    import time
    timestamp = int(time.time())
    filename = f"qrcode_{timestamp}.png"
    filepath = os.path.join("static", filename)
    img.save(filepath)
    
    return jsonify({"qr_code": f"/static/{filename}"})

# Protect reset votes route
@app.route("/reset", methods=["POST"])
@login_required
def reset_votes():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    # Reset vote counts
    c.execute("UPDATE votes SET total_votes = 0")
    # Reset user voting status so they can vote again
    c.execute("UPDATE users SET has_voted = 0, voted_for = NULL")
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

@app.route("/event-access", methods=["GET", "POST"])
def event_access():
    # If user already verified, send them to voting
    if 'event_verified' in session:
        return redirect(url_for('home'))
        
    if request.method == "POST":
        code = request.form.get("access_code")
        # Simple single code for the entire event
        if code == "CLUBEVENT2025":  # You can change this for each event
            session['event_verified'] = True
            return redirect(url_for('home'))
        else:
            return render_template("event_access.html", error="Invalid code")
            
    # Display the access form for GET requests
    return render_template("event_access.html")

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)