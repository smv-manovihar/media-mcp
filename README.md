# MediaMCP - Intelligent Local Media Management 🧠

Chat with your local photo and document library using the power of AI! 🤖 MediaMCP combines local vision models for **privacy** 🛡️ with your choice of LLM provider (OpenAI, Anthropic, Google, Groq, OpenRouter, Ollama, or any OpenAI-compatible endpoint) to create an intelligent, conversational media experience.

Your library is indexed right on your machine for fast, private search. The LLM acts as a smart orchestrator, understanding your **natural language commands** 🗣️ to search, organize, and manage your files.

-----

## Core Features ✨

### 💬 Conversational Media Chat
  - Chat with your media library in plain language, no commands or syntax to learn
  - Past conversations are saved and searchable on your device
  - See the assistant's reasoning and which actions it took, step by step
  - Token usage shown for transparency on every reply
  - Attach files in chat, images preview inline, videos play inline
  - Duplicate uploads are detected automatically
  - Results show image grids with links to open or reveal files on your computer
  - Custom instructions and creativity control for the assistant

### 🔎 Semantic Image Search
  - Find photos by describing them, e.g. *"sunset photos"*
  - Find visually similar photos from a reference image
  - Filter photos by camera, location, size, or GPS availability
  - Inspect any photo for details: colors, camera info, location, and content tags, with optional AI description when Visual AI is enabled

### 📂 Natural-Language File Management
  - Browse, search, and get info about files and folders by asking in chat
  - Create folders, read documents, copy, move, rename, and delete files
  - Works only inside folders you explicitly allow, for safety
  - Reads common formats: PDFs, Word, Excel, PowerPoint, CSV, notebooks, archives, and code/text files
  - Detects and cleans up duplicate uploads
  - Exports your photo metadata to a spreadsheet file with filters

### 🖼️ Smart Media Indexing
  - New, changed, moved, or deleted files are detected automatically
  - Photos are indexed locally for fast similarity search
  - Photo details (camera, date, location) extracted automatically, including city/country lookup from GPS
  - Background scanning with progress and cancel, so the app stays responsive
  - Custom exclusion rules to skip folders or file types you don't want indexed

### 🌐 Web Search
  - Ask questions that need current or web knowledge
  - Get summarized answers with source links
  - Can read and summarize the content of a web page

### 🔌 Works With Your Choice of AI Provider
  - Supports OpenAI, Anthropic, Google, Groq, OpenRouter, local Ollama, or any compatible provider
  - Switch providers and models from the sidebar, with automatic model listing
  - Optional separate vision model for richer photo descriptions, otherwise photo analysis stays fully local

### 🔒 Privacy-First, With One Trade-off
  - Indexing, photo analysis, file operations, and chat history all stay on your device
  - Your chat text is sent to your chosen AI provider, along with any file content the assistant reads to answer you and any photo sent for a Visual AI description
  - For fully offline chat, use a local provider such as Ollama
  - For better privacy, use self hosted and local models so your content stays on your own machine

> **Heads up:** chat text plus any file content the assistant reads and any photo sent for a Visual AI description are shared with your chosen LLM provider. For the best privacy, use self hosted or local models.

-----

## Limitations ⚠️

  - Images only for content search, videos and audio can be managed and previewed but are not indexed by content, with no keyframe or scene detection
  - Slow initial scan, large libraries take time to index on first run, use local disk space for the index, and run faster with a GPU
  - Internet only needed for cloud AI providers, web search, and location name lookups, local hosting works fully offline
  - Text-based document reading, scanned files without selectable text, handwritten notes, and complex layouts may not read well
  - Single local user, chat history and indexes live on your machine, with no multi-user or cloud sync
  - AI-dependent accuracy, search results and file actions follow the model's interpretation, so complex requests may need rephrasing or confirmation

-----

## How It Works 🗺️

MediaMCP uses a hybrid model that balances privacy and power:

1.  **💻 Local Processing**: Your photos are analyzed on your computer and indexed for search. The index itself stays local.
2.  **🗣️ Language Understanding**: When you type a message, it is sent to your chosen AI provider, together with any file content or photo the assistant needs to read to answer you.
3.  **🤖 Reasoning & Tool Use**: The AI figures out which local actions to take, searching photos, managing files, or searching the web.
4.  **⚙️ Execution**: The actions run locally on your machine, inside your allowed folders.
5.  **✅ Response**: The AI reviews the results and replies in chat with previews, links, and an explanation.

-----

## Project Roadmap 🚀

#### ✅ **Implemented**

  - 🗂️ **File operations by chat**: browse, create, read, copy, move, rename, delete, upload deduplication, metadata export
  - 🔄 **Incremental media scanning**: detects new, updated, moved, or removed files in the background
  - 🔎 **Photo search**: search by description, by similar photo, or by camera/location filters
  - 🤖 **Conversational assistant**: explains its reasoning, keeps chat history, supports attachments and custom instructions
  - 🌐 **Web search**: answers with summaries and source links
  - 📸 **Photo details**: camera info, dates, and GPS-based location lookup
  - 📄 **Document reading**: PDFs, office files, spreadsheets, presentations, notebooks, archives, and code/text
  - 🔌 **Multiple AI providers**: OpenAI, Anthropic, Google, Groq, OpenRouter, Ollama, or custom endpoints, with optional vision model

#### 🔜 **In Progress & Future Goals**

  - 🗂️ **Automatic organization**: suggest folder placements by photo content, e.g. grouping vacation photos by city
  - 🎬 **Video understanding**: content search, keyframe extraction, and scene detection
  - 🔌 **Attach your own MCP servers**: connect external or custom MCP tools to extend what the assistant can do

-----

## Getting Started 🏁

### 1\. Prerequisites ✅

  - [Git](https://git-scm.com/)
  - [Python 3.11+](https://www.python.org/downloads/)
  - Large language model and multimodal LLM access (OpenAI, Anthropic, Google, Groq, or OpenRouter, or local [Ollama](https://ollama.com/) / any OpenAI-compatible endpoint)

### 2\. Clone the Repository 📂

Open your terminal and run the following commands:

```bash
git clone https://github.com/smv-manovihar/media-mcp.git
cd media-mcp
```

### 3\. Install Dependencies with `uv` 📦

This project uses `uv`, a fast Python package manager.

```bash
# First, install uv
pip install uv

# Then, install project dependencies
uv sync
```

### 4\. Run the Application 🚀

You have two ways to run the application.

#### Option 1: Easy Start (Recommended)

For a quick and easy start, use the launch script. This starts all necessary servers and the client application together in a single terminal:

```bash
uv run launch.py
# or: python launch.py
```

#### Option 2: Manual Launch (For Development)

If you are developing and need to see the logs for each process separately, run the servers and the client in three different terminals:

> **⚠️ Important**: Make sure you are in the project root directory in each terminal.

| Terminal 1: File Ops Server  | Terminal 2: Web Search Server  | Terminal 3: Client App |
| ---------------------------- | ------------------------------ | ---------------------- |
| `python -m servers.file_ops` | `python -m servers.search_web` | `streamlit run app.py` |

-----

The **MediaMCP chat interface** will open in your browser at `http://localhost:8501`. 🎉

-----

### 5\. Configure via UI / User Config ⚙️

No `.env` file is required! All settings and credentials are managed directly through the user configuration file (`config/config.json`) and the application sidebar:

1. **AI Provider & API Keys**: In the sidebar under **🔌 Provider & Credentials**, select your active provider (Google Gemini, Groq, OpenAI, Anthropic, OpenRouter, local Ollama, etc.) and enter your API key.
2. **Model Selection**: Choose your preferred chat model from the dynamically loaded list.
3. **Allowed Paths**: Add the local folders you want MediaMCP to access and index under **Allowed Directories**.
4. **Visual AI (Optional)**: Configure an optional vision model under **Visual AI (Image Inspection)** for enhanced photo descriptions.

All choices and keys are saved locally in `config/config.json`.

> **💡 Note**: Standard environment variables (e.g. `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`) are still supported as optional fallbacks if you prefer them.

-----

## Contributing 🤝

Contributions are highly welcome\! Whether it's a bug fix, a new feature, or a documentation improvement, please feel free to open an issue or submit a pull request. 💖