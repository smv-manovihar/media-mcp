import streamlit as st
from components.sidebar import manage_sidebar
from components.chat import display_greeting, display_chat_history, handle_user_input
from client.agent import init_agent

st.set_page_config(page_title="MediaMCP", page_icon="🤖")
st.title("📁 MediaMCP")

# Agent initialization
agent, agent_loop = init_agent()

# Setup session state
if "messages" not in st.session_state:
    st.session_state.messages = []

if "media_feedback" not in st.session_state:
    st.session_state.media_feedback = None
if "allowed_feedback" not in st.session_state:
    st.session_state.allowed_feedback = None
if "exclusion_feedback" not in st.session_state:
    st.session_state.exclusion_feedback = None

# Display warning and retry button if agent initialization failed
if agent is None or agent_loop is None:
    st.error(
        "Failed to initialize the agent. Please ensure MCP servers are running and try again."
    )
    if st.button("Retry Agent Initialization"):
        # Clear the cached init_agent to force reinitialization
        init_agent.clear()
        st.session_state.messages = []  # Optionally clear messages to reset chat
        st.rerun()  # Rerun the app to reattempt initialization
else:
    # Display greeting
    display_greeting()

    # Manage sidebar
    manage_sidebar()

    # Display chat history
    display_chat_history()

    # Handle user input
    handle_user_input(agent, agent_loop)
