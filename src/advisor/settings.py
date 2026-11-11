"""仓库配置和环境覆盖；路径始终相对于仓库。"""

import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def settings():
    cfg = yaml.safe_load((ROOT / "config/ai_advisor.yaml").read_text())
    env = {
        "ollama_base": "IRDS_OLLAMA_BASE",
        "chat_model": "IRDS_LLM_MODEL",
        "embed_model": "IRDS_EMBED_MODEL",
        "knowledge_dir": "IRDS_KB_DIR",
        "index_dir": "IRDS_KB_INDEX",
        "skills_dir": "IRDS_SKILLS_DIR",
    }
    for key, var in env.items():
        cfg[key] = os.getenv(var, cfg[key])
    if "IRDS_OLLAMA_BASE" not in os.environ and os.getenv("IRDS_OLLAMA_URL"):
        cfg["ollama_base"] = os.environ["IRDS_OLLAMA_URL"].removesuffix("/api/chat")
    cfg["ollama_base"] = cfg["ollama_base"].rstrip("/")
    for key in ["knowledge_dir", "index_dir", "skills_dir"]:
        cfg[key] = str(ROOT / cfg[key])
    return cfg
