"""
Shared configuration loader for validation scripts.

Reads connection parameters from environment variables or configs/.env.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / "configs" / ".env"


@dataclass(frozen=True)
class OVMSConfig:
    host: str
    rest_port: int
    model_name: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.rest_port}/v3"


def load_config() -> OVMSConfig:
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)

    return OVMSConfig(
        host=os.getenv("OVMS_HOST", "localhost"),
        rest_port=int(os.getenv("OVMS_REST_PORT", "8000")),
        model_name=os.getenv("OVMS_MODEL_NAME", "qwen2.5-0.5b-instruct"),
    )
