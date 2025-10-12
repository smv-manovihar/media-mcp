import subprocess
import os
import sys

# Ensure we're in the media-mcp directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Launch File Ops Server (Terminal 1 equivalent)
file_ops = subprocess.Popen([sys.executable, "-m", "servers.file_ops"])

# Launch Web Search Server (Terminal 2 equivalent)
search_web = subprocess.Popen([sys.executable, "-m", "servers.search_web"])

# Launch Client App with Streamlit (Terminal 3 equivalent)
streamlit = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py"])

# Keep the script running to monitor processes (optional: add error handling or wait)
try:
    subprocess.Popen.wait(
        file_ops
    )  # Wait for any to finish, or remove to run indefinitely
except KeyboardInterrupt:
    file_ops.terminate()
    search_web.terminate()
    streamlit.terminate()
