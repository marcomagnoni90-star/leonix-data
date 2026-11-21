from __future__ import annotations

from pathlib import Path
import pandas as pd

from config.settings import DATA_DIR

def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def get_data_path(filename: str) -> Path:
    ensure_data_dir()
    return DATA_DIR / filename

def save_dataframe(df: pd.DataFrame, filename: str) -> Path:
    path = get_data_path(filename)
    df.to_csv(path)
    return path

def load_dataframe(filename: str) -> pd.DataFrame:
    path = get_data_path(filename)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return pd.read_csv(path, index_col=0, parse_dates=True)

def file_exists(filename: str) -> bool:
    return get_data_path(filename).exists()
