from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import qrcode
import os
import sqlite3
import uuid
import time

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
        password = request.form['password']
        
        # Hash the password before storing
        hashed_password = generate_password_hash(password)
        
        conn = sqlite3.connect('voting.db')
        c = conn.cursor()
        
        # Check if username already exists
        c.execute("SELECT username FROM users WHERE username = ?", (username,))
        if c.fetchone() is not None:
            conn.close()
            error = "Username already taken. Please choose another."
        else:
            c.execute("INSERT INTO users(username, password, has_voted) VALUES(?, ?, 0)", 
                     (username, hashed_password))
            conn.commit()
            conn.close()
            session['voter_username'] = username
            return redirect(url_for('home'))
    
    return render_template("register.html", error=error)

# User login
@app.route("/voter/login", methods=["GET", "POST"])
def voter_login():
    return redirect(url_for('login'))

# User logout
@app.route("/voter/logout")
def voter_logout():
    session.pop('voter_username', None)
    return redirect(url_for('home'))

# Modified vote route to allow changing votes
@app.route("/vote", methods=["POST"])
def vote():
    # Check if user has verified event access
    if 'event_verified' not in session or 'voter_id' not in session:
        return jsonify({"success": False, "error": "Please enter event access code"}), 403
    
    voter_id = session['voter_id']
    
    # Get the new vote option
    data = request.get_json()
    new_option = data.get("option")
    if not new_option:
        return jsonify({"success": False, "error": "Invalid option"}), 400
    
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    
    # Check if user has already voted and for which option
    c.execute("SELECT has_voted, voted_for FROM temp_voters WHERE id = ?", (voter_id,))
    user_vote = c.fetchone()
    
    if not user_vote:
        conn.close()
        return jsonify({"success": False, "error": "Invalid session"}), 401
    
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
            c.execute("UPDATE temp_voters SET voted_for = ? WHERE id = ?", (new_option, voter_id))
    else:
        # Mark user as voted and record their choice
        c.execute("UPDATE temp_voters SET has_voted = 1, voted_for = ? WHERE id = ?", (new_option, voter_id))
        
        # Increment new choice
        c.execute("UPDATE votes SET total_votes = total_votes + 1 WHERE option_name = ?", (new_option,))
    
    conn.commit()
    
    # Get updated votes
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    
    # Emit an event specifically about this user's vote
    socketio.emit("vote_cast", {
        "voter_id": voter_id,
        "nickname": session.get('nickname'),
        "option": new_option
    })
    
    # Emit a specific event for temp voter votes
    if 'voter_id' in session:
        socketio.emit("temp_voter_voted", {
            "voter_id": voter_id, 
            "nickname": session.get('nickname'),
            "option": new_option
        })
    
    # Also emit the overall vote update
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
    
    # Reset registered user voting status
    c.execute("UPDATE users SET has_voted = 0, voted_for = NULL")
    
    # Also reset temp_voters voting status
    c.execute("UPDATE temp_voters SET has_voted = 0, voted_for = NULL")
    
    conn.commit()
    
    # Get updated votes
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    
    # Emit socket event to update all clients
    socketio.emit("update_votes", updated_votes)
    socketio.emit("votes_reset", True)  # Signal that votes were reset
    
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
        socketio.emit('contestant_added', {
            'name': name,
            'votes': 0
        })
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
    socketio.emit('contestant_removed', {'name': name})
    return jsonify({"success": True, "votes": updated_votes})

# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        login_type = request.form.get('login_type', '')
        
        conn = sqlite3.connect('voting.db')
        c = conn.cursor()
        
        if login_type == 'admin':
            # Admin login logic
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session['logged_in'] = True
                session['is_admin'] = True
                conn.close()
                return redirect(url_for('admin'))
            else:
                error = 'Invalid administrator credentials'
        else:
            # Voter login logic
            c.execute("SELECT * FROM users WHERE username = ?", (username,))
            user = c.fetchone()
            
            if user:
                # Check if password is already hashed (contains $ signs)
                if user[1] and '$' in str(user[1]):
                    # Password is hashed, use check_password_hash
                    password_matches = check_password_hash(user[1], password)
                else:
                    # Password is stored as plain text, compare directly
                    password_matches = (user[1] == password)
                
                if password_matches:
                    session['voter_username'] = username
                    conn.close()
                    return redirect(url_for('home'))
            
            error = 'Invalid username or password'
        
        conn.close()
    
    return render_template("login.html", error=error)

# Logout route
@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    session.pop('is_admin', None)
    session.pop('voter_username', None)
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
    if 'event_verified' in session and 'voter_id' in session:
        return redirect(url_for('home'))
        
    if request.method == "POST":
        code = request.form.get("access_code")
        nickname = request.form.get("nickname", "").strip()
        
        # Validate nickname
        if not nickname or len(nickname) < 2:
            return render_template("event_access.html", error="Please enter a valid nickname (at least 2 characters)")
        
        # Simple single code for the entire event
        if code == "CLUBEVENT2025":  # Your event code
            # Generate a unique session ID for this user
            voter_id = str(uuid.uuid4())
            session['event_verified'] = True
            session['voter_id'] = voter_id
            session['nickname'] = nickname
            
            # Store in temporary voters table
            conn = sqlite3.connect('voting.db')
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS temp_voters
                      (id TEXT PRIMARY KEY, nickname TEXT, joined_at INTEGER, 
                      has_voted INTEGER DEFAULT 0, voted_for TEXT DEFAULT NULL)''')
            
            c.execute("INSERT INTO temp_voters VALUES (?, ?, ?, 0, NULL)", 
                     (voter_id, nickname, int(time.time())))
            conn.commit()
            conn.close()
            
            socketio.emit('participant_joined', {
                'id': voter_id, 
                'nickname': nickname,
                'joined_at': int(time.time())
            })
            
            return redirect(url_for('home'))
        else:
            return render_template("event_access.html", error="Invalid event code")
            
    # Display the access form for GET requests
    return render_template("event_access.html")

@app.route("/clear-event-access", methods=["POST"])
def clear_event_access():
    # Remove event verification from session
    session.pop('event_verified', None)
    return jsonify({"success": True})

@app.route("/get_users")
@login_required
def get_users():
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT username, has_voted, voted_for FROM users ORDER BY username")
    users = []
    for row in c.fetchall():
        users.append({
            "username": row[0],
            "has_voted": row[1],
            "voted_for": row[2]
        })
    conn.close()
    return jsonify(users)

@app.route("/reset_user_vote", methods=["POST"])
@login_required
def reset_user_vote():
    data = request.get_json()
    username = data.get("username")
    
    if not username:
        return jsonify({"success": False, "error": "No username provided"})
    
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    
    # Get the user's current vote
    c.execute("SELECT voted_for FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    
    if user and user[0]:
        # Decrease vote count for the option they voted for
        c.execute("UPDATE votes SET total_votes = total_votes - 1 WHERE option_name = ?", (user[0],))
        
    # Reset the user's voting status
    c.execute("UPDATE users SET has_voted = 0, voted_for = NULL WHERE username = ?", (username,))
    
    conn.commit()
    
    # Get updated votes
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    
    socketio.emit("update_votes", updated_votes)
    socketio.emit('user_vote_reset', {'username': username})
    
    return jsonify({"success": True})

@app.route("/remove_user", methods=["POST"])
@login_required
def remove_user():
    # Check if user is admin
    if 'logged_in' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    # Get username from request
    data = request.get_json()
    username = data.get("username")
    
    if not username:
        return jsonify({"success": False, "error": "Invalid request"}), 400
    
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    
    try:
        # Get user's current vote
        c.execute("SELECT has_voted, voted_for FROM users WHERE username = ?", (username,))
        result = c.fetchone()
        
        if not result:
            return jsonify({"success": False, "error": "User not found"}), 404
            
        has_voted, voted_for = result
        
        # If user had voted, decrement the vote count
        votes_changed = False
        if has_voted and voted_for:
            c.execute("UPDATE votes SET total_votes = total_votes - 1 WHERE option_name = ?", (voted_for,))
            votes_changed = True
        
        # Delete the user
        c.execute("DELETE FROM users WHERE username = ?", (username,))
        
        conn.commit()
        
        # Get updated votes if needed
        updated_votes = None
        if votes_changed:
            c.execute("SELECT option_name, total_votes FROM votes")
            updated_votes = dict(c.fetchall())
            # Emit websocket event for real-time updates
            socketio.emit("update_votes", updated_votes)
        
        return jsonify({
            "success": True, 
            "votesChanged": votes_changed,
            "votes": updated_votes
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@app.route("/clear-session", methods=["POST"])
def clear_session():
    # Store the voter_id to remove from the database
    voter_id = session.get('voter_id')
    
    if voter_id:
        # Connect to database
        conn = sqlite3.connect('voting.db')
        c = conn.cursor()
        
        try:
            # Check if user has voted
            c.execute("SELECT has_voted, voted_for FROM temp_voters WHERE id = ?", (voter_id,))
            user_vote = c.fetchone()
            
            if user_vote and user_vote[0] == 1 and user_vote[1]:
                # Decrement vote count for the option they voted for
                c.execute("UPDATE votes SET total_votes = total_votes - 1 WHERE option_name = ?", (user_vote[1],))
            
            # Remove user from temp_voters
            c.execute("DELETE FROM temp_voters WHERE id = ?", (voter_id,))
            conn.commit()
            
            # Emit websocket event about participant leaving
            socketio.emit('participant_removed', {'id': voter_id})
            
        except Exception as e:
            conn.rollback()
            print(f"Error in clear_session: {e}")
        finally:
            conn.close()
    
    # Clear all session data
    session.clear()
    
    # Return empty response with 204 No Content status
    return ('', 204)

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['is_admin'] = True
            return redirect(url_for('admin'))
        else:
            error = 'Invalid administrator credentials'
    
    return render_template("admin_login.html", error=error)

@app.route("/get_temp_voters")
@login_required
def get_temp_voters():
    if 'is_admin' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    c.execute("SELECT id, nickname, joined_at, has_voted, voted_for FROM temp_voters ORDER BY joined_at DESC")
    
    voters = []
    for row in c.fetchall():
        voters.append({
            "id": row[0],
            "nickname": row[1],
            "joined_at": row[2],
            "has_voted": bool(row[3]),
            "voted_for": row[4]
        })
    
    conn.close()
    return jsonify({"success": True, "voters": voters})

@app.route("/reset_temp_vote", methods=["POST"])
@login_required
def reset_temp_vote():
    if 'is_admin' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.get_json()
    voter_id = data.get("voter_id")
    
    if not voter_id:
        return jsonify({"success": False, "error": "Invalid voter ID"}), 400
    
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    
    # Get the voter's current vote
    c.execute("SELECT voted_for FROM temp_voters WHERE id = ? AND has_voted = 1", (voter_id,))
    result = c.fetchone()
    
    if not result:
        conn.close()
        return jsonify({"success": False, "error": "Voter has not voted or does not exist"}), 404
        
    voted_for = result[0]
    
    # Decrement vote count
    c.execute("UPDATE votes SET total_votes = total_votes - 1 WHERE option_name = ?", (voted_for,))
    
    # Reset voter's status
    c.execute("UPDATE temp_voters SET has_voted = 0, voted_for = NULL WHERE id = ?", (voter_id,))
    
    conn.commit()
    
    # Get updated votes
    c.execute("SELECT option_name, total_votes FROM votes")
    updated_votes = dict(c.fetchall())
    conn.close()
    
    socketio.emit("update_votes", updated_votes)
    socketio.emit('temp_vote_reset', {'voter_id': voter_id})
    
    return jsonify({"success": True, "votes": updated_votes})

@app.route("/remove_temp_voter", methods=["POST"])
@login_required
def remove_temp_voter():
    if 'is_admin' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.get_json()
    voter_id = data.get("voter_id")
    
    if not voter_id:
        return jsonify({"success": False, "error": "Invalid voter ID"}), 400
    
    conn = sqlite3.connect('voting.db')
    c = conn.cursor()
    
    # Get the voter's current vote
    c.execute("SELECT has_voted, voted_for FROM temp_voters WHERE id = ?", (voter_id,))
    result = c.fetchone()
    
    if not result:
        conn.close()
        return jsonify({"success": False, "error": "Voter not found"}), 404
        
    has_voted, voted_for = result
    
    votes_changed = False
    if has_voted and voted_for:
        # Decrement vote count
        c.execute("UPDATE votes SET total_votes = total_votes - 1 WHERE option_name = ?", (voted_for,))
        votes_changed = True
    
    # Remove the voter
    c.execute("DELETE FROM temp_voters WHERE id = ?", (voter_id,))
    
    conn.commit()
    
    # Get updated votes if needed
    updated_votes = None
    if votes_changed:
        c.execute("SELECT option_name, total_votes FROM votes")
        updated_votes = dict(c.fetchall())
        socketio.emit("update_votes", updated_votes)
    
    conn.close()
    
    socketio.emit('participant_removed', {'id': voter_id})
    
    return jsonify({
        "success": True,
        "votesChanged": votes_changed,
        "votes": updated_votes
    })

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)