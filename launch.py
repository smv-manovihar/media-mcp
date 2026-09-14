import subprocess
import os
import sys
import socket
import time

# Ensure we're in the media-mcp directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def is_port_open(host, port):
    """Check if a port is open on the given host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        return result == 0

# Launch File Ops Server (Terminal 1 equivalent)
print("Launching File Ops Server...")
file_ops = subprocess.Popen([sys.executable, "-m", "servers.file_ops"])

# Launch Web Search Server (Terminal 2 equivalent)
print("Launching Web Search Server...")
search_web = subprocess.Popen([sys.executable, "-m", "servers.search_web"])

# Wait for servers to be ready
print("Waiting for servers to start...")
while not (is_port_open('localhost', 8000) and is_port_open('localhost', 8001)):
    time.sleep(1)

print("Servers are ready. Launching Streamlit app...")
# Launch Client App with Streamlit (Terminal 3 equivalent)
streamlit = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py"])

# Keep the script running to monitor processes (optional: add error handling or wait)
try:
    subprocess.Popen.wait(
        file_ops
    )  # Wait for any to finish, or remove to run indefinitely
except KeyboardInterrupt:
    print("Terminating processes...")
    file_ops.terminate()
    search_web.terminate()
    streamlit.terminate()
