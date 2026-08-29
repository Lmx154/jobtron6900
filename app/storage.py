import mimetypes
import re
from pathlib import Path
from uuid import uuid4


class FileStorage:
    def __init__(self, root: Path): self.root = root; self.root.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, content: bytes) -> tuple[str, str]:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip(".-") or "file"
        relative = f"{uuid4().hex}-{safe}"
        (self.root / relative).write_bytes(content)
        return relative, mimetypes.guess_type(safe)[0] or "application/octet-stream"

    def path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if self.root.resolve() not in candidate.parents: raise ValueError("Invalid stored path")
        return candidate
