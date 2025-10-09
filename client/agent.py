import streamlit as st
import os
import asyncio
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import client.prompts as prompts
from langchain_mcp_adapters.client import MultiServerMCPClient


@st.cache_resource
def init_agent():
    """
    Initialize the MCP client, tools, and the conversational agent.
    This function is cached to prevent re-initialization on every interaction.
    """
    load_dotenv()
    try:
        # Configure the client to connect to your tool servers
        client = MultiServerMCPClient(
            {
                "file_management": {
                    "url": "http://localhost:8000/mcp",
                    "transport": "streamable_http",
                },
                "web_search_scraper": {
                    "url": "http://localhost:8001/mcp",
                    "transport": "streamable_http",
                },
            }
        )
        # Set up an asyncio event loop for asynchronous operations
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tools = loop.run_until_complete(client.get_tools())

        if not tools:
            st.error("Failed to fetch any tools from the MCP servers.")
            return None, None

        # Get the Groq API key from environment variables
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            st.error("GROQ_API_KEY environment variable not set!")
            return None, None

        # Initialize the language model
        model = ChatGroq(api_key=groq_key, model="qwen/qwen3-32b")

        prompt_template = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    prompts.sys,
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # Create the ReAct agent
        agent_executor = create_react_agent(
            model=model,
            tools=tools,
            prompt=prompt_template,
        )

        return agent_executor, loop
    except Exception as e:
        st.error(f"Failed to initialize agent. Is an MCP server running? Error: {e}")
        return None, None
