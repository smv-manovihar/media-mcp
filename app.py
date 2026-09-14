import streamlit as st
from components.sidebar import manage_sidebar
from components.chat import display_greeting, display_chat_history, handle_user_input, sync_chat_session_url
from client.agent import init_agent, config_fingerprint
from config.settings import load_config

st.set_page_config(page_title="MediaMCP", page_icon="📁", layout="wide")

# Spacious layout width while preserving Streamlit's default chat input positioning
st.markdown(
    """
    <style>
    /* Expand main content container and bottom container to a comfortable width */
    [data-testid="stMainBlockContainer"],
    [data-testid="stBottomBlockContainer"] {
        max-width: min(1200px, 94vw) !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }

    /* Clean Sidebar Layout with optional Pinned Bottom Action */
    /* Base = Chats view (already OK): keep tight */
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
        display: flex !important;
        flex-direction: column !important;
        min-height: calc(100vh - 4.5rem) !important;
        padding-bottom: 1rem !important;
        gap: 0.35rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div {
        display: flex !important;
        flex-direction: column !important;
        flex: 1 1 auto !important;
        min-height: 100% !important;
        height: auto !important;
        gap: 0.35rem !important;
    }

    /* Tighten default stacking between header / toolbars / content */
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > div {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        padding-top: 0 !important;
    }
    [data-testid="stSidebar"] h3 {
        margin-bottom: 0.25rem !important;
        padding-bottom: 0 !important;
        line-height: 1.3 !important;
    }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        margin-top: 0 !important;
        margin-bottom: 0.2rem !important;
        line-height: 1.4 !important;
    }
    /* Settings views only (AI Config / Paths & Rules, detected via their
       Back buttons): looser Back -> subheader -> expander rhythm.
       Chats view is untouched. */
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_ai) [data-testid="stSidebarUserContent"],
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_media) [data-testid="stSidebarUserContent"],
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_ai) [data-testid="stSidebarUserContent"] > div,
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_media) [data-testid="stSidebarUserContent"] > div {
        gap: 0.6rem !important;
    }
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_ai) h3,
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_media) h3 {
        margin-top: 0.35rem !important;
        margin-bottom: 0.35rem !important;
    }
    .st-key-btn_back_from_ai,
    .st-key-btn_back_from_media {
        margin-bottom: 0.15rem !important;
    }
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_ai) details[data-testid="stExpander"],
    [data-testid="stSidebar"]:has(.st-key-btn_back_from_media) details[data-testid="stExpander"] {
        margin-top: 0.15rem !important;
    }

    /* Pushes ONLY the bottom action container to the bottom.
       NOTE: no `:last-child` fallback — that was pinning the last chat
       row to the bottom and creating the huge header/content gap. */
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > div:has(.st-key-sidebar_bottom_action) {
        margin-top: auto !important;
        position: sticky !important;
        bottom: 0 !important;
        padding-top: 1rem !important;
        padding-bottom: 0.5rem !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border-top: 1px solid rgba(255, 255, 255, 0.1) !important;
        z-index: 100 !important;
    }

    /* Remove any default box or background from the container */
    .st-key-sidebar_bottom_action {
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        margin: 0 !important;
        width: 100% !important;
    }

    /* --- Sidebar toolbars: stay usable when sidebar is dragged narrow --- */
    .st-key-sidebar_header_toolbar div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.4rem !important;
    }
    .st-key-sidebar_header_toolbar div[data-testid="stHorizontalBlock"] > div {
        flex: 1 1 110px !important;
        min-width: 90px !important;
    }
    .st-key-sidebar_header_toolbar button {
        width: 100% !important;
        min-height: 2.35rem !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    /* Back-to-chats buttons are full-width already; keep them left-aligned */
    .st-key-btn_back_from_ai button,
    .st-key-btn_back_from_media button {
        justify-content: flex-start !important;
        text-align: left !important;
        white-space: nowrap !important;
        overflow: hidden !important;
    }
    /* New-chat row: chat button flexes, cleanup button keeps a fixed tap target */
    .st-key-sidebar_newchat_toolbar div[data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
        gap: 0.4rem !important;
        align-items: center !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: visible !important;
        padding-right: 2px !important;
        box-sizing: border-box !important;
    }
    .st-key-sidebar_newchat_toolbar div[data-testid="stHorizontalBlock"] > div:first-child {
        flex: 1 1 auto !important;
        min-width: 0 !important;
        overflow: hidden !important;
    }
    .st-key-sidebar_newchat_toolbar div[data-testid="stHorizontalBlock"] > div:last-child {
        flex: 0 0 2.4rem !important;
        min-width: 2.4rem !important;
        width: 2.4rem !important;
        max-width: 2.4rem !important;
        overflow: visible !important;
        box-sizing: border-box !important;
    }
    /* NOTE: Streamlit nests <button> inside div.stButton inside the keyed
       container, so descendant (space) selectors are required — "> button"
       never matches. */
    .st-key-sidebar_newchat_toolbar .st-key-sidebar_btn_dedup_uploads {
        overflow: visible !important;
        width: 100% !important;
        box-sizing: border-box !important;
    }
    .st-key-sidebar_newchat_toolbar .st-key-sidebar_btn_dedup_uploads div[data-testid="stButton"] {
        width: 100% !important;
    }
    .st-key-sidebar_newchat_toolbar .st-key-sidebar_btn_dedup_uploads button {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        min-height: 2.35rem !important;
        height: 2.35rem !important;
        padding: 0 !important;
        margin: 0 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        overflow: visible !important;
        box-sizing: border-box !important;
        line-height: 1.5 !important;
        font-size: 1rem !important;
    }
    .st-key-sidebar_newchat_toolbar .st-key-sidebar_btn_dedup_uploads button div[data-testid="stMarkdownContainer"],
    .st-key-sidebar_newchat_toolbar .st-key-sidebar_btn_dedup_uploads button div[data-testid="stMarkdownContainer"] p {
        overflow: visible !important;
        line-height: 1.5 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    /* --- Chat Session List & Delete Button Styling --- */

    /* Tightly align columns in session list row; never wrap or clip delete btn */
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]) {
        gap: 0.25rem !important;
        flex-wrap: nowrap !important;
        align-items: center !important;
        margin-bottom: 0.2rem !important;
        border-radius: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: visible !important;
        padding-right: 2px !important;
        box-sizing: border-box !important;
    }
    /* Link column flexes + truncates; delete column keeps a fixed tap target */
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]) > div:first-child {
        flex: 1 1 auto !important;
        min-width: 0 !important;
        overflow: hidden !important;
    }
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]) > div:last-child {
        flex: 0 0 2.4rem !important;
        min-width: 2.4rem !important;
        width: 2.4rem !important;
        max-width: 2.4rem !important;
        overflow: visible !important;
        box-sizing: border-box !important;
    }

    /* Left-align chat session buttons with clean text truncation */
    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button {
        justify-content: flex-start !important;
        text-align: left !important;
        min-height: 2.35rem !important;
        height: 2.35rem !important;
        padding: 0 0.65rem !important;
        border-radius: 8px !important;
        font-size: 0.85rem !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        border: 1px solid transparent !important;
        transition: all 0.15s ease !important;
        width: 100% !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button div[data-testid="stMarkdownContainer"] {
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        width: 100% !important;
        text-align: left !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button div[data-testid="stMarkdownContainer"] p {
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        text-align: left !important;
        margin: 0 !important;
        font-size: 0.85rem !important;
    }

    /* Active chat session styling */
    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button[data-testid="baseButton-primary"] {
        justify-content: flex-start !important;
        font-weight: 500 !important;
    }

    /* Inactive chat session — a proper list item, not plain text.
       Secondary type guarantees a border from Streamlit defaults;
       this reinforces it so it stays visible in light + dark themes. */
    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button[data-testid="baseButton-secondary"] {
        background: light-dark(rgba(0, 0, 0, 0.03), rgba(255, 255, 255, 0.03)) !important;
        border: 1px solid light-dark(rgba(0, 0, 0, 0.18), rgba(255, 255, 255, 0.14)) !important;
        font-weight: 400 !important;
        cursor: pointer !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button[data-testid="baseButton-secondary"]:hover {
        background: light-dark(rgba(0, 0, 0, 0.06), rgba(255, 255, 255, 0.08)) !important;
        border-color: light-dark(rgba(0, 0, 0, 0.28), rgba(255, 255, 255, 0.22)) !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button[data-testid="baseButton-secondary"]:active {
        background: light-dark(rgba(0, 0, 0, 0.09), rgba(255, 255, 255, 0.10)) !important;
        border-color: light-dark(rgba(0, 0, 0, 0.32), rgba(255, 255, 255, 0.26)) !important;
    }

    /* Hovering anywhere on the row (incl. delete btn) highlights the item */
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]):hover div[class*="st-key-session_link_"] button[data-testid="baseButton-secondary"] {
        background: light-dark(rgba(0, 0, 0, 0.06), rgba(255, 255, 255, 0.08)) !important;
        border-color: light-dark(rgba(0, 0, 0, 0.28), rgba(255, 255, 255, 0.22)) !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] button:focus-visible {
        outline: 2px solid rgba(147, 197, 253, 0.6) !important;
        outline-offset: 1px !important;
    }

    /* Sleek Session Delete Button — overflow visible so emoji never clips */
    [data-testid="stSidebar"] div[class*="st-key-session_del_"] {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        overflow: visible !important;
        width: 100% !important;
        box-sizing: border-box !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] div[data-testid="stButton"] {
        width: 100% !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] button {
        min-height: 2.35rem !important;
        height: 2.35rem !important;
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        overflow: visible !important;
        box-sizing: border-box !important;
        line-height: 1.5 !important;
        border-radius: 8px !important;
        background: rgba(255, 255, 255, 0.06) !important;
        border: 1px solid rgba(255, 255, 255, 0.14) !important;
        opacity: 0.85;
        transition: background 0.2s ease, border-color 0.2s ease, opacity 0.2s ease !important;
        cursor: pointer !important;
        transform: none !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] button div[data-testid="stMarkdownContainer"] {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        overflow: visible !important;
        line-height: 1.5 !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] button p {
        margin: 0 !important;
        padding: 0 !important;
        font-size: 1rem !important;
        line-height: 1.5 !important;
        overflow: visible !important;
        filter: none;
        transition: filter 0.2s ease !important;
    }

    /* Gently highlight delete button when its row is hovered */
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]):hover div[class*="st-key-session_del_"] button {
        opacity: 1;
        background: rgba(255, 255, 255, 0.10) !important;
        border-color: rgba(255, 255, 255, 0.22) !important;
    }

    /* Delete Button Hover & Active States (no scale — scale pushed the
       right-edge button outside the sidebar and got clipped) */
    [data-testid="stSidebar"] div[class*="st-key-session_del_"] button:hover {
        opacity: 1 !important;
        background: rgba(239, 68, 68, 0.18) !important;
        border: 1px solid rgba(239, 68, 68, 0.45) !important;
        box-shadow: 0 0 10px rgba(239, 68, 68, 0.25) !important;
        transform: none !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] button:hover p {
        filter: none !important;
        transform: none !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] button:active {
        transform: none !important;
        background: rgba(239, 68, 68, 0.35) !important;
    }

    /* Path/pattern remove buttons (allowed, media pending, exclusion pending) */
    [data-testid="stSidebar"] div[class*="st-key-del_allowed_"] button,
    [data-testid="stSidebar"] div[class*="st-key-del_media_temp_"] button,
    [data-testid="stSidebar"] div[class*="st-key-del_excl_temp_"] button {
        min-height: 1.85rem !important;
        height: 1.85rem !important;
        width: 1.85rem !important;
        padding: 0 !important;
        border-radius: 6px !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        opacity: 0.4;
        transition: all 0.2s ease !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-del_allowed_"] button:hover,
    [data-testid="stSidebar"] div[class*="st-key-del_media_temp_"] button:hover,
    [data-testid="stSidebar"] div[class*="st-key-del_excl_temp_"] button:hover {
        opacity: 1 !important;
        background: rgba(239, 68, 68, 0.18) !important;
        border: 1px solid rgba(239, 68, 68, 0.4) !important;
        color: #ef4444 !important;
    }

    /* Hide Streamlit's automatic "Press Enter to submit form" hint inside
       sidebar forms — the Add inputs already explain that Enter adds. */
    [data-testid="stSidebar"] [data-testid="stInputInstructions"] {
        display: none !important;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# Setup session state
from utils import chat_store

active_id = st.session_state.get("active_session_id")
chat_store.prune_empty_sessions(exclude_id=active_id)

if "active_session_id" not in st.session_state or (active_id and not chat_store.get_session(active_id)):
    st.session_state.active_session_id = chat_store.ensure_active_session()

if "messages" not in st.session_state:
    if st.session_state.active_session_id:
        st.session_state.messages = chat_store.get_session_messages(st.session_state.active_session_id)
    else:
        st.session_state.messages = []

if "media_feedback" not in st.session_state:
    st.session_state.media_feedback = None
if "allowed_feedback" not in st.session_state:
    st.session_state.allowed_feedback = None
if "exclusion_feedback" not in st.session_state:
    st.session_state.exclusion_feedback = None

# Manage sidebar
manage_sidebar()

# Inline file links (?open= / ?reveal=) must be handled before rendering,
# so a link click opens the target instead of just reloading the view.
sync_chat_session_url()

# Initialize the agent — fingerprint ensures switching provider/model/keys
# automatically invalidates the cached agent (previously stuck on old provider).
_cfg_snapshot = load_config()
_fingerprint = config_fingerprint(_cfg_snapshot)
agent, agent_loop = init_agent(cache_key=_fingerprint)
# Display greeting on empty chat, otherwise show conversation history
if not st.session_state.messages:
    st.title("📁 MediaMCP")
    display_greeting()
else:
    display_chat_history()

# Check agent readiness
if agent is None or agent_loop is None:
    error_type = st.session_state.get("agent_init_error_type")
    error_msg = st.session_state.get("agent_init_error")

    if error_type == "llm_config":
        st.info(
            "⚙️ **LLM Configuration Required**\n\n"
            f"{error_msg or 'Please configure your LLM provider in the sidebar.'}\n\n"
            "👉 Open the **⚙️ Settings & Configuration** panel in the sidebar on the left to enter your API key, or switch to **Ollama** for 100% local, keyless operation."
        )
    else:
        st.error(
            f"⚠️ **Agent Initialization Failed**\n\n"
            f"{error_msg or 'Please ensure MCP servers are running and try again.'}"
        )
        if st.button("🔄 Retry Agent Initialization"):
            init_agent.clear()
            st.session_state.messages = []
            st.rerun()

    # Disabled chat input placeholder while unconfigured
    st.chat_input("Configure your LLM provider in the sidebar to start chatting...", disabled=True)
else:
    # Handle user input when agent is ready
    handle_user_input(agent, agent_loop)
