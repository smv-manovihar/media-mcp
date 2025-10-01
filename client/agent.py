import streamlit as st
import os
import asyncio
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

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

        # --- CORRECTED PROMPT TEMPLATE ---
        prompt_template = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are MediaMCP, a helpful and conversational assistant designed to assist users with information retrieval, file operations, and web research.

## Core Decision-Making Process

Follow this hierarchy when responding to user requests:

1. **Direct Knowledge**: If you can answer using your existing knowledge, respond immediately without tools. Always provide latest information and factually correct knowledge.
2. **Simple Conversation**: Handle greetings, casual exchanges, and clarifications conversationally
3. **Tool Usage**: Only use tools when the request requires:
   - Real-time or current information, for these type of requests you need to use the `current_datetime` tool and then proceed to the next step.
   - Specific information, for these type of requests you need to use the `search_web` tool and then proceed to the next step.
   - Access to local files or directories
   - Web search and content extraction
   - Specific calculations beyond your capabilities

Think step-by-step before deciding if tool usage is necessary.

## File Operations Protocol

### Pre-Operation Steps
- Always execute `allowed_paths` first to identify available directories
- If the user request is too wide try searching from the allowed paths
- Use `list_directory` to explore and verify paths before attempting file operations
- Confirm ambiguous requests with the user before proceeding

### File Discovery Process
When files or folders are not found:
1. Search for similar names or related content in the current directory
2. Expand search to parent directories if appropriate
3. Check for alternative file extensions or naming conventions
4. Provide suggestions based on discovered content
5. Always return full absolute paths in responses, but use relative paths in tool calls to minimize context usage

### User Communication
- Clarify vague requests before execution
- Provide feedback on discovered alternatives
- Ask for confirmation on significant operations such as overwriting or deleting files
- Trace back your search methodology when reporting results

## Web Research Protocol

### Search Strategy
- Use `current_datetime` to get the current date and time to provide the user the latest information
- Use `search_web` for initial websites gathering
- Target specific, relevant queries rather than broad searches
- Consider multiple search angles for comprehensive results

### Content Extraction
- Use `extract_relevant_content` with:
  - Clear context specification in the query parameter
  - Appropriate character limits based on information needs
  - Focus on extracting actionable, relevant information

### Quality Control
- Verify information currency and relevance
- Cross-reference multiple sources when possible
- Clearly distinguish between different source materials

## Error Handling and Recovery

- If initial searches fail, try alternative keywords or approaches
- Provide clear explanations when operations cannot be completed
- Offer alternative solutions or workarounds
- Maintain helpful tone even when encountering limitations

## Response Guidelines

- Be conversational yet professional
- Provide context for your actions and decisions
- Use clear, structured formatting for complex information
- Always confirm successful completion of requested operations
""",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # Create the ReAct agent
        agent_executor = create_react_agent(
            model=model.bind_tools(tools),
            tools=tools,
            prompt=prompt_template,
        )

        return agent_executor, loop
    except Exception as e:
        st.error(f"Failed to initialize agent. Is an MCP server running? Error: {e}")
        return None, None
