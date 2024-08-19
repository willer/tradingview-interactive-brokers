import json
import redis, sqlite3, time, os, hashlib, math
from flask import Flask, render_template, request, g, current_app, redirect, url_for, session
from datetime import datetime, time as dt_time
import random
from flask_sqlalchemy import SQLAlchemy
import configparser

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trade.db'
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Add this line
db = SQLAlchemy(app)

# Load user credentials from config file
config = configparser.ConfigParser()
config.read('config.ini')
USER_CREDENTIALS = config['users']

# Import create_dash_app after db is initialized
from dash_app import create_dash_app
create_dash_app(app, db)

r = redis.Redis(host='localhost', port=6379, db=0)
p = r.pubsub()
p.subscribe('health')
p.get_message(timeout=3)

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect('trade.db')
        g.db.row_factory = sqlite3.Row

    return g.db

# initial setup of db (if it doesn't exist)
conn = sqlite3.connect('trade.db')
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, 
        ticker,
        order_action,
        order_contracts,
        order_price,
        order_message text
    )
""")
conn.commit()

# migrations for db, if you have older schemas
cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE signals ADD COLUMN order_message text")
    conn.commit()
except: pass

cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE signals ADD COLUMN bot text")
    conn.commit()
except: pass

cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE signals ADD COLUMN market_position text")
    conn.commit()
except: pass

cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE signals ADD COLUMN market_position_size text")
    conn.commit()
except: pass


@app.context_processor
def add_imports():
    # Note: we only define the top-level module names!
    return dict(hashlib=hashlib, time=time, os=os, math=math)

## ROUTES

# New function to check if user is logged in
def is_logged_in():
    return session.get('logged_in', False)

# New login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

# New logout route
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# Modify existing routes to require login
@app.route('/')
def index():
    if not is_logged_in():
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.get('/dashboard')
def dashboard():
    if not is_logged_in():
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT datetime(timestamp, 'localtime') as timestamp,
        ticker,
        bot,
        order_action,
        order_contracts,
        market_position,
        market_position_size,
        order_price,
        order_message
        FROM signals
        order by timestamp desc
        LIMIT 500
    """)
    signals = cursor.fetchall()
    #hashlib.sha1(row['order_message'])

    return render_template('dashboard.html', signals=signals, sha1=hashlib.sha1)

def is_dangerous_time():
    now = datetime.now().time()
    return (dt_time(8, 25) <= now <= dt_time(10, 0)) or (dt_time(12, 0) <= now <= dt_time(16, 0))

@app.route('/confirm_action', methods=['GET'])
def confirm_action():
    if not is_logged_in():
        return redirect(url_for('login'))
    action = request.args.get('action')
    params = request.args.get('params')
    x = random.randint(-10, 30)
    y = random.randint(-10, 30)
    return render_template('confirm_action.html', action=action, params=params, x=x, y=y)

# POST /resend?hash=xxx
@app.post('/resend')
def resend():
    if not is_logged_in():
        return redirect(url_for('login'))
    if is_dangerous_time():
        return redirect(url_for('confirm_action', action='resend', params=request.form.get("hash")))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT order_message
        FROM signals
        order by timestamp desc
    """)
    signals = cursor.fetchall()
    for row in signals:
        if isinstance(row["order_message"], str):
            sha1hash = hashlib.sha1(row["order_message"].encode('utf-8')).hexdigest()
        else:
            sha1hash = hashlib.sha1(row["order_message"]).hexdigest()
        if request.form.get("hash") == sha1hash:
            r.publish('tradingview', row["order_message"])
            return "<html><body>Found it!<br><br><a href=/>Back to Home</a></body></html>"
    return "<html><body>Didn't find it!<br><br><a href=/>Back to Home</a></body></html>"

# POST /order
@app.post('/order')
def order():
    if not is_logged_in():
        return redirect(url_for('login'))
    if is_dangerous_time():
        params = f"{request.form.get('direction')},{request.form.get('ticker')}"
        return redirect(url_for('confirm_action', action='order', params=params))

    direction = request.form.get("direction")
    ticker = request.form.get("ticker")

    position_size = 1000000
    if direction == "flat":
        position_size = 0
    # special case for futures, for now
    if direction != "flat":
        if ticker == "NQ1!" or ticker == "ES1!" or ticker == "GC1!":
            position_size = 1

    # Message to send to the broker
    broker_message = {
        "ticker": ticker.upper(),
        "strategy": {
            "bot": "live",  # Send 'live' to the broker
            "market_position": direction,
            "market_position_size": position_size,
        }
    }
    r.publish('tradingview', json.dumps(broker_message))

    # Log the manual activity in the signals table
    db = get_db()
    cursor = db.cursor()
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Message to log in the database
    log_message = {
        "ticker": ticker.upper(),
        "strategy": {
            "bot": "human",  # Log as 'human' in the database
            "market_position": direction,
            "market_position_size": position_size,
        }
    }
    
    cursor.execute("""
        INSERT INTO signals (timestamp, ticker, bot, market_position, market_position_size, order_price, order_message) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (current_time, 
          ticker.upper(), 
          "human",
          direction,
          position_size,
          "N/A",  # Placeholder for price
          json.dumps(log_message)))
    db.commit()

    return f"<html><body>Order submitted and logged!<br>{log_message}<br><a href=/>Back to Home</a></body></html>"

@app.route('/execute_action', methods=['POST'])
def execute_action():
    if not is_logged_in():
        return redirect(url_for('login'))
    action = request.form.get('action')
    params = request.form.get('params')

    if action == 'resend':
        return resend_action(params)
    elif action == 'order':
        direction, ticker = params.split(',')
        return order_action(direction, ticker)

def resend_action(hash_value):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT order_message
        FROM signals
        order by timestamp desc
    """)
    signals = cursor.fetchall()
    for row in signals:
        if isinstance(row["order_message"], str):
            sha1hash = hashlib.sha1(row["order_message"].encode('utf-8')).hexdigest()
        else:
            sha1hash = hashlib.sha1(row["order_message"]).hexdigest()
        if hash_value == sha1hash:
            r.publish('tradingview', row["order_message"])
            return "<html><body>Found it!<br><br><a href=/>Back to Home</a></body></html>"
    return "<html><body>Didn't find it!<br><br><a href=/>Back to Home</a></body></html>"

def order_action(direction, ticker):
    position_size = 1000000
    if direction == "flat":
        position_size = 0
    # special case for futures, for now
    if direction != "flat":
        if ticker == "NQ1!" or ticker == "ES1!" or ticker == "GC1!":
            position_size = 1

    # Message to send to the broker
    broker_message = {
        "ticker": ticker.upper(),
        "strategy": {
            "bot": "live",  # Send 'live' to the broker
            "market_position": direction,
            "market_position_size": position_size,
        }
    }
    r.publish('tradingview', json.dumps(broker_message))

    # Log the manual activity in the signals table
    db = get_db()
    cursor = db.cursor()
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Message to log in the database
    log_message = {
        "ticker": ticker.upper(),
        "strategy": {
            "bot": "human",  # Log as 'human' in the database
            "market_position": direction,
            "market_position_size": position_size,
        }
    }
    
    cursor.execute("""
        INSERT INTO signals (timestamp, ticker, bot, market_position, market_position_size, order_price, order_message) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (current_time, 
          ticker.upper(), 
          "human",
          direction,
          position_size,
          "N/A",  # Placeholder for price
          json.dumps(log_message)))
    db.commit()

    return f"<html><body>Order submitted and logged!<br>{log_message}<br><a href=/>Back to Home</a></body></html>"

# POST /webhook
@app.post("/webhook")
def webhook():
    data = request.data

    if data:
        r.publish('tradingview', data)

        #print('got message: ' + request.get_data())

        data_dict = request.json

        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO signals (ticker, bot, order_action, order_contracts, market_position, market_position_size, order_price, order_message) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (data_dict['ticker'], 
                data_dict['strategy']['bot'],
                data_dict['strategy']['order_action'], 
                data_dict['strategy']['order_contracts'],
                data_dict['strategy']['market_position'],
                data_dict['strategy']['market_position_size'],
                data_dict['strategy']['order_price'],
                request.get_data()))
        db.commit()

        return data

    return {"code": "success"}

# GET /health
@app.get("/health")
def health():

    # send a message to the redis channel to test connectivity
    r.publish('tradingview', 'health check')
    # check if we got a response 
    message = p.get_message(timeout=15)
    if message and message['type'] == 'message':
        return {"code": "success"}

    if message != None:
        return {"code": "failure", "message-type": message['type'], "message": message['data']}, 500
    else:
        return {"code": "failure", "message-type": "timeout", "message": "no message received"}, 500

# Modify these routes to require login
@app.post("/stop-backend")
def stop_backend():
    if not is_logged_in():
        return redirect(url_for('login'))
    # find the broker processes and kill them
    os.system("pkill -f start-broker-live.sh")
    os.system("pkill -f broker.py")

    return "<html><body>Done<br><br><a href=/>Back to Home</a></body></html>"

@app.post("/start-backend")
def start_backend():
    if not is_logged_in():
        return redirect(url_for('login'))
    # find the broker processes and kill them
    os.system("pkill -f start-broker-live.sh")
    os.system("pkill -f broker.py")

    # start the broker processes in the background
    if os.system("sh start-broker-live.sh &") != 0:
        return "<html><body>Failed to start IBKR broker<br><br><a href=/>Back to Home</a></body></html>"

    return "<html><body>Done<br><br><a href=/>Back to Home</a></body></html>"

# GET /show-logs-ibkr?tail=xxx
@app.get("/show-logs-ibkr")
def show_logs_ibkr():
    if not is_logged_in():
        return redirect(url_for('login'))
    tail = request.args.get("tail")
    if tail == None:
        tail = 100
    else:
        tail = int(tail)

    fname = "start-broker-ibkr-mac-live.sh.log"
    if os.path.exists(fname):
        with open(fname) as f:
            # read the last n lines
            lines = f.readlines()
            lines = lines[-tail:]
            lines = "".join(lines)
            return f"<html><body><h1>IBKR Broker Logs</h1><br><br><a href=/>Back to Home</a><br><br><pre>{lines}</pre></body></html>"
    else:
        return "<html><body>File not found<br><br><a href=/>Back to Home</a></body></html>"

# GET /show-logs-alpaca?tail=xxx
@app.get("/show-logs-alpaca")
def show_logs_alpaca():
    if not is_logged_in():
        return redirect(url_for('login'))
    tail = request.args.get("tail")
    if tail == None:
        tail = 100
    else:
        tail = int(tail)

    fname = "start-broker-alpaca-mac.sh.log"
    if os.path.exists(fname):
        with open(fname) as f:
            # read the last n lines
            lines = f.readlines()
            lines = lines[-tail:]
            lines = "".join(lines)
            return f"<html><body><h1>alpaca Broker Logs</h1><br><br><a href=/>Back to Home</a><br><br><pre>{lines}</pre></body></html>"
    else:
        return "<html><body>File not found<br><br><a href=/>Back to Home</a></body></html>"

@app.route('/dashboard')
def dashboard_page():
    if not is_logged_in():
        return redirect(url_for('login'))
    return redirect('/dashboard/')

if __name__ == '__main__':
    app.run(debug=True)