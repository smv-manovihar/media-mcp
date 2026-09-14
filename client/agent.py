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

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from config.settings import load_config, PROVIDER_DEFAULTS


def get_llm_model(config):
    """
    Instantiate the appropriate LangChain chat model based on config settings.
    Supports OpenAI, OpenRouter, Anthropic, Groq, Google Gemini, Ollama, and Custom OpenAI-compatible endpoints.
    """
    provider = (config.llm_provider or "openai").lower()
    model_name = config.llm_model or ""
    base_url = (config.llm_base_url or "").strip()
    temperature = float(config.llm_temperature if config.llm_temperature is not None else 0.1)

    # Resolve API Key: prioritize provider-specific key in config, then fallback to environment
    api_key = (config.get_api_key_for_provider(provider) or config.llm_api_key or "").strip()
    preset = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["custom"])
    env_var = preset.get("env_key")
    if not api_key and env_var:
        api_key = (os.getenv(env_var) or "").strip()

    # Ollama and Custom do not require an API key
    if provider == "ollama":
        effective_base_url = base_url or "http://localhost:11434/v1"
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            api_key=api_key or "ollama",
            base_url=effective_base_url,
            temperature=temperature,
        )

    if provider == "custom":
        effective_base_url = base_url or "http://localhost:8000/v1"
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            api_key=api_key or "custom",
            base_url=effective_base_url,
            temperature=temperature,
        )

    if not api_key:
        provider_name = preset.get("name", provider.title())
        raise ValueError(
            f"API key for {provider_name} is required. Please enter your API key in the Model & Provider Settings panel in the sidebar."
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=temperature,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model_name,
            api_key=api_key,
            temperature=temperature,
        )

    if provider == "groq":
        effective_base_url = base_url or "https://api.groq.com/openai/v1"
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=effective_base_url,
            temperature=temperature,
        )

    if provider == "openrouter":
        effective_base_url = base_url or "https://openrouter.ai/api/v1"
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=effective_base_url,
            temperature=temperature,
        )

    # Default: OpenAI
    effective_base_url = base_url or None
    from langchain_openai import ChatOpenAI
    is_reasoning_model = any(model_name.lower().startswith(p) for p in ("o1", "o3"))
    kwargs = {}
    if not is_reasoning_model:
        kwargs["temperature"] = temperature
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=effective_base_url,
        **kwargs,
    )


@st.cache_resource(ttl=3600)
def init_agent(_cache_key=None):
    """
    Initialize the MCP client, tools, and the conversational ReAct agent.
    This function is cached to prevent re-initialization on every interaction.
    The event loop is kept open for use in chat.py and closed on cache cleanup.
    """
    load_dotenv()
    loop = None
    try:
        config = load_config()

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
            st.error("Failed to fetch any tools from the MCP servers after retries. Ensure file_ops and search_web servers are running.")
            return None, None

        # Initialize the configured language model
        try:
            model = get_llm_model(config)
        except ValueError as e:
            st.warning(f"⚠️ {e}")
            return None, None

        # Create the ReAct agent
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


def _cleanup_agent(agent_executor, loop):
    """Cleanup function to close the event loop when the cache is cleared."""
    if loop and not loop.is_closed():
        loop.close()


# Register cleanup callback with cache_resource
init_agent.cleanup = _cleanup_agent