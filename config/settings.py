import json
import fnmatch
from pathlib import Path, PurePath
from dataclasses import dataclass, field
from typing import List, Any, Tuple, Optional

# --- Constants and Setup ---
CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent
CONFIG_FILE_PATH = CONFIG_DIR / "config.json"
DEFAULT_UPLOADS_DIR = PROJECT_ROOT / "uploads"

DEFAULT_ALLOWED_PATHS: List[Path] = [DEFAULT_UPLOADS_DIR]
DEFAULT_MEDIA_INDEX_PATHS: List[Path] = []
DEFAULT_EXCLUSIONS = [
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "*.egg-info",
    "build",
    "dist",
    ".pytest_cache",
    ".tox",
    ".mypy_cache",
    "node_modules",
    ".npm",
    ".yarn",
    "bower_components",
    ".git",
    ".svn",
    ".hg",
    ".vscode",
    ".idea",
    ".eclipse",
    "*.iml",
    "target",
    "out",
    ".gradle",
    ".mvn",
    "bin",
    "obj",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.log",
    "*.tmp",
    "*.cache",
    "logs",
    "temp",
    "tmp",
]


# --- JSON Encoder ---
class PathEncoder(json.JSONEncoder):
    """A custom JSON encoder to handle Path objects."""

    def default(self, obj: Any):
        if isinstance(obj, PurePath):
            return str(obj)
        return super().default(obj)


# --- LLM Providers Setup ---
PROVIDER_DEFAULTS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "",
        "default_model": "",
        "requires_api_key": True,
        "env_key": "OPENAI_API_KEY",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "",
        "requires_api_key": True,
        "env_key": "OPENROUTER_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "default_model": "",
        "requires_api_key": True,
        "env_key": "ANTHROPIC_API_KEY",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "",
        "requires_api_key": True,
        "env_key": "GROQ_API_KEY",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "",
        "default_model": "",
        "requires_api_key": True,
        "env_key": "GOOGLE_API_KEY",
    },
    "ollama": {
        "name": "Ollama (Local)",
        "base_url": "http://localhost:11434/v1",
        "default_model": "",
        "requires_api_key": False,
        "env_key": None,
    },
    "custom": {
        "name": "Custom (OpenAI-Compatible)",
        "base_url": "http://localhost:8000/v1",
        "default_model": "",
        "requires_api_key": False,
        "env_key": None,
    },
}


def fetch_provider_models(provider: str, api_key: str = "", base_url: str = "") -> Tuple[List[str], Optional[str]]:
    """
    Fetches the official list of available models directly from the provider's /models endpoint.
    Returns a tuple of (model_ids: List[str], error_message: Optional[str]).
    """
    import requests

    provider = (provider or "openai").lower()
    preset = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["custom"])
    effective_url = (base_url or preset.get("base_url") or "").strip().rstrip("/")
    if not effective_url and provider == "openai":
        effective_url = "https://api.openai.com/v1"

    requires_key = preset.get("requires_api_key", False)
    if requires_key and not api_key:
        return [], f"API key is required to fetch models for {preset.get('name', provider)}."

    headers = {}
    if api_key:
        if provider == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    try:
        if provider == "gemini":
            # Use Google Generative Language REST API to list models
            if not api_key:
                return [], "API key is required to fetch Gemini models."
            try:
                resp = requests.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key, "pageSize": 100},
                    timeout=8,
                )
                if resp.status_code != 200:
                    try:
                        err_detail = resp.json().get("error", {}).get("message", resp.text[:120])
                    except Exception:
                        err_detail = resp.text[:120]
                    return [], f"HTTP {resp.status_code}: {err_detail}"
                models_data = resp.json().get("models", [])
                # Filter to generative (chat-capable) models only
                model_ids = [
                    m["name"].replace("models/", "")
                    for m in models_data
                    if isinstance(m, dict)
                    and "generateContent" in m.get("supportedGenerationMethods", [])
                    and m.get("name", "")
                ]
                return sorted(model_ids), None
            except requests.exceptions.ConnectionError:
                return [], "Connection error: could not reach Google Generative Language API."
            except requests.exceptions.Timeout:
                return [], "Timeout: request to Google API timed out."

        if provider == "ollama":
            root_url = effective_url.replace("/v1", "")
            # Try Ollama native endpoint first
            try:
                resp = requests.get(f"{root_url}/api/tags", timeout=5)
                if resp.status_code == 200:
                    models = [m.get("name") for m in resp.json().get("models", []) if isinstance(m, dict) and m.get("name")]
                    if models:
                        return sorted(models), None
            except Exception:
                pass
            # Fallback to OpenAI-compatible endpoint on Ollama
            try:
                resp = requests.get(f"{effective_url}/models", timeout=5)
                if resp.status_code == 200:
                    models = [m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict) and m.get("id")]
                    if models:
                        return sorted(models), None
            except Exception as e:
                return [], f"Could not connect to Ollama at {effective_url}: {e}"
            return [], f"Ollama returned no models. Is Ollama running at {effective_url}?"

        if provider == "anthropic":
            # Anthropic official models endpoint is /v1/models
            url = f"{effective_url}/v1/models" if not effective_url.endswith("/v1") else f"{effective_url}/models"
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                try:
                    err_detail = resp.json().get("error", {}).get("message", resp.text[:120])
                except Exception:
                    err_detail = resp.text[:120]
                return [], f"HTTP {resp.status_code}: {err_detail}"

            data = resp.json().get("data", [])
            model_ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
            return sorted(model_ids), None

        # OpenAI, OpenRouter, Groq, Custom
        url = f"{effective_url}/models"
        resp = requests.get(url, headers=headers, timeout=8)

        # If 404 and /v1 wasn't in URL, try appending /v1/models
        if resp.status_code == 404 and "/v1" not in effective_url:
            fallback_url = f"{effective_url}/v1/models"
            try:
                fallback_resp = requests.get(fallback_url, headers=headers, timeout=8)
                if fallback_resp.status_code == 200:
                    resp = fallback_resp
            except Exception:
                pass

        if resp.status_code != 200:
            try:
                err_detail = resp.json().get("error", {})
                if isinstance(err_detail, dict):
                    err_msg = err_detail.get("message", resp.text[:120])
                else:
                    err_msg = str(err_detail)
            except Exception:
                err_msg = resp.text[:120]
            return [], f"HTTP {resp.status_code}: {err_msg}"

        resp_json = resp.json()
        raw_list = resp_json.get("data") or resp_json.get("models") or []
        if not isinstance(raw_list, list):
            return [], f"Unexpected response format from {url}"

        model_ids = []
        for item in raw_list:
            if isinstance(item, str):
                model_ids.append(item)
            elif isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
                if mid:
                    model_ids.append(str(mid))

        if provider == "openai":
            # Filter non-chat models
            non_chat = ("whisper", "tts", "dall-e", "embedding", "moderation", "babbage", "davinci")
            model_ids = [m for m in model_ids if not any(x in m for x in non_chat)]

        if not model_ids:
            return [], f"No models found from {url}"

        return sorted(model_ids), None

    except requests.exceptions.ConnectionError:
        return [], f"Connection error: could not connect to {effective_url}. Is the service running?"
    except requests.exceptions.Timeout:
        return [], f"Timeout error: request to {effective_url} timed out."
    except Exception as e:
        return [], f"Error fetching models: {str(e)}"


# --- Configuration Management ---
@dataclass
class Config:
    """
    Manages application configuration by storing user-defined settings,
    which are then merged with system-wide defaults.
    """

    user_allowed_paths: List[Path] = field(default_factory=list)
    user_media_index_allowed_paths: List[Path] = field(default_factory=list)
    user_exclusion_paths: List[str] = field(default_factory=list)

    # LLM Settings
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_temperature: float = 0.1
    llm_keys: dict = field(default_factory=dict)
    llm_custom_instructions: str = ""
    llm_system_prompt: str = ""

    def __post_init__(self):
        # Keep custom instructions and legacy system prompt synchronized
        if not self.llm_custom_instructions and self.llm_system_prompt:
            self.llm_custom_instructions = self.llm_system_prompt
        elif self.llm_custom_instructions and not self.llm_system_prompt:
            self.llm_system_prompt = self.llm_custom_instructions

    @property
    def allowed_paths(self) -> List[Path]:
        """Returns the active list of allowed paths, merging user and default."""
        return sorted(list(set(self.user_allowed_paths + DEFAULT_ALLOWED_PATHS)))

    @property
    def media_index_allowed_paths(self) -> List[Path]:
        """Returns the active list of media index paths, merging user and default."""
        return sorted(
            list(set(self.user_media_index_allowed_paths + DEFAULT_MEDIA_INDEX_PATHS))
        )

    @property
    def all_exclusions(self) -> List[str]:
        """Returns a merged and unique list of default and user-defined exclusions."""
        return sorted(list(set(DEFAULT_EXCLUSIONS + self.user_exclusion_paths)))

    def get_api_key_for_provider(self, provider: str) -> str:
        """Returns the saved API key for a given provider, if any."""
        if hasattr(self, "llm_keys") and isinstance(self.llm_keys, dict) and provider in self.llm_keys:
            return self.llm_keys[provider]
        if self.llm_provider == provider:
            return self.llm_api_key
        return ""

    def set_api_key_for_provider(self, provider: str, key: str) -> None:
        """Sets the API key for a given provider in the keys map."""
        if not hasattr(self, "llm_keys") or not isinstance(self.llm_keys, dict):
            self.llm_keys = {}
        self.llm_keys[provider] = key
        if self.llm_provider == provider:
            self.llm_api_key = key

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return default

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict:
        """
        Serializes the config's active state to a dictionary for saving.
        It saves both system defaults (for reference) and user settings.
        """
        return {
            "system": {
                "allowed_paths": [str(p) for p in DEFAULT_ALLOWED_PATHS],
                "media_index_allowed_paths": [
                    str(p) for p in DEFAULT_MEDIA_INDEX_PATHS
                ],
                "exclusion_patterns": DEFAULT_EXCLUSIONS,
            },
            "user": {
                "allowed_paths": [str(p) for p in self.user_allowed_paths],
                "media_index_allowed_paths": [
                    str(p) for p in self.user_media_index_allowed_paths
                ],
                "exclusion_paths": self.user_exclusion_paths,
            },
            "llm": {
                "provider": self.llm_provider,
                "model": self.llm_model,
                "base_url": self.llm_base_url,
                "api_key": self.llm_api_key,
                "temperature": self.llm_temperature,
                "keys": self.llm_keys,
                "custom_instructions": self.llm_custom_instructions,
                "system_prompt": self.llm_custom_instructions,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """
        Creates a Config object from a dictionary.
        """
        # Prioritize 'user' key for new format, but fall back to root for old format
        user_data = data.get("user", data)
        llm_data = data.get("llm", {})

        def _parse_paths(path_list: List[str]) -> List[Path]:
            return [Path(p).expanduser().resolve() for p in path_list]

        provider = llm_data.get("provider") or "openai"
        if provider not in PROVIDER_DEFAULTS:
            provider = "openai"
        default_preset = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])

        raw_keys = llm_data.get("keys", {})
        llm_keys = dict(raw_keys) if isinstance(raw_keys, dict) else {}

        api_key = llm_data.get("api_key", "")
        if not api_key and provider in llm_keys:
            api_key = llm_keys[provider]
        elif api_key and provider not in llm_keys:
            llm_keys[provider] = api_key

        custom_instructions = llm_data.get(
            "custom_instructions", llm_data.get("system_prompt", "")
        )

        return cls(
            user_allowed_paths=_parse_paths(user_data.get("allowed_paths", [])),
            user_media_index_allowed_paths=_parse_paths(
                user_data.get("media_index_allowed_paths", [])
            ),
            user_exclusion_paths=list(
                user_data.get(
                    "user_exclusion_paths", user_data.get("exclusion_paths", [])
                )
            ),
            llm_provider=provider,
            llm_model=llm_data.get("model", ""),
            llm_base_url=llm_data.get("base_url", ""),
            llm_api_key=api_key,
            llm_temperature=float(llm_data.get("temperature", 0.1)),
            llm_keys=llm_keys,
            llm_custom_instructions=custom_instructions,
            llm_system_prompt=custom_instructions,
        )


# --- Utility Functions ---
def _ensure_config_file() -> None:
    """
    Ensures the config file and default directories exist. Creates a config
    file with the project root as the default user path if one is not found.
    """
    DEFAULT_UPLOADS_DIR.mkdir(exist_ok=True)
    if not CONFIG_FILE_PATH.exists():
        default_config = {
            "system": {
                "allowed_paths": [str(p) for p in DEFAULT_ALLOWED_PATHS],
                "media_index_allowed_paths": [
                    str(p) for p in DEFAULT_MEDIA_INDEX_PATHS
                ],
                "exclusion_patterns": DEFAULT_EXCLUSIONS,
            },
            "user": {
                # This now defaults the user's paths to the project directory
                "allowed_paths": [str(PROJECT_ROOT)],
                "media_index_allowed_paths": [str(PROJECT_ROOT)],
                "exclusion_paths": [],
            },
        }
        CONFIG_FILE_PATH.write_text(
            json.dumps(default_config, indent=4, cls=PathEncoder)
        )


def load_config(verbose: bool = False) -> Config:
    """Loads the configuration from config.json into a Config object."""
    _ensure_config_file()
    raw_data = json.loads(CONFIG_FILE_PATH.read_text())
    cfg = Config.from_dict(raw_data)

    if verbose:
        print("\n--- Configuration Summary ---")
        print(f"Source: {CONFIG_FILE_PATH}\n")

        print(
            f"Allowed Paths:      {len(cfg.allowed_paths)} total ({len(cfg.user_allowed_paths)} user-defined)"
        )
        print(
            f"Media Index Paths:  {len(cfg.media_index_allowed_paths)} total ({len(cfg.user_media_index_allowed_paths)} user-defined)"
        )
        print(
            f"Exclusion Patterns: {len(cfg.all_exclusions)} total ({len(cfg.user_exclusion_paths)} user-defined, {len(DEFAULT_EXCLUSIONS)} defaults)"
        )
        print(f"LLM Provider:       {cfg.llm_provider} (model: {cfg.llm_model})")

        print("\n--- Config Loaded ---\n")

    return cfg


def save_config(config: Config) -> None:
    """Saves a Config object to the config.json file."""
    if not isinstance(config, Config):
        raise TypeError("save_config expects a Config object")
    data_to_save = config.to_dict()
    CONFIG_FILE_PATH.write_text(json.dumps(data_to_save, indent=4, cls=PathEncoder))


def should_exclude(path: Path, config: Config) -> bool:
    """
    Checks if a given path should be excluded based on the config. This logic is
    rewritten to be more accurate and efficient.
    """
    exclusions = config.all_exclusions
    path_parts_set = set(path.parts)
    path_name = path.name

    for pattern in exclusions:
        # Check if the pattern is a glob or a simple name
        is_glob = "*" in pattern or "?" in pattern or "[" in pattern
        if is_glob:
            # Glob patterns match only against the final file/directory name
            if fnmatch.fnmatch(path_name, pattern):
                return True
        else:
            # Simple name patterns match against any component of the path
            if pattern in path_parts_set:
                return True
    return False
