from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def frontend_base_url(self) -> str:
        browser_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        if ":" in browser_host and not browser_host.startswith("["):
            browser_host = f"[{browser_host}]"
        return f"http://{browser_host}:{self.port}"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def default_settings() -> Settings:
    return Settings(Path(__file__).resolve().parent.parent / "data")
