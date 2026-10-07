"""
main.py - Main automation pipeline coordinating ADB controller and local AI agent.
"""

import json
import time
from typing import Any, Dict, List

import config
from core.adb.controller import AndroidController
from core.ai.agent import AIAgent
from config import logger


def print_iteration_banner(iteration: int, max_iterations: int) -> None:
    """Prints a structured banner for each iteration."""
    banner = f"--- Iteration {iteration} / {max_iterations} ---"
    logger.info("=" * len(banner))
    logger.info(banner)
    logger.info("=" * len(banner))


def log_analysis_results(vision_result: Dict[str, Any], text_analysis: Dict[str, Any]) -> None:
    """Formatted console logging of the AI extraction and analysis."""
    reasoning = vision_result.get("reasoning", "N/A")
    should_scroll = vision_result.get("should_scroll", True)
    posts = vision_result.get("posts", [])
    analysis_items = text_analysis.get("analysis", [])

    logger.info(f"AI Vision Reasoning : {reasoning}")
    logger.info(f"AI Should Scroll     : {should_scroll}")
    logger.info(f"Posts Detected       : {len(posts)}")

    # Map analysis by author or index for clean display
    analysis_by_author = {item.get("author", ""): item for item in analysis_items if "author" in item}

    for idx, post in enumerate(posts, start=1):
        author = post.get("author", "Unknown")
        snippet = post.get("content_snippet", "")
        is_ad = post.get("is_ad", False)

        item = analysis_by_author.get(author)
        if not item and idx - 1 < len(analysis_items):
            item = analysis_items[idx - 1]

        topic = item.get("topic", "Unclassified") if item else "Unclassified"
        sentiment = item.get("sentiment", "Neutral") if item else "Neutral"
        action = item.get("action", "None") if item else "None"

        logger.info(
            f"  [Post #{idx}] Author: @{author} | IsAd: {is_ad}\n"
            f"    - Snippet   : \"{snippet}\"\n"
            f"    - Topic     : {topic}\n"
            f"    - Sentiment : {sentiment}\n"
            f"    - Action    : {action}"
        )


def main() -> None:
    """
    Main orchestration function:
    1. Initializes Android controller and AI agent.
    2. Opens target app and waits for initial render.
    3. Runs an iterative loop capturing screens, extracting UI text, and querying local LLMs.
    4. Executes actions (scrolling/copying) and records results to JSON.
    """
    logger.info("Starting Local AI Social Media Feed Automator...")

    try:
        adb_controller = AndroidController()
    except RuntimeError as e:
        logger.critical(f"Initialization aborted: {e}")
        return

    ai_agent = AIAgent(
        base_url=config.OLLAMA_BASE_URL,
        vision_model=config.VISION_MODEL,
        text_model=config.TEXT_MODEL,
    )

    # 1. Open target app and pause for UI startup
    logger.info(f"Launching target app: {config.PACKAGE_NAME}")
    adb_controller.open_app(config.PACKAGE_NAME)
    logger.info(f"Waiting {config.APP_STARTUP_DELAY_SECONDS} seconds for app initialization...")
    time.sleep(config.APP_STARTUP_DELAY_SECONDS)

    # 2. Results collection
    all_results: List[Dict[str, Any]] = []

    # 3. Iteration loop
    for iteration in range(1, config.MAX_ITERATIONS + 1):
        print_iteration_banner(iteration, config.MAX_ITERATIONS)

        # A. Screen capture and UI text extraction
        screen_file = adb_controller.capture_screen(config.DEFAULT_SCREEN_IMAGE)
        ui_text = adb_controller.get_ui_text()

        # B. Multimodal vision analysis
        vision_result = ai_agent.analyze_screen_vision(
            image_path=screen_file,
            ui_text=ui_text
        )

        posts = vision_result.get("posts", [])
        text_analysis: Dict[str, Any] = {"analysis": []}

        # C. Post data analysis (if posts were identified)
        if posts:
            text_analysis = ai_agent.analyze_post_data(vision_result)

        # D. Log the analysis clearly to the console
        log_analysis_results(vision_result, text_analysis)

        # E. Record current iteration state
        iteration_payload = {
            "iteration": iteration,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "vision_data": vision_result,
            "text_analysis": text_analysis,
        }
        all_results.append(iteration_payload)

        # Check for recommended actions (e.g. copy to clipboard if requested)
        for item in text_analysis.get("analysis", []):
            if str(item.get("action", "")).strip().lower() == "copy":
                snippet_to_copy = f"@{item.get('author')}: {item.get('topic')}"
                adb_controller.copy_to_clipboard(snippet_to_copy)

        # F. Navigation decision: scroll or terminate
        should_scroll = vision_result.get("should_scroll", True)
        if should_scroll:
            logger.info(f"Scrolling down. Waiting {config.SCROLL_WAIT_SECONDS}s for feed reload...")
            adb_controller.swipe_down()
            time.sleep(config.SCROLL_WAIT_SECONDS)
        else:
            logger.info("AI determined 'should_scroll' is False. Exiting feed loop.")
            break

    # 4. Save results to local JSON file
    logger.info(f"Writing all analysis results to '{config.OUTPUT_FILE}'...")
    try:
        with open(config.OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        logger.info(f"Successfully saved {len(all_results)} iteration record(s) to '{config.OUTPUT_FILE}'.")
    except Exception as e:
        logger.error(f"Failed to write output file: {e}")

    # 5. Log completion
    logger.info("Social media feed automation process completed successfully.")


if __name__ == "__main__":
    main()