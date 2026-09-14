import json
import fnmatch
import re
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
    # --- Dev caches, virtual environments, and dependencies ---
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
    # --- OS junk, logs, and temporary files ---
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.log",
    "*.tmp",
    "*.cache",
    "logs",
    "temp",
    "tmp",
    # --- OS / system directories: never useful to index, often huge/slow ---
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "AppData",
    "$Recycle.Bin",
    "System Volume Information",
    "Recovery",
    "$WinREAgent",
    "PerfLogs",
    "MSOCache",
    "$GetCurrent",
    "$Windows.~BT",
    "$Windows.~WS",
    ".Trash",
    ".Spotlight-V100",
    ".fseventsd",
    ".DocumentRevisions-V100",
    ".TemporaryItems",
    # --- Locked OS system files & compiler debug artifacts ---
    "*.sys",
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
    "*.pdb",
    "*.ilk",
    "*.pyc",
    "*.pyo",
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


# Non-text models to filter out from chat model lists across all providers:
# audio/speech/music, image/video generation, embeddings, moderation/guardrails, and legacy completion models
NON_TEXT_PATTERNS = (
    # Audio, Speech, TTS, Music
    "whisper", "tts", "transcribe", "speech", "audio", "orpheus", "voice", "sound",
    "bark", "music", "lyria", "audiogen",
    # Image & Video generation
    "dall-e", "imagen", "flux", "diffusion", "image-gen", "video", "sora", "runway",
    # Embeddings & Rerankers
    "embedding", "embed", "bge-", "gte-", "e5-", "rerank",
    # Moderation & Guardrails
    "moderation", "guard", "safeguard",
    # Legacy / Non-chat base completion engines
    "babbage", "davinci", "curie", "ada",
    # Robotics
    "robotics",
)


def is_text_chat_model(model_name: str) -> bool:
    """Returns True if the model name appears to be a text/chat LLM, not audio/image/embed."""
    name_lower = (model_name or "").lower()
    if not name_lower.strip():
        return False
    # Hyphenated/compound patterns (dall-e, image-gen, bge-, ...) match by substring.
    for pattern in NON_TEXT_PATTERNS:
        p = pattern.lower()
        if re.search(r"[^a-z0-9]", p):
            if p in name_lower:
                return False
    tokens = [t for t in re.split(r"[^a-z0-9]+", name_lower) if t]
    if not tokens:
        return True
    for pattern in NON_TEXT_PATTERNS:
        p = pattern.lower()
        if re.search(r"[^a-z0-9]", p):
            continue  # already handled above
        if len(p) <= 4:
            # Short patterns (ada, tts, sora, ...): exact token only,
            # so "ada" doesn't kill unrelated names containing those letters.
            if p in tokens:
                return False
        else:
            # Longer patterns: exact token or token starting with the pattern
            # (catches "embedding" for "embed", "guardrail" for "guard", ...).
            if any(t == p or t.startswith(p) for t in tokens):
                return False
    return True


def fetch_provider_models(provider: str, api_key: str = "", base_url: str = "", text_only: bool = True) -> Tuple[List[str], Optional[str]]:
    """
    Fetches the official list of available models directly from the provider's /models endpoint.
    Returns a tuple of (model_ids: List[str], error_message: Optional[str]).
    When text_only is False, no text/chat filtering is applied (used for vision lists).
    """
    import requests

    def _keep(mid: str) -> bool:
        return is_text_chat_model(mid) if text_only else True

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
                # Filter to generative (chat-capable) text models only
                model_ids = [
                    m["name"].replace("models/", "")
                    for m in models_data
                    if isinstance(m, dict)
                    and "generateContent" in m.get("supportedGenerationMethods", [])
                    and m.get("name", "")
                    and _keep(m["name"])
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
                    models = [
                        m.get("name")
                        for m in resp.json().get("models", [])
                        if isinstance(m, dict) and m.get("name") and _keep(m.get("name"))
                    ]
                    if models:
                        return sorted(models), None
            except Exception:
                pass
            # Fallback to OpenAI-compatible endpoint on Ollama
            try:
                resp = requests.get(f"{effective_url}/models", timeout=5)
                if resp.status_code == 200:
                    models = [
                        m.get("id")
                        for m in resp.json().get("data", [])
                        if isinstance(m, dict) and m.get("id") and _keep(m.get("id"))
                    ]
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
            model_ids = [
                m.get("id")
                for m in data
                if isinstance(m, dict) and m.get("id") and _keep(m.get("id"))
            ]
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
            mid = None
            if isinstance(item, str):
                mid = item
            elif isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
            if mid and _keep(str(mid)):
                model_ids.append(str(mid))

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
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_temperature: Optional[float] = None
    llm_keys: dict = field(default_factory=dict)
    llm_models: dict = field(default_factory=dict)
    llm_custom_instructions: str = ""
    llm_system_prompt: str = ""

    # Vision Model Settings (for inspect_image tool)
    vision_provider: str = ""
    vision_model: str = ""
    vision_enabled: bool = False

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

    def get_model_for_provider(self, provider: str) -> str:
        """Returns the saved model for a given provider, if any."""
        if hasattr(self, "llm_models") and isinstance(self.llm_models, dict) and provider in self.llm_models:
            return self.llm_models[provider]
        if self.llm_provider == provider:
            return self.llm_model
        return ""

    def set_model_for_provider(self, provider: str, model: str) -> None:
        """Sets the model for a given provider in the models map."""
        if not hasattr(self, "llm_models") or not isinstance(self.llm_models, dict):
            self.llm_models = {}
        self.llm_models[provider] = model
        if self.llm_provider == provider:
            self.llm_model = model

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
                "models": getattr(self, "llm_models", {}),
                "custom_instructions": self.llm_custom_instructions,
                "system_prompt": self.llm_custom_instructions,
                "vision_provider": self.vision_provider,
                "vision_model": self.vision_model,
                "vision_enabled": self.vision_enabled,
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

        raw_models = llm_data.get("models", {})
        llm_models = dict(raw_models) if isinstance(raw_models, dict) else {}

        api_key = llm_data.get("api_key", "")
        if not api_key and provider in llm_keys:
            api_key = llm_keys[provider]
        elif api_key and provider not in llm_keys:
            llm_keys[provider] = api_key

        model_name = llm_data.get("model", "")
        if not model_name and provider in llm_models:
            model_name = llm_models[provider]
        elif model_name and provider not in llm_models:
            llm_models[provider] = model_name

        custom_instructions = llm_data.get(
            "custom_instructions", llm_data.get("system_prompt", "")
        )

        # None (or missing) means "provider default" — temperature is omitted
        # from model requests instead of forcing 0.1.
        raw_temp = llm_data.get("temperature", None)
        try:
            llm_temperature = None if raw_temp is None else float(raw_temp)
        except (TypeError, ValueError):
            llm_temperature = None

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
            llm_model=model_name,
            llm_base_url=llm_data.get("base_url", ""),
            llm_api_key=api_key,
            llm_temperature=llm_temperature,
            llm_keys=llm_keys,
            llm_models=llm_models,
            llm_custom_instructions=custom_instructions,
            llm_system_prompt=custom_instructions,
            vision_provider=llm_data.get("vision_provider", "") or "",
            vision_model=llm_data.get("vision_model", ""),
            vision_enabled=bool(llm_data.get("vision_enabled", False)),
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


def compile_exclusions(config: Config) -> Tuple[frozenset, tuple]:
    """
    Precompiled exclusion matcher for hot loops.

    Returns (plain_names_lower, glob_patterns_lower). Plain names match any
    path component case-insensitively; globs match the final file/dir name.
    Compile once per scan instead of rebuilding per file.
    """
    names = set()
    globs = []
    for pattern in config.all_exclusions:
        if "*" in pattern or "?" in pattern or "[" in pattern:
            globs.append(pattern.lower())
        else:
            names.add(pattern.lower())
    return frozenset(names), tuple(globs)


def should_exclude_compiled(
    path: Path, plain_names: frozenset, globs: tuple
) -> bool:
    """Exclusion check against a precompiled (names, globs) pair."""
    if plain_names and plain_names.intersection(p.lower() for p in path.parts):
        return True
    if globs:
        name_lower = path.name.lower()
        for pattern in globs:
            if fnmatch.fnmatch(name_lower, pattern):
                return True
    return False


def should_exclude(path: Path, config: Config) -> bool:
    """
    Checks if a given path should be excluded based on the config.
    Matching is case-insensitive so e.g. 'Windows' also catches 'WINDOWS'.
    For hot loops prefer compile_exclusions + should_exclude_compiled.
    """
    names, globs = compile_exclusions(config)
    return should_exclude_compiled(path, names, globs)
