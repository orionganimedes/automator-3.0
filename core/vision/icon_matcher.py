"""
icon_matcher.py - Computer Vision and AI-assisted visual icon locator for Android screens.
Supports both direct icon templates and full/partial screenshots with RED marker annotations.
"""

from pathlib import Path
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from config import logger
from core.memory.store import memory_store


ICON_TARGETS_DIR = Path("icon_targets")


class IconMatcher:
    """
    Locates on-screen UI buttons/icons using a combination of OpenCV template matching
    (with automatic red-marker ROI extraction) and multimodal Vision AI fallback.
    """

    def __init__(self, targets_dir: Path = ICON_TARGETS_DIR) -> None:
        self.targets_dir: Path = Path(targets_dir)
        self.targets_dir.mkdir(exist_ok=True)

    @staticmethod
    def detect_red_annotation_box(image_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """
        Detects if the user drew a red or green circle, square, or outline on the image.
        Returns the bounding box (x, y, w, h) of the outer contour, or None.
        """
        if image_bgr is None or image_bgr.size == 0:
            return None

        # Convert to HSV color space to segment red and green markers
        hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

        # Red hue
        mask1 = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
        # Green hue (bright marker green)
        mask_green = cv2.inRange(hsv, np.array([35, 70, 60]), np.array([95, 255, 255]))
        marker_mask = cv2.bitwise_or(mask1, cv2.bitwise_or(mask2, mask_green))

        # Morphological close to bridge thin pencil/pen strokes
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed_mask = cv2.morphologyEx(marker_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Filter contours by minimum size (ignore single pixel noise)
        valid_contours = [c for c in contours if cv2.contourArea(c) > 80]
        if not valid_contours:
            return None

        # Pick the most prominent contour
        best_contour = max(valid_contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(best_contour)

        # Ensure box is meaningful (at least 15x15 px)
        if w >= 15 and h >= 15:
            logger.info(f"Annotation box detected at: x={x}, y={y}, w={w}, h={h}")
            return (x, y, w, h)

        return None

    @classmethod
    def extract_template_roi(cls, template_bgr: np.ndarray) -> Tuple[np.ndarray, bool]:
        """
        Extracts the target icon pattern:
        - If red or green markings exist, detects the loop, isolates the inner icon,
          and eliminates any residual marker stroke so template matching is clean.
        - Supports both dark icons on light background (posts) and white icons on dark background (reels).
        - Otherwise, returns the original image.
        """
        if template_bgr is None or template_bgr.size == 0:
            return template_bgr, False

        hsv = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
        mask_green = cv2.inRange(hsv, np.array([35, 70, 60]), np.array([95, 255, 255]))
        marker_mask = cv2.bitwise_or(mask1, cv2.bitwise_or(mask2, mask_green))

        contours, _ = cv2.findContours(marker_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return template_bgr, False

        # Filter contours by minimum size - user hand-drawn annotations are prominent loops
        valid_contours = [
            c for c in contours 
            if cv2.boundingRect(c)[2] >= 45 and cv2.boundingRect(c)[3] >= 35 and cv2.contourArea(c) > 600
        ]
        if not valid_contours:
            return template_bgr, False

        largest_contour = max(valid_contours, key=cv2.contourArea)
        rx, ry, rw, rh = cv2.boundingRect(largest_contour)
        logger.info(f"User annotation loop detected at: x={rx}, y={ry}, w={rw}, h={rh} (area={cv2.contourArea(largest_contour):.0f})")

        # Create filled mask of the contour interior using convex hull for stability
        hull = cv2.convexHull(largest_contour)
        filled_contour = np.zeros(template_bgr.shape[:2], dtype=np.uint8)
        cv2.drawContours(filled_contour, [hull], -1, 255, thickness=cv2.FILLED)

        # Dilate marker mask to cover stroke width
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dilated_marker = cv2.dilate(marker_mask, kernel, iterations=2)

        # Strict interior area
        inner_area = cv2.bitwise_and(filled_contour, cv2.bitwise_not(dilated_marker))

        gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        img_h, img_w = template_bgr.shape[:2]

        # Check 1: Dark icon on bright background (posts)
        dark_pixels = (gray < 160) & (inner_area > 0)
        dark_cnts, _ = cv2.findContours(dark_pixels.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_dark = [c for c in dark_cnts if cv2.boundingRect(c)[2] >= 10 and cv2.boundingRect(c)[3] >= 10]

        # Check 2: Bright/white icon on dark background (reels)
        bright_pixels = (gray > 140) & (inner_area > 0)
        # Exclude top text (e.g., numbers / counts)
        bright_pixels[0 : max(0, ry + int(rh * 0.15)), :] = 0
        bright_cnts, _ = cv2.findContours(bright_pixels.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_bright = [c for c in bright_cnts if cv2.boundingRect(c)[2] >= 10 and cv2.boundingRect(c)[3] >= 10]

        chosen_pts = None
        if valid_bright and (not valid_dark or max(cv2.contourArea(c) for c in valid_bright) >= max(cv2.contourArea(c) for c in valid_dark)):
            chosen_pts = np.concatenate(valid_bright)
        elif valid_dark:
            chosen_pts = np.concatenate(valid_dark)

        if chosen_pts is not None:
            ox, oy, ow, oh = cv2.boundingRect(chosen_pts)
            pad = 2
            ox1 = max(0, ox - pad)
            oy1 = max(0, oy - pad)
            ox2 = min(img_w, ox + ow + pad)
            oy2 = min(img_h, oy + oh + pad)
            cropped = template_bgr[oy1:oy2, ox1:ox2].copy()

            # Clean any residual marker strokes at borders
            crop_hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
            c_mask1 = cv2.inRange(crop_hsv, np.array([0, 50, 40]), np.array([15, 255, 255]))
            c_mask2 = cv2.inRange(crop_hsv, np.array([165, 50, 40]), np.array([180, 255, 255]))
            c_mask_g = cv2.inRange(crop_hsv, np.array([35, 50, 40]), np.array([95, 255, 255]))
            c_marker = cv2.bitwise_or(c_mask1, cv2.bitwise_or(c_mask2, c_mask_g))
            c_marker_dil = cv2.dilate(c_marker, np.ones((3, 3), np.uint8), iterations=1)
            
            # If icon was bright, replace marker stroke with dark blur; else with white
            if valid_bright and chosen_pts is not None and (not valid_dark or max(cv2.contourArea(c) for c in valid_bright) >= max(cv2.contourArea(c) for c in valid_dark)):
                cropped[c_marker_dil > 0] = [30, 30, 30]
            else:
                cropped[c_marker_dil > 0] = [255, 255, 255]

            logger.info(f"Extracted and cleaned inner icon ROI: size={cropped.shape[1]}x{cropped.shape[0]}")
            return cropped, True

        # Fallback: crop inner bounding box and clean marker pixels
        inset = min(6, rw // 6, rh // 6)
        ix1 = max(0, rx + inset)
        iy1 = max(0, ry + inset)
        ix2 = min(img_w, rx + rw - inset)
        iy2 = min(img_h, ry + rh - inset)
        if (ix2 - ix1) > 10 and (iy2 - iy1) > 10:
            cropped = template_bgr[iy1:iy2, ix1:ix2].copy()
            crop_hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
            c_mask1 = cv2.inRange(crop_hsv, np.array([0, 50, 40]), np.array([15, 255, 255]))
            c_mask2 = cv2.inRange(crop_hsv, np.array([165, 50, 40]), np.array([180, 255, 255]))
            c_mask_g = cv2.inRange(crop_hsv, np.array([35, 50, 40]), np.array([95, 255, 255]))
            c_marker = cv2.bitwise_or(c_mask1, cv2.bitwise_or(c_mask2, c_mask_g))
            cropped[c_marker > 0] = [255, 255, 255]
            return cropped, True

        return template_bgr, False

    def match_template_on_screen(
        self,
        screen_bgr: np.ndarray,
        template_bgr: np.ndarray,
        threshold: float = 0.55,
        scales: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Searches for template_bgr within screen_bgr across multiple scales.
        """
        if scales is None:
            scales = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30]

        screen_h, screen_w = screen_bgr.shape[:2]
        tpl_h, tpl_w = template_bgr.shape[:2]

        best_score = -1.0
        best_loc = None
        best_scale = 1.0
        best_size = (tpl_w, tpl_h)

        # Convert to gray for robust contrast matching
        screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        tpl_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)

        for scale in scales:
            sw = int(tpl_w * scale)
            sh = int(tpl_h * scale)

            if sw >= screen_w or sh >= screen_h or sw < 10 or sh < 10:
                continue

            resized = cv2.resize(tpl_gray, (sw, sh), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
            res = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

            if max_val > best_score:
                best_score = max_val
                best_loc = max_loc
                best_scale = scale
                best_size = (sw, sh)

        if best_score >= threshold and best_loc is not None:
            bx, by = best_loc
            bw, bh = best_size
            cx = bx + bw // 2
            cy = by + bh // 2

            logger.info(f"Template matched with confidence {best_score:.3f} at ({cx}, {cy}), scale={best_scale:.2f}")
            return {
                "found": True,
                "center": (cx, cy),
                "bounds": (bx, by, bx + bw, by + bh),
                "confidence": round(float(best_score), 4),
                "scale": round(float(best_scale), 2),
                "method": "template_matching"
            }

        logger.info(f"Template match below threshold ({best_score:.3f} < {threshold})")
        return {
            "found": False,
            "center": None,
            "bounds": None,
            "confidence": round(float(best_score), 4),
            "scale": round(float(best_scale), 2),
            "method": "template_matching"
        }

    def locate_icon(
        self,
        screen_path: str,
        template_name_or_path: str,
        threshold: float = 0.55,
        ai_agent: Any = None
    ) -> Dict[str, Any]:
        """
        Locates an icon on the given screen screenshot:
        1. Resolves template file from icon_targets/
        2. Detects if user marked it in red and extracts inner ROI
        3. Runs multi-scale OpenCV matching
        4. If below threshold and ai_agent is supplied, uses Qwen2.5-VL Vision AI fallback
        """
        # Resolve template path
        tpl_path = Path(template_name_or_path)
        if not tpl_path.exists():
            tpl_path = self.targets_dir / template_name_or_path

        if not tpl_path.exists():
            # Try with common extensions
            for ext in [".png", ".jpg", ".jpeg"]:
                candidate = self.targets_dir / f"{template_name_or_path}{ext}"
                if candidate.exists():
                    tpl_path = candidate
                    break

        if not tpl_path.exists():
            logger.error(f"Icon template not found: {template_name_or_path} (searched in {self.targets_dir})")
            return {"found": False, "error": f"Icon template '{template_name_or_path}' not found."}

        screen_bgr = cv2.imread(screen_path)
        if screen_bgr is None:
            logger.error(f"Could not read screen image: {screen_path}")
            return {"found": False, "error": f"Could not read screenshot at '{screen_path}'"}

        tpl_bgr = cv2.imread(str(tpl_path))
        if tpl_bgr is None:
            logger.error(f"Could not read template image: {tpl_path}")
            return {"found": False, "error": f"Could not read template image at '{tpl_path}'"}

        # Extract target ROI (auto-crop inside red marker if present)
        roi_bgr, had_red_marker = self.extract_template_roi(tpl_bgr)

        # 1. OpenCV Multi-Scale Template Matching
        match_res = self.match_template_on_screen(screen_bgr, roi_bgr, threshold=threshold)
        memory_store.record_locator("template", tpl_path.stem, bool(match_res.get("found")))
        if match_res.get("found"):
            match_res["template_name"] = tpl_path.name
            match_res["had_red_marker"] = had_red_marker
            return match_res

        # 2. Vision AI Fallback (Qwen2.5-VL)
        if ai_agent is not None:
            logger.info(f"Attempting Vision AI element localization for '{tpl_path.stem}'...")
            ai_res = self._locate_with_vision_ai(screen_path, tpl_path.stem, ai_agent)
            memory_store.record_locator("vision", tpl_path.stem, bool(ai_res.get("found")))
            if ai_res.get("found"):
                ai_res["template_name"] = tpl_path.name
                ai_res["had_red_marker"] = had_red_marker
                return ai_res

        match_res["template_name"] = tpl_path.name
        match_res["had_red_marker"] = had_red_marker
        return match_res

    def _locate_with_vision_ai(self, screen_path: str, icon_label: str, ai_agent: Any) -> Dict[str, Any]:
        """
        Uses Qwen2.5-VL to find coordinates of a UI component by descriptive name or label.
        """
        try:
            import base64
            import requests

            with open(screen_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            screen_img = cv2.imread(screen_path)
            h, w = screen_img.shape[:2]

            label_lower = icon_label.lower()
            if "heart" in label_lower or "like" in label_lower or "whatsapp" in label_lower or "whats" in label_lower:
                target_desc = "the 'Like' icon (heart outline or filled heart button below the post photo)"
            elif "comment" in label_lower or "burbuja" in label_lower:
                target_desc = "the 'Comment' icon (speech bubble next to the like heart)"
            elif "mas" in label_lower or "more" in label_lower:
                target_desc = "the '... más' text button to expand post caption"
            else:
                target_desc = f"the UI element or icon corresponding to '{icon_label}'"

            prompt = (
                f"You are a precise computer vision UI locator for an Android smartphone screen (width: {w}px, height: {h}px). "
                f"Your task is to locate the center pixel coordinates (x, y) of: {target_desc}. "
                f'Return STRICT JSON with the center coordinates in pixels: '
                f'{{"found": true, "x": int, "y": int, "description": "str"}} '
                f'If the element is not visible on screen, return: {{"found": false}}'
            )

            payload = {
                "model": ai_agent.vision_model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "format": "json"
            }

            res = requests.post(ai_agent.base_url, json=payload, timeout=25)
            if res.status_code == 200:
                data = ai_agent._clean_and_parse_json(res.json().get("response", "{}"))
                if data.get("found") and "x" in data and "y" in data:
                    x = int(data["x"])
                    y = int(data["y"])
                    # Bounds clamp
                    x = max(0, min(w - 1, x))
                    y = max(0, min(h - 1, y))
                    logger.info(f"Vision AI located '{icon_label}' at ({x}, {y}): {data.get('description')}")
                    return {
                        "found": True,
                        "center": (x, y),
                        "bounds": (x - 25, y - 25, x + 25, y + 25),
                        "confidence": 0.85,
                        "method": "vision_ai",
                        "description": data.get("description", "")
                    }
        except Exception as e:
            logger.warning(f"Vision AI fallback locator failed: {e}")

        return {"found": False, "method": "vision_ai"}

    def list_templates(self) -> List[Dict[str, Any]]:
        """
        Lists all image templates in the icon_targets directory.
        """
        supported_exts = {".png", ".jpg", ".jpeg", ".webp"}
        templates = []
        for file in sorted(self.targets_dir.iterdir()):
            if file.is_file() and file.suffix.lower() in supported_exts:
                size_kb = round(file.stat().st_size / 1024, 1)
                img = cv2.imread(str(file))
                dims = ""
                has_red = False
                if img is not None:
                    h, w = img.shape[:2]
                    dims = f"{w}x{h}"
                    has_red = self.detect_red_annotation_box(img) is not None

                templates.append({
                    "filename": file.name,
                    "stem": file.stem,
                    "size_kb": size_kb,
                    "dimensions": dims,
                    "has_red_marker": has_red,
                    "path": str(file)
                })
        return templates

    def draw_debug_match(self, screen_path: str, match_result: Dict[str, Any], output_path: str) -> bool:
        """
        Draws a visual highlight (bounding box + center crosshair) on the screen image for debugging.
        """
        try:
            screen = cv2.imread(screen_path)
            if screen is None or not match_result.get("found"):
                return False

            cx, cy = match_result["center"]
            bx1, by1, bx2, by2 = match_result.get("bounds", (cx - 25, cy - 25, cx + 25, cy + 25))

            # Draw green bounding box
            cv2.rectangle(screen, (bx1, by1), (bx2, by2), (0, 255, 0), 3)

            # Draw red crosshair in center
            cv2.drawMarker(screen, (cx, cy), (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2)

            # Put confidence label
            conf = match_result.get("confidence", 1.0)
            method = match_result.get("method", "match")
            label = f"{method}: {conf:.2f}"
            cv2.putText(screen, label, (bx1, max(25, by1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            cv2.imwrite(output_path, screen)
            return True
        except Exception as e:
            logger.error(f"Failed to draw debug match: {e}")
            return False
