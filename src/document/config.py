"""Document config loader for the Layer 2 regulatory pipeline."""

from pathlib import Path

import yaml


def load_document_config(config_path: Path) -> dict:
    """Load and return data/layer_2/document_config.yaml as a dict."""
    with open(config_path) as f:
        return yaml.safe_load(f)
