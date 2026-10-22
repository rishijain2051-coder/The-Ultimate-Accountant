from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the repo folder, two levels above this file (app/bridge/config.py);
# in the installed app, the folder the app's code is unpacked into (_internal).
ROOT = Path(__file__).resolve().parents[2]
# The built UI (bridge-ui, `npm run build`): bridge-ui/out from the source; in the
# installed app, app/bridge/ui, with the rest of the app's own code, so an update
# carries it too (build.spec).
UI_DIR = next((p for p in (Path(__file__).resolve().parent / "ui", ROOT / "bridge-ui" / "out") if p.exists()),
              ROOT / "bridge-ui" / "out")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- storage -------------------------------------------------------
    # launch.py sets DATA_DIR; this is for running the bridge on its own.
    data_dir: Path = ROOT / "data" / "bridge"
    # SQLite by default: one file, backup = copy it. Point at postgresql+psycopg2://
    # for a shared multi-user setup; the models and queries are identical.
    # Left empty, it follows data_dir -- so setting DATA_DIR alone moves the
    # database too, which is what the installer relies on.
    database_url: str = ""

    def model_post_init(self, _ctx) -> None:
        if not self.database_url:
            self.database_url = f"sqlite:///{(Path(self.data_dir) / 'tally.db').as_posix()}"

    # --- tally ---------------------------------------------------------
    tally_host: str = "localhost"
    tally_port: int = 9000
    tally_timeout: float = 30.0
    # Export XML by default; direct posting is opt-in per the deployment.
    tally_allow_post: bool = False
    # A link to each bill in its voucher, opened from Tally (services/bill_links.py).
    # Off until the Tally add-on is loaded: the add-on is what shows the link.
    bill_links: bool = False
    bill_link_port: int = 8766
    # Bills photographed on a phone on the same Wi-Fi (services/phone.py).
    phone_port: int = 8767

    # --- ollama --------------------------------------------------------
    # Used only when the app's own AI (app/backend/ai.py) is not on this
    # computer: an Ollama you run yourself.
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"  # text + vision: also reads scans and handwriting
    ollama_timeout: float = 120.0
    # The app is fully functional without Ollama; the rule parser carries it.
    llm_enabled: bool = True

    # --- share of the computer -------------------------------------------
    # Set by the launcher, which also hard-caps the whole app at 50% of the
    # CPU (installer/launcher.py). 0 and 1.0 mean no limit, as in development.
    cpu_threads: int = 0     # threads the AI and the OCR may run on
    gpu_share: float = 1.0   # most of the time the GPU may be kept busy

    # --- ocr -----------------------------------------------------------
    # Pages with fewer than this many extracted characters are treated as scans.
    ocr_char_threshold: int = 120
    ocr_dpi: int = 300

    # --- review thresholds ---------------------------------------------
    # Entries at or above this are flagged "clean" for one-click approval.
    auto_clean_confidence: float = 0.90
    # Fuzzy party-name score below this is not offered as a match at all.
    match_floor: float = 0.55

    # --- watched folder -------------------------------------------------
    watch_enabled: bool = True
    watch_interval_seconds: float = 5.0

    # --- backup ---------------------------------------------------------
    backup_keep: int = 14

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def watch_dir(self) -> Path:
        return self.data_dir / "watch"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()

for _d in (settings.data_dir, settings.upload_dir, settings.backup_dir, settings.watch_dir):
    _d.mkdir(parents=True, exist_ok=True)
