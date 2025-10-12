import json
import fnmatch
from pathlib import Path, PurePath
from dataclasses import dataclass, field
from typing import List, Any

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
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """
        Creates a Config object from a dictionary.
        """
        # Prioritize 'user' key for new format, but fall back to root for old format
        user_data = data.get("user", data)

        def _parse_paths(path_list: List[str]) -> List[Path]:
            return [Path(p).expanduser().resolve() for p in path_list]

        return cls(
            user_allowed_paths=_parse_paths(user_data.get("allowed_paths", [])),
            user_media_index_allowed_paths=_parse_paths(
                user_data.get("media_index_allowed_paths", [])
            ),
            # Maintain backward compatibility for old key names
            user_exclusion_paths=list(
                user_data.get(
                    "user_exclusion_paths", user_data.get("exclusion_paths", [])
                )
            ),
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
        print("\n--- ⚙️ Configuration Summary ---")
        print(f"Source: {CONFIG_FILE_PATH}\n")

        print(
            f"🗂️  Allowed Paths:      {len(cfg.allowed_paths)} total ({len(cfg.user_allowed_paths)} user-defined)"
        )
        print(
            f"🎬 Media Index Paths:  {len(cfg.media_index_allowed_paths)} total ({len(cfg.user_media_index_allowed_paths)} user-defined)"
        )
        print(
            f"🚫 Exclusion Patterns: {len(cfg.all_exclusions)} total ({len(cfg.user_exclusion_paths)} user-defined, {len(DEFAULT_EXCLUSIONS)} defaults)"
        )

        print("\n--- ✅ Config Loaded ---\n")

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
