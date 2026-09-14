import streamlit as st
from components.sidebar import manage_sidebar
from components.chat import display_greeting, display_chat_history, handle_user_input
from client.agent import init_agent

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

    /* Clean Sidebar Layout with Pinned Bottom Action */
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
        display: flex !important;
        flex-direction: column !important;
        min-height: calc(100vh - 4.5rem) !important;
        padding-bottom: 1rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div {
        display: flex !important;
        flex-direction: column !important;
        flex: 1 1 auto !important;
        min-height: 100% !important;
        height: auto !important;
    }

    /* Pushes the bottom action container all the way to the bottom */
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > div:has(.st-key-sidebar_bottom_action),
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > div:last-child {
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

    /* --- Chat Session List & Delete Button Styling --- */
    
    /* Tightly align columns in session list row */
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]) {
        gap: 0.25rem !important;
        align-items: center !important;
        margin-bottom: 0.2rem !important;
        border-radius: 8px !important;
    }

    /* Left-align chat session buttons with clean text truncation */
    [data-testid="stSidebar"] div[class*="st-key-session_link_"] > button {
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

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] > button div[data-testid="stMarkdownContainer"] {
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        width: 100% !important;
        text-align: left !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] > button div[data-testid="stMarkdownContainer"] p {
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        text-align: left !important;
        margin: 0 !important;
        font-size: 0.85rem !important;
    }

    /* Active chat session styling */
    [data-testid="stSidebar"] div[class*="st-key-session_link_"] > button[data-testid="baseButton-secondary"] {
        background: rgba(255, 255, 255, 0.08) !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
        color: #ffffff !important;
        font-weight: 500 !important;
    }

    /* Inactive chat session styling */
    [data-testid="stSidebar"] div[class*="st-key-session_link_"] > button[data-testid="baseButton-tertiary"] {
        background: transparent !important;
        border: 1px solid transparent !important;
        color: rgba(255, 255, 255, 0.65) !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_link_"] > button[data-testid="baseButton-tertiary"]:hover {
        background: rgba(255, 255, 255, 0.05) !important;
        color: #ffffff !important;
        border-color: rgba(255, 255, 255, 0.08) !important;
    }

    /* Sleek Session Delete Button */
    [data-testid="stSidebar"] div[class*="st-key-session_del_"] {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] > button {
        min-height: 2.35rem !important;
        height: 2.35rem !important;
        width: 2.35rem !important;
        min-width: 2.35rem !important;
        max-width: 2.35rem !important;
        padding: 0 !important;
        margin: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        border-radius: 8px !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        opacity: 0.28;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        cursor: pointer !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] > button div[data-testid="stMarkdownContainer"] {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        line-height: 1 !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] > button p {
        margin: 0 !important;
        font-size: 0.95rem !important;
        line-height: 1 !important;
        filter: grayscale(1) opacity(0.65);
        transition: filter 0.2s ease, transform 0.2s ease !important;
    }

    /* Softly elevate delete button when row is hovered */
    [data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-session_del_"]):hover div[class*="st-key-session_del_"] > button {
        opacity: 0.7;
    }

    /* Delete Button Hover & Active States */
    [data-testid="stSidebar"] div[class*="st-key-session_del_"] > button:hover {
        opacity: 1 !important;
        background: rgba(239, 68, 68, 0.18) !important;
        border: 1px solid rgba(239, 68, 68, 0.45) !important;
        box-shadow: 0 0 10px rgba(239, 68, 68, 0.25) !important;
        transform: scale(1.06) !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] > button:hover p {
        filter: none !important;
        transform: scale(1.15) !important;
    }

    [data-testid="stSidebar"] div[class*="st-key-session_del_"] > button:active {
        transform: scale(0.95) !important;
        background: rgba(239, 68, 68, 0.35) !important;
    }

    /* Allowed Paths remove button */
    [data-testid="stSidebar"] div[class*="st-key-del_allowed_"] > button {
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

    [data-testid="stSidebar"] div[class*="st-key-del_allowed_"] > button:hover {
        opacity: 1 !important;
        background: rgba(239, 68, 68, 0.18) !important;
        border: 1px solid rgba(239, 68, 68, 0.4) !important;
        color: #ef4444 !important;
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

# Initialize the agent
agent, agent_loop = init_agent()

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
