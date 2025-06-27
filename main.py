from app import app
import threading
import os
import time
from flask import request, jsonify

# Configurable timeout in seconds (10 minutes default)
SHUTDOWN_TIMEOUT = int(os.environ.get("SHUTDOWN_TIMEOUT", 600))
SHUTDOWN_KEY = os.environ.get("SHUTDOWN_KEY", "secret-shutdown-key")
last_activity_time = time.time()

@app.before_request
def update_last_activity():
    global last_activity_time
    last_activity_time = time.time()

@app.route("/shutdown", methods=["POST"])
def shutdown():
    if request.headers.get("X-SHUTDOWN-KEY") != SHUTDOWN_KEY:
        return jsonify({"error": "Unauthorized"}), 403
    shutdown_server()
    return jsonify({"message": "Server shutting down..."})

def shutdown_server():
    func = request.environ.get("werkzeug.server.shutdown")
    if func:
        func()

def monitor_idle_time():
    while True:
        time.sleep(60)
        if time.time() - last_activity_time > SHUTDOWN_TIMEOUT:
            print("Server idle timeout reached. Shutting down...")
            with app.test_request_context():
                shutdown_server()
            break

if __name__ == "__main__":
    # Start idle monitor in background
    threading.Thread(target=monitor_idle_time, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
