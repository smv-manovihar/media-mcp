# Modified agent.py (simplified, removed wrapper and prompt handling)
import streamlit as st
import os
import asyncio
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from requests.exceptions import RequestException

from langchain_groq import ChatGroq
from langchain.agents import create_agent
import client.prompts as prompts
from langchain_mcp_adapters.client import MultiServerMCPClient


@st.cache_resource(ttl=3600)  # Cache for 1 hour
def init_agent(_cache_key=None):
    """
    Initialize the MCP client, tools, and the conversational ReAct agent.
    This function is cached to prevent re-initialization on every interaction.
    The event loop is kept open for use in chat.py and closed on cache cleanup.
    """
    load_dotenv()
    loop = None
    try:
        # Define server configurations
        servers = {
            "file_management": {
                "url": "http://localhost:8000/mcp",
                "transport": "streamable_http",
                "timeout": 10,
            },
            "web_search_scraper": {
                "url": "http://localhost:8001/mcp",
                "transport": "streamable_http",
                "timeout": 10,
            },
        }

        # Configure the client with retry logic
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=4, max=10),
            retry=retry_if_exception_type(RequestException),
        )
        async def get_tools_with_retry(client):
            return await client.get_tools()

        # Initialize client
        client = MultiServerMCPClient(servers)

        # Set up event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tools = loop.run_until_complete(get_tools_with_retry(client))

        if not tools:
            st.error("Failed to fetch any tools from the MCP servers after retries.")
            return None, None

        # Get the Groq API key
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            st.error("GROQ_API_KEY environment variable not set!")
            return None, None

        # Initialize the language model
        model = ChatGroq(api_key=groq_key, model="qwen/qwen3.8-27b")

        # Create the ReAct agent (no system_prompt here; handled dynamically in chat.py)
        agent = create_agent(
            model=model,
            tools=tools,
        )

        return agent, loop

    except Exception as e:
        st.error(f"Failed to initialize agent. Is an MCP server running? Error: {e}")
        if loop and not loop.is_closed():
            loop.close()
        return None, None

    # Note: Do not close the loop here; it will be closed on cache cleanup


def _cleanup_agent(agent_executor, loop):
    """Cleanup function to close the event loop when the cache is cleared."""
    if loop and not loop.is_closed():
        loop.close()


# Register cleanup callback with cache_resource
init_agent.cleanup = _cleanup_agent