# -*- coding: utf-8 -*-
"""Project-local ASR defaults; explicit environment variables take precedence."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "asr-config.json"


def read_settings():
    if not CONFIG_PATH.is_file():
        return {}
    with CONFIG_PATH.open(encoding="utf-8-sig") as source:
        settings = json.load(source)
    if not isinstance(settings, dict):
        raise ValueError("asr-config.json 必须是 JSON 对象")
    return settings


def model_directory():
    path = Path(read_settings().get("model_dir") or "models/paraformer-zh")
    return str((path if path.is_absolute() else ROOT / path).resolve())
