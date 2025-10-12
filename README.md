# MediaMCP – Intelligent Local Media Management 🧠

Chat with your local photo and video library using the power of AI\! 🤖 MediaMCP combines local vision models for **privacy** 🛡️ with Groq's LLM API to create an intelligent, conversational media experience.

All heavy processing of your photos and videos happens on your machine, ensuring **total privacy**. The LLM acts as a smart orchestrator, understanding your **natural language commands** 🗣️ to search, organize, and manage your files.

-----

## Core Features ✨

  - 💬 **Conversational Search**: Find media with simple queries like *"show me sunset photos from last May"* 🌅 or *"find videos of my dog at the beach."* 🐶

  - ✨ **Semantic Similarity Search**: Pick a reference image 🖼️ or use a text description to instantly find the most visually similar items in your library.

  - 📂 **Natural-Language File Management**: Use simple commands to manage your files. Ask the agent to `move photos to a new folder`, `copy specific files`, or `create a directory structure`. The LLM translates your request into the correct file operations.

  - ⚙️ **Smart Ingestion Pipeline**: New media is automatically detected, scanned, and embedded 📥, making it immediately available for semantic search.

  - 🔒 **Privacy-First Architecture**: Image and video analysis, embedding generation (using **SigLIP**), and metadata extraction are performed **entirely on your device** 💻.

  - 🤔 **Transparent ReAct Agent**: The LLM "thinks" out loud, showing you which tools it's using and why. This gives you full visibility into the process without needing to learn any syntax.

  - 🛠️ **Extensible Toolset**: Easily add your own custom Python functions 🐍 and expose them to the AI agent to expand its capabilities.

-----

## How It Works 🗺️

MediaMCP uses a hybrid model that balances privacy and power:

1.  **💻 Local Processing**: A local AI vision model (**SigLIP**) scans your media files, generating vector embeddings. **Your files never leave your computer.**
2.  **🗣️ Language Understanding**: When you type a command, the text is sent to the Groq LLM API.
3.  **🤖 Reasoning & Tool Use**: The LLM uses a ReAct pattern to interpret your request and decide which local tool to use (e.g., `find_top_k_similar_images`, `move_file`).
4.  **⚙️ Execution**: The local server executes the command on your filesystem.
5.  **✅ Response**: The LLM observes the result, decides if the task is complete, and gives you a final response in the chat.

-----

## Project Roadmap 🚀

#### ✅ **Implemented**

  - 🗂️ **Basic File System Operations**: Create, read, delete, move, and copy files/folders via chat.
  - 🔄 **Incremental Media Scanning**: Automatically detect new, updated, or removed media.
  - 🔎 **Semantic Search**: Text-to-image and image-to-image similarity search (`top_k`).
  - 🤖 **Conversational Agent**: ReAct-based agent with visible reasoning steps.
  - 🌐 **Web Search & Scraping**: Ask questions that require web access.
  - 📸 **EXIF Data Extraction**: Automatically read metadata like date, time, and location.

#### 🔜 **In Progress & Future Goals**

  - 🗂️ **Automatic Semantic Organization**: The next major focus. Develop tools that can understand content and automatically suggest folder placements (e.g., grouping vacation photos by city).
  - ⚡ **Asynchronous Scanning & Processing**: Improve performance by moving the current synchronous scanning process to a background, asynchronous pipeline to keep the UI responsive, especially with large libraries.
  - 🎬 **Advanced Video Analysis**: Semantic video search, keyframe extraction, and scene detection.
  - 🎨 **Enhanced UI**: Add search filters, media previews, and a full browsing interface.
  - 🔌 **Custom Tool Plug-in System**: Streamline the process for users to add their own tools.

-----

## Getting Started 🏁

### 1\. Prerequisites ✅

  - [Git](https://git-scm.com/)
  - [Python 3.9+](https://www.python.org/downloads/)
  - [Groq API Key](https://console.groq.com/)

### 2\. Clone the Repository 📂

Open your terminal and run the following commands:

```bash
git clone https://github.com/smv-manovihar/media-mcp.git
cd media-mcp
```

### 3\. Install Dependencies with `uv` 📦

This project uses [`uv`](https://www.google.com/search?q=%5Bhttps://github.com/astral-sh/uv%5D\(https://github.com/astral-sh/uv\)), a fast Python package manager.

```bash
# First, install uv
pip install uv

# Then, install project dependencies
uv sync
```

### 4\. Configure Environment Variables 🔑

Create a `.env` file in the project root (`media-mcp/.env`). You can do this by copying the example file:

```bash
cp .env.example .env
```

Now, open the `.env` file and add your Groq API key:

```env
# Get your free key from https://console.groq.com/
GROQ_API_KEY="your_api_key_here"
```

### 5\. Run the Application 🚀

You have two ways to run the application.

#### Option 1: Easy Start (Recommended)

For a quick and easy start, use the new launch script. This will start all the necessary servers and the client application in a single terminal.

```bash
python launch.py
```

#### Option 2: Manual Launch (For Development)

If you are developing and need to see the logs for each process separately, it is recommended to run the servers and the client in three different terminals.

> **⚠️ Important**: Make sure you are in the `media-mcp` directory in each terminal.

| Terminal 1: File Ops Server  | Terminal 2: Web Search Server  | Terminal 3: Client App |
| ---------------------------- | ------------------------------ | ---------------------- |
| `python -m servers.file_ops` | `python -m servers.search_web` | `streamlit run app.py` |

-----

After running the application with either method, the **MediaMCP chat interface** will open in your browser at `http://localhost:8501`. 🎉

-----

## Contributing 🤝

Contributions are highly welcome\! Whether it's a bug fix, a new feature, or a documentation improvement, please feel free to open an issue or submit a pull request. 💖