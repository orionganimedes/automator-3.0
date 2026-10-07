"""
config.py - Central configuration and logging setup for Android Social Media Automator.
"""

import logging
import sys

# --- Target Android Application ---
PACKAGE_NAME: str = "com.instagram.lite"  # "com.instagram.lite", "com.instagram.android", "com.facebook.katana"

# --- Local AI (Ollama) Endpoints & Models ---
OLLAMA_BASE_URL: str = "http://localhost:11434/api/generate"
VISION_MODEL: str = "qwen2.5vl:latest"
TEXT_MODEL: str = "qwen3:latest"

# --- Automation Parameters ---
MAX_ITERATIONS: int = 5
OUTPUT_FILE: str = "analysis_results.json"
DEFAULT_SCREEN_IMAGE: str = "screen.png"

# --- UI Interaction Timers (Seconds) ---
APP_STARTUP_DELAY_SECONDS: float = 5.0
SCROLL_WAIT_SECONDS: float = 2.5
POST_LOAD_WAIT_SECONDS: float = 6.5
LIKE_DELAY_SECONDS: float = 1.2
COMMENT_INPUT_TIMEOUT: float = 2.0
REQUEST_TIMEOUT_SECONDS: int = 90  # Generous timeout for local vision inference

# --- Post Engagement Defaults ---
DEFAULT_COMMENT_TONE: str = "positive"  # "positive", "casual", "enthusiastic", "question"

# --- Visual Icon Matching & Targets ---
ICON_TARGETS_DIR: str = "icon_targets"
ICON_MATCH_THRESHOLD: float = 0.65
ICON_SLEEP_DEFAULT: float = 1.5

# --- Logging Configuration ---
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger: logging.Logger = logging.getLogger("AndroidAutomator")
