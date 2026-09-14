import sqlite3, threading, chromadb
from contextlib import contextmanager
from pathlib import Path

# --- Constants ---
BASE_DIR = Path(__file__).parent.parent
DB_DIR = BASE_DIR / "database"
CHROMADB_PATH = DB_DIR / "chroma_store"
SQLITE_PATH = str(DB_DIR / "files.sqlite")

DB_DIR.mkdir(parents=True, exist_ok=True)

# --- ChromaDB Setup ---
chroma_client = chromadb.PersistentClient(path=str(CHROMADB_PATH))
# This collection will only store embeddings for images with cosine distance
chroma_coll = chroma_client.get_or_create_collection(
    name="siglip_images", embedding_function=None, metadata={"hnsw:space": "cosine"}
)
chroma_lock = threading.Lock()


# --- SQLiteDB Connection Class ---
class SQLiteDB:
    def __init__(self, path: str, timeout: int = 30):
        self.path = path
        self.timeout = timeout
        self.local = threading.local()

    def _create_conn(self):
        conn = sqlite3.connect(self.path, timeout=self.timeout)
        # Enable foreign key support in SQLite
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def get_conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = self._create_conn()
            self.local.conn = conn
        return conn

    @contextmanager
    def cursor(self):
        con = self.get_conn()
        cur = con.cursor()
        try:
            yield cur
            con.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def close_thread_conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self.local.conn = None


# --- Global Database Instance ---
db = SQLiteDB(SQLITE_PATH)


# --- Schema Initialization ---
def _initialize_schema():
    with db.cursor() as cur:
        # Table 1: Generic file information for ALL tracked files
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT NOT NULL,
                path TEXT NOT NULL,
                file_type TEXT, -- e.g., 'image', 'video', 'document'
                file_size INTEGER,
                file_mtime REAL, -- source file mtime (seconds since epoch) for fast change detection
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )

        # Table 2: Specific metadata ONLY for images
        # Linked to the 'files' table with a foreign key
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS images (
                file_id INTEGER PRIMARY KEY,
                
                -- Device/Camera Information
                make TEXT, model TEXT, software TEXT,
                
                -- Image Properties
                width INTEGER, height INTEGER, orientation INTEGER,
                datetime_original TEXT, datetime_digitized TEXT,
                
                -- Camera Settings
                exposure_time REAL, f_number REAL, iso INTEGER,
                focal_length REAL, flash INTEGER,
                
                -- GPS Coordinates
                latitude REAL, longitude REAL, altitude REAL,
                gps_timestamp TEXT,
                
                -- Reverse Geocoded Location Data
                location_display_name TEXT, location_country TEXT,
                location_state TEXT, location_city TEXT, location_postcode TEXT,
                
                FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
            )"""
        )

        # --- Table 3: Chat Sessions ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_pinned INTEGER DEFAULT 0
            )"""
        )

        # --- Table 4: Chat Messages ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                reasoning TEXT,
                monologue TEXT,
                tool_calls TEXT, -- JSON string
                file_path TEXT, -- file path or JSON list
                provider TEXT,
                model TEXT,
                token_usage TEXT, -- JSON string
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )"""
        )

        # --- Indexes for new 'files' table ---
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_path ON files(path)"
        )  # Path should be unique
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_hash ON files(hash)"
        )  # Index hash for lookups
        cur.execute("CREATE INDEX IF NOT EXISTS idx_file_type ON files(file_type)")

        # --- Indexes for 'images' table ---
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_make_model ON images(make, model)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_datetime ON images(datetime_original)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_location ON images(latitude, longitude)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_country_city ON images(location_country, location_city)"
        )

        # --- Indexes for Chat tables ---
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC)"
        )

        # --- Migration: monologue, provider, model, token_usage columns ---
        for col in ("monologue", "provider", "model", "token_usage"):
            try:
                cur.execute(f"SELECT {col} FROM chat_messages LIMIT 1")
            except Exception:
                try:
                    cur.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} TEXT")
                except Exception:
                    pass

        # --- Migration: file_mtime for fast stat-based change detection ---
        # Lets incremental scans skip re-hashing large unchanged files (e.g. videos).
        try:
            cur.execute("SELECT file_mtime FROM files LIMIT 1")
        except Exception:
            try:
                cur.execute("ALTER TABLE files ADD COLUMN file_mtime REAL")
            except Exception:
                pass
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_mtime ON files(file_mtime)"
        )


# Run schema setup when the module is imported
_initialize_schema()

