from pathlib import Path
from typing import Union


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_runtime_path(value: Union[str, Path]) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root() / path
