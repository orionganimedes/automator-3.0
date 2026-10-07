"""
ai_agent.py - Local AI interaction agent using Ollama for Vision and Text inference.
"""

import base64
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import requests

from config import (
    OLLAMA_BASE_URL,
    REQUEST_TIMEOUT_SECONDS,
    TEXT_MODEL,
    VISION_MODEL,
    logger,
)

from core.ai.archetypes import (
    HUMAN_ARCHETYPES,
    NARRATIVE_GUIDELINES,
    REGIOMONTANO_ARCHETYPES,
)
from core.memory.store import memory_store


class AIAgent:
    """
    Communicates with a local Ollama instance to perform multimodal vision
    understanding and text analysis on extracted social media feeds.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        vision_model: str = VISION_MODEL,
        text_model: str = TEXT_MODEL,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initializes the AIAgent with API endpoints and model selections.

        :param base_url: Ollama API generate endpoint URL
        :param vision_model: Name of the local Vision model (e.g., 'qwen2-vl', 'qwen2.5vl:latest')
        :param text_model: Name of the local Text model (e.g., 'qwen3:latest', 'llama3')
        :param timeout: HTTP request timeout in seconds
        """
        self.base_url: str = base_url
        self.vision_model: str = self._resolve_model_name(base_url, vision_model, prefer_vision=True)
        self.text_model: str = text_model
        self.timeout: int = timeout

    @classmethod
    def is_ollama_online(cls, base_url: str = OLLAMA_BASE_URL, timeout: float = 0.8) -> bool:
        """Quickly checks whether the local Ollama instance is reachable."""
        try:
            tags_url = base_url.replace("/api/generate", "/api/tags")
            res = requests.get(tags_url, timeout=timeout)
            return res.status_code == 200
        except Exception:
            return False

    @classmethod
    def list_installed_models(cls, base_url: str = OLLAMA_BASE_URL) -> List[Dict[str, Any]]:
        """
        Queries Ollama to discover all locally installed models on the user's computer,
        classifying their capabilities (vision, text) and formatted disk sizes.
        """
        try:
            tags_url = base_url.replace("/api/generate", "/api/tags")
            res = requests.get(tags_url, timeout=2.5)
            if res.status_code != 200:
                logger.warning(f"Ollama tags endpoint responded with status {res.status_code}")
                return []

            data = res.json()
            models = []
            for item in data.get("models", []):
                name = item.get("name", "")
                size_bytes = item.get("size", 0)
                size_gb = round(size_bytes / (1024 ** 3), 2)
                details = item.get("details", {})
                capabilities = item.get("capabilities", [])

                family = details.get("family", "").lower()
                families = [f.lower() for f in details.get("families", [])]
                is_vision = (
                    "vision" in capabilities or
                    "vl" in name.lower() or
                    "vision" in name.lower() or
                    "qwen25vl" in family or
                    any("vl" in f for f in families)
                )
                is_text = "completion" in capabilities or not is_vision or True
                param_size = details.get("parameter_size", "")

                models.append({
                    "name": name,
                    "model": item.get("model", name),
                    "size_gb": size_gb,
                    "param_size": param_size,
                    "is_vision": is_vision,
                    "is_text": is_text,
                    "capabilities": capabilities,
                    "modified_at": item.get("modified_at", "")
                })

            logger.info(f"Discovered {len(models)} local Ollama models: {[m['name'] for m in models]}")
            return models
        except Exception as e:
            logger.error(f"Failed to list local Ollama models: {e}")
            return []

    @staticmethod
    def _resolve_model_name(base_url: str, target_model: str, prefer_vision: bool = False) -> str:
        """
        Attempts to find the exact matching model tag in local Ollama
        (e.g. matching 'qwen2.5vl' with 'qwen2.5vl:latest' or 'qwen2.5-vl:latest').
        If prefer_vision is True and target_model is not found, automatically falls back
        to any locally installed model with vision capabilities.
        """
        try:
            tags_url = base_url.replace("/api/generate", "/api/tags")
            res = requests.get(tags_url, timeout=1.5)
            if res.status_code == 200:
                raw_models = res.json().get("models", [])
                models = [m.get("name", "") for m in raw_models]
                for m in models:
                    if m == target_model or m.startswith(f"{target_model}:"):
                        return m
                t_norm = target_model.replace("-", "").replace(":latest", "").lower()
                for m in models:
                    base_m = m.split(":")[0].replace("-", "").lower()
                    if t_norm == base_m:
                        logger.info(f"Resolved model '{target_model}' to installed Ollama model '{m}'.")
                        return m

                if prefer_vision:
                    for item in raw_models:
                        m_name = item.get("name", "")
                        caps = item.get("capabilities", [])
                        family = item.get("details", {}).get("family", "").lower()
                        if "vision" in caps or "vl" in m_name.lower() or "vision" in m_name.lower() or "vl" in family:
                            logger.info(f"Using installed vision model '{m_name}' as vision fallback for '{target_model}'.")
                            return m_name
        except Exception:
            pass
        return target_model

    def _clean_and_parse_json(self, raw_text: str) -> Dict[str, Any]:
        """
        Cleans markdown formatting, removes reasoning thoughts (<think>...</think>),
        and parses raw JSON response text safely.
        """
        # Remove <think>...</think> tags if emitted by reasoning models
        cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # Extract strictly from opening brace to closing brace
        s = cleaned.find("{")
        e = cleaned.rfind("}")
        if s != -1 and e != -1 and s < e:
            cleaned = cleaned[s : e + 1]

        return json.loads(cleaned)

    def analyze_screen_vision(self, image_path: str, ui_text: str) -> Dict[str, Any]:
        """
        Reads the captured screen image, encodes it to base64, and prompts the local
        Vision model (Ollama) along with the accessibility tree text.

        :param image_path: Path to the screenshot file
        :param ui_text: Extracted text from the Android accessibility tree
        :return: Dictionary containing detected posts, should_scroll, and reasoning
        """
        logger.info(f"Encoding image '{image_path}' for vision model analysis...")
        try:
            with open(image_path, "rb") as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode("utf-8")
        except FileNotFoundError:
            logger.error(f"Screenshot file not found: {image_path}")
            return {"posts": [], "should_scroll": True, "reasoning": "Image file not found"}
        except Exception as e:
            logger.error(f"Error reading image file '{image_path}': {e}")
            return {"posts": [], "should_scroll": True, "reasoning": str(e)}

        prompt = (
            f"You are an AI analyzing a social media feed. "
            f"UI Text context: {ui_text}. "
            f"Identify up to 2 main posts. Ignore ads/system UI. "
            f"Return STRICT JSON: "
            f'{{"posts": [{{"author": "str", "content_snippet": "str", "is_ad": bool}}], '
            f'"should_scroll": bool, "reasoning": "str"}}'
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [img_base64],
            "stream": False,
            "format": "json",
        }

        logger.info(f"Querying vision model '{self.vision_model}' via Ollama...")
        try:
            response = requests.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            api_data = response.json()

            raw_response = api_data.get("response", "{}")
            result = self._clean_and_parse_json(raw_response)

            logger.info("Vision analysis successfully parsed.")
            return result

        except requests.exceptions.Timeout:
            logger.error(f"Vision API request timed out after {self.timeout}s.")
            return {"posts": [], "should_scroll": True, "reasoning": "Vision request timed out"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error calling Ollama API: {e}")
            return {"posts": [], "should_scroll": True, "reasoning": f"Ollama connection error: {e}"}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode vision JSON response: {e}. Raw: {raw_response[:200] if 'raw_response' in locals() else 'N/A'}")
            return {"posts": [], "should_scroll": True, "reasoning": "Invalid JSON response from vision model"}
        except Exception as e:
            logger.error(f"Unexpected error in vision analysis: {e}")
            return {"posts": [], "should_scroll": True, "reasoning": str(e)}

    def analyze_post_data(self, posts_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyzes extracted post items using the local Text model to determine
        topic classification, sentiment analysis, and recommended action.

        :param posts_data: Dictionary or list representing the extracted posts
        :return: Dictionary containing analysis entries for each post
        """
        logger.info(f"Querying text model '{self.text_model}' for post analysis...")
        # Prepare posts content string for the prompt
        posts_content = json.dumps(posts_data.get("posts", posts_data), ensure_ascii=False)

        prompt = (
            f"Analyze these posts: {posts_content}. "
            f"For each, determine: topic, sentiment (Positive/Neutral/Negative), and action (Save, Ignore, Copy). "
            f"Return STRICT JSON: "
            f'{{"analysis": [{{"author": "str", "topic": "str", "sentiment": "str", "action": "str"}}]}}'
        )

        payload = {
            "model": self.text_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        try:
            response = requests.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            api_data = response.json()

            raw_response = api_data.get("response", "{}")
            result = self._clean_and_parse_json(raw_response)

            logger.info("Text model post analysis successfully parsed.")
            return result

        except requests.exceptions.Timeout:
            logger.error(f"Text API request timed out after {self.timeout}s.")
            return {"analysis": []}
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error calling Ollama Text API: {e}")
            return {"analysis": []}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode text analysis JSON: {e}. Raw: {raw_response[:200] if 'raw_response' in locals() else 'N/A'}")
            return {"analysis": []}
        except Exception as e:
            logger.error(f"Unexpected error in post data analysis: {e}")
            return {"analysis": []}

    def analyze_single_post(self, image_path: str, ui_text: str) -> Dict[str, Any]:
        """
        Analyzes a single Instagram post view using Qwen2.5-VL to extract
        the caption, author, topic, and whether it has already been liked.

        :param image_path: Path to post screenshot
        :param ui_text: Dumped UI text context
        :return: Dict containing author, caption, already_liked, topic, summary
        """
        logger.info(f"Analyzing single post from screenshot '{image_path}'...")
        try:
            with open(image_path, "rb") as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to read image '{image_path}': {e}")
            return {
                "author": "unknown",
                "caption": ui_text[:200] if ui_text else "Post content",
                "already_liked": False,
                "topic": "General",
                "summary": ui_text[:100] if ui_text else ""
            }

        prompt = (
            f"You are an AI analyzing a single Instagram post opened on an Android screen. "
            f"UI Text context: \"{ui_text}\". "
            f"Extract the post author/username, the full caption or visible post text, "
            f"determine whether the post is already liked (heart is filled/red), and identify the topic. "
            f"Return STRICT JSON: {{"
            f'\"author\": \"str\", '
            f'\"caption\": \"str\", '
            f'\"already_liked\": bool, '
            f'\"topic\": \"str\", '
            f'\"summary\": \"str\"'
            f"}}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [img_base64],
            "stream": False,
            "format": "json",
        }

        try:
            response = requests.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            api_data = response.json()
            raw_response = api_data.get("response", "{}")
            result = self._clean_and_parse_json(raw_response)
            logger.info(f"Single post analysis complete: Author=@{result.get('author')}, Topic={result.get('topic')}")
            return result
        except Exception as e:
            logger.warning(f"Error in single post vision analysis: {e}. Falling back to UI text.")
            return {
                "author": "instagram_user",
                "caption": ui_text[:300] if ui_text else "Publicación de Instagram",
                "already_liked": False,
                "topic": "General",
                "summary": ui_text[:150] if ui_text else ""
            }

    def extract_post_from_screenshot(
        self,
        image_path: str,
        ui_text: str = ""
    ) -> Dict[str, Any]:
        """
        Visual multimodal extraction of post text, caption, author, topic, dynamic/contest details,
        and summary directly from a mobile screenshot using local Vision AI (Qwen2.5-VL / Ollama).
        Specifically resolves cases where Android ADB uiautomator dump cannot expose text
        from custom Canvas/Compose views in Instagram or Instagram Lite.

        :param image_path: Path to the screenshot file (e.g. 'current_post_screen.png')
        :param ui_text: Optional text dumped from accessibility tree as supplementary context
        :return: Dict with keys: caption, author, topic, tone, is_contest, contest_requirement,
                 teams_or_topic, summary, ai_transcribed, error_hint
        """
        logger.info(f"Extracting and reading post directly from screenshot '{image_path}'...")
        if not os.path.exists(image_path):
            logger.error(f"Screenshot file does not exist: {image_path}")
            return {
                "caption": "",
                "author": "desconocido",
                "topic": "General",
                "tone": "Informativo",
                "is_contest": False,
                "contest_requirement": "",
                "teams_or_topic": "",
                "summary": "No se encontró el archivo de captura de pantalla.",
                "ai_transcribed": False,
                "error_hint": f"Archivo '{image_path}' no encontrado en disco."
            }

        try:
            with open(image_path, "rb") as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to read image '{image_path}': {e}")
            return {
                "caption": "",
                "author": "desconocido",
                "topic": "General",
                "tone": "Informativo",
                "is_contest": False,
                "contest_requirement": "",
                "teams_or_topic": "",
                "summary": "Error al leer el archivo de captura.",
                "ai_transcribed": False,
                "error_hint": str(e)
            }

        # Check whether Ollama is online
        ollama_available = self.is_ollama_online(self.base_url, timeout=1.0)

        prompt = (
            "Eres un agente de Inteligencia Artificial experto en visión computacional y OCR especializado en redes sociales (Instagram e Instagram Lite).\n"
            "Analiza detenidamente la captura de pantalla del teléfono móvil adjunta.\n\n"
            "INSTRUCCIONES CLAVE:\n"
            "1. TRANSCRIBE ÍNTEGRAMENTE TODO EL TEXTO VISIBLE de la publicación: el texto del post, la descripción, el pie de foto, hashtags, y cualquier texto relevante sobreimpreso en la imagen o video.\n"
            "2. AUTOR: Identifica el nombre de usuario o cuenta que publica (ej. @clubtigres, @rayados, o la cuenta que aparece arriba).\n"
            "3. DINÁMICA / CONCURSO / PRONÓSTICO: Detecta si la publicación es una dinámica, sorteo, giveaway, concurso, o reto de pronóstico (ej. adivinar marcador Tigres vs Rayados, regalar boletos/jersey, etiquetar amigos, comentar para ganar).\n"
            "   - 'is_contest': true o false.\n"
            "   - 'contest_requirement': El requisito exacto que pide para participar (ej. 'Comentar marcador exacto del partido', 'Mencionar a dos amigos', etc.).\n"
            "   - 'teams_or_topic': Equipos participantes o temática (ej. 'Tigres UANL vs Rayados de Monterrey').\n"
            "4. RESUMEN: Redacta un resumen conciso de 1 a 2 oraciones en español que explique claramente de qué trata la publicación.\n"
            "5. TEMA Y TONO: Define el tema principal (ej. 'Deportes / Clásico Regio', 'Humor', 'Sorteo') y el tono (ej. 'Pasional', 'Festivo', 'Informativo').\n\n"
            f"Contexto adicional detectado por accesibilidad (si existe): \"{ui_text[:300]}\"\n\n"
            "RESPONDE ESTRICTAMENTE EN FORMATO JSON CON ESTA ESTRUCTURA:\n"
            "{\n"
            '  "caption": "texto completo transcrito de la publicación",\n'
            '  "author": "nombre_de_usuario",\n'
            '  "topic": "tema principal",\n'
            '  "tone": "tono de la publicación",\n'
            '  "is_contest": true,\n'
            '  "contest_requirement": "requisito o dinámica",\n'
            '  "teams_or_topic": "equipos o temática",\n'
            '  "summary": "resumen claro de 1-2 oraciones"\n'
            "}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [img_base64],
            "stream": False,
            "format": "json",
        }

        if ollama_available:
            try:
                response = requests.post(self.base_url, json=payload, timeout=min(self.timeout, 45))
                if response.status_code == 200:
                    raw_response = response.json().get("response", "{}")
                    result = self._clean_and_parse_json(raw_response)
                    if isinstance(result, dict):
                        caption = (result.get("caption") or "").strip()
                        if caption and len(caption) > 5:
                            result["caption"] = caption
                            result["author"] = (result.get("author") or "desconocido").strip()
                            result["topic"] = result.get("topic") or "General"
                            result["tone"] = result.get("tone") or "Informativo"
                            result["is_contest"] = bool(result.get("is_contest", False))
                            result["contest_requirement"] = (result.get("contest_requirement") or "").strip()
                            result["teams_or_topic"] = (result.get("teams_or_topic") or "").strip()
                            result["summary"] = (result.get("summary") or "").strip()
                            result["ai_transcribed"] = True
                            result["error_hint"] = ""
                            logger.info(f"Visual transcription succeeded via model '{self.vision_model}'. Author={result.get('author')}, Length={len(caption)}")
                            return result
            except Exception as e:
                logger.warning(f"Ollama Vision API call failed: {e}")

        # Fallback when Ollama Vision is offline or timed out
        meaningful_ui = ""
        if ui_text:
            lines = [line.strip() for line in ui_text.split(" | ") if line.strip()]
            nav_words = {"inicio", "buscar", "reels", "perfil", "tienda", "notificaciones", "mensajes", "atrás", "menu", "siguiendo", "para ti"}
            filtered = [l for l in lines if len(l) > 3 and l.lower() not in nav_words]
            meaningful_ui = "\n".join(filtered[:10])

        base_text = meaningful_ui or "Publicación capturada desde la pantalla del dispositivo móvil."
        analysis = self.analyze_post_context(base_text)

        error_msg = ""
        if not ollama_available:
            error_msg = "Ollama no está activo en http://localhost:11434. Inicia 'ollama serve' con un modelo de visión (ej. qwen2.5vl:latest) para transcripción automática con IA."
        else:
            error_msg = f"El modelo de visión '{self.vision_model}' no respondió a tiempo. Puedes ajustar el texto manualmente."

        return {
            "caption": meaningful_ui if meaningful_ui else "",
            "author": "instagram_user",
            "topic": analysis.get("topic") or "General",
            "tone": analysis.get("tone") or "Informativo",
            "is_contest": bool(analysis.get("is_contest", False)),
            "contest_requirement": (analysis.get("contest_requirement") or "").strip(),
            "teams_or_topic": (analysis.get("teams_or_topic") or "").strip(),
            "summary": (analysis.get("summary") or "Captura tomada correctamente.").strip(),
            "ai_transcribed": False,
            "error_hint": error_msg
        }


    def analyze_comments_context(
        self,
        screen_path: str,
        ui_text: str = ""
    ) -> Dict[str, Any]:
        """
        Multimodal vision and text analysis of the open comments drawer/feed.
        Extracts sample comments, detects if the post is a contest, giveaway,
        or prediction challenge (e.g. Clásico Regio Tigres vs Rayados),
        and identifies the exact requirement to participate.
        """
        img_base64 = ""
        if screen_path and Path(screen_path).is_file():
            img_base64 = self._encode_image_base64(screen_path)

        prompt = (
            "You are an AI analyzing the open comments drawer of an Instagram post on Android. "
            f"UI Text context: \"{ui_text[:1200]}\". "
            "Tasks: "
            "1. Determine if this post or comment thread is a contest, giveaway, raffle, or prediction challenge "
            "(e.g. 'sorteo', 'concurso', 'dinámica', 'jersey', 'autografiada', 'pronóstico', 'gana', 'marcador', 'quién se la lleva'). Set is_contest: true/false. "
            "2. If it is a contest/prediction, extract the specific requirement or challenge (e.g. 'pronóstico para el Clásico Regio 143: Tigres vs Rayados'). "
            "3. Extract a representative list of 2-5 sample comments showing how other users are commenting (e.g. 'Tigres 2-1', 'Gana la U 1-0', '3-0 Rayados'). "
            "4. Identify the subject and teams/people involved (e.g. Tigres UANL vs Rayados de Monterrey, Diego Balleza). "
            "Return STRICT JSON: {"
            '\"is_contest\": bool, '
            '\"contest_type\": \"pronostico\" | \"sorteo\" | \"pregunta\" | \"general\", '
            '\"contest_requirement\": \"str\", '
            '\"teams_or_topic\": \"str\", '
            '\"sample_comments\": [\"str\"], '
            '\"summary_vibe\": \"str\"'
            "}"
        )

        payload = {
            "model": self.vision_model if img_base64 else self.text_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        if img_base64:
            payload["images"] = [img_base64]

        try:
            response = requests.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            api_data = response.json()
            raw_response = api_data.get("response", "{}")
            result = self._clean_and_parse_json(raw_response)
            logger.info(
                f"Comments context analysis: IsContest={result.get('is_contest')}, "
                f"Type={result.get('contest_type')}, Requirement='{result.get('contest_requirement')}'"
            )
            return result
        except Exception as e:
            logger.warning(f"Error in comments vision analysis: {e}. Fallback to keyword heuristics.")
            text_lower = (ui_text or "").lower()
            is_c = any(w in text_lower for w in ["concurso", "sorteo", "dinamica", "dinámica", "jersey", "pronostico", "pronóstico", "ganador", "participar"])
            return {
                "is_contest": is_c,
                "contest_type": "pronostico" if "pronostico" in text_lower or "pronóstico" in text_lower or "marcador" in text_lower else ("sorteo" if is_c else "general"),
                "contest_requirement": "Comentar pronóstico del Clásico Regio Tigres vs Rayados" if "clasico" in text_lower or "clásico" in text_lower else "",
                "teams_or_topic": "Tigres vs Rayados" if "tigres" in text_lower or "rayados" in text_lower else "",
                "sample_comments": [],
                "summary_vibe": "deportivo" if "tigres" in text_lower or "clasico" in text_lower else "conversacional"
            }

    # Arquetipos y narrativas centralizados en core.ai.archetypes
    REGIOMONTANO_ARCHETYPES: List[Dict[str, Any]] = REGIOMONTANO_ARCHETYPES
    HUMAN_ARCHETYPES: List[Dict[str, Any]] = HUMAN_ARCHETYPES

    @staticmethod
    def strip_emojis(text: str) -> str:
        """Removes all emojis to enforce the non-negotiable rule: Cero emojis."""
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "\U0001F900-\U0001F9FF"
            "\U0001FA70-\U0001FAFF"
            "\U00002600-\U000026FF"
            "]+",
            flags=re.UNICODE
        )
        cleaned = emoji_pattern.sub("", text)
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def enforce_max_3_lines(text: str) -> str:
        """Enforces the rule: Máximo 3 líneas por comentario."""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) > 3:
            lines = lines[:3]
        return "\n".join(lines) if len(lines) > 1 else (" ".join(lines))

    @classmethod
    def load_persistent_memory(cls, filepath: str = "agents.md", max_entries: int = 8) -> Dict[str, Any]:
        """
        Parses agents.md (or agent.md) to extract recent interaction memory:
        - Which archetypes were used in the last 3-8 comments (for rotation)
        - Which visual elements were already referenced
        - What comments were emitted (to avoid repeating structures/words)
        """
        empty = {
            "recent_archetypes": [],
            "recent_comments": [],
            "recent_elements": [],
            "recent_openings": []
        }

        # 1. Fuente primaria: SQLite (agent_reflections)
        try:
            refs = memory_store.recent_reflections(max_entries)
            if refs:
                recent_archetypes = []
                recent_comments = []
                recent_elements = []
                recent_openings = []
                for r in refs:  # ya vienen newest-first (ORDER BY id DESC)
                    arch = (r.get("archetype") or "").strip()
                    if arch and arch not in recent_archetypes:
                        recent_archetypes.append(arch)
                    comm = (r.get("comment") or "").strip()
                    if comm:
                        recent_comments.append(comm)
                        words = comm.split()
                        if len(words) >= 2:
                            recent_openings.append(" ".join(words[:2]).lower())
                    elem = (r.get("visual_element") or "").strip()
                    if elem and elem.lower() not in ["ninguno", "n/a", "no aplica"] and elem not in recent_elements:
                        recent_elements.append(elem)
                return {
                    "recent_archetypes": recent_archetypes,
                    "recent_comments": recent_comments,
                    "recent_elements": recent_elements,
                    "recent_openings": recent_openings
                }
        except Exception as e:
            logger.warning(f"load_persistent_memory (SQLite) failed: {e}")

        # 2. Fallback: parseo legacy de agents.md
        target_path = filepath
        if not os.path.exists(target_path) and os.path.exists("agent.md"):
            target_path = "agent.md"

        if not os.path.exists(target_path):
            return empty

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()

            sections = content.split("### Interacción")
            recent_archetypes = []
            recent_comments = []
            recent_elements = []
            recent_openings = []

            for sec in reversed(sections[1:]):  # newest first
                arch_m = re.search(r"\*\*(?:Arquetipo|Arquetipo Usado):\*\*\s*(.+)", sec)
                comm_m = re.search(r"\*\*(?:Comentario Generado|Comentario):\*\*\s*\"?([^\n\"]+)\"?", sec)
                elem_m = re.search(r"\*\*(?:Elemento Visual Referenciado|Elemento Visual):\*\*\s*\"?([^\n\"]+)\"?", sec)

                if arch_m:
                    raw_arch = arch_m.group(1).strip()
                    cleaned_arch = re.sub(r"^\d+\.\s*", "", raw_arch)
                    cleaned_arch = re.sub(r"\s*\(.*?\)", "", cleaned_arch).strip()
                    if cleaned_arch and cleaned_arch not in recent_archetypes:
                        recent_archetypes.append(cleaned_arch)

                if comm_m:
                    comm = comm_m.group(1).strip()
                    recent_comments.append(comm)
                    words = comm.split()
                    if len(words) >= 2:
                        recent_openings.append(" ".join(words[:2]).lower())

                if elem_m:
                    elem = elem_m.group(1).strip()
                    if elem and elem.lower() not in ["ninguno", "n/a", "no aplica"] and elem not in recent_elements:
                        recent_elements.append(elem)

                if len(recent_comments) >= max_entries:
                    break

            return {
                "recent_archetypes": recent_archetypes,
                "recent_comments": recent_comments,
                "recent_elements": recent_elements,
                "recent_openings": recent_openings
            }
        except Exception as e:
            logger.warning(f"Error reading persistent memory from '{target_path}': {e}")
            return empty

    def analyze_visual_details(self, image_path: str = "", caption: str = "") -> Dict[str, Any]:
        """
        Multimodal visual analysis extracting subjects, action, environment,
        visible emotion, and specific organic details ("esa mirada", "el entorno", "la postura", "las manos", "el momento").
        """
        if not image_path or not os.path.exists(image_path):
            return {
                "sujetos": "personas o protagonistas de la publicación",
                "accion": "actividad y presencia en redes",
                "entorno": "el entorno de la escena",
                "emocion_visible": "autenticidad y dinamismo",
                "detalles_especificos": ["el momento", "el entorno", "esa mirada"],
                "puntos_conexion": "conexión directa con el mensaje del post"
            }

        ollama_available = self.is_ollama_online(self.base_url, timeout=0.8)
        if ollama_available:
            try:
                with open(image_path, "rb") as img_f:
                    b64 = base64.b64encode(img_f.read()).decode("utf-8")

                prompt = (
                    "Analiza esta imagen para un sistema de comentarios humanos de redes sociales.\n"
                    f"Texto del post de referencia: \"{caption[:300]}\"\n\n"
                    "Extrae los siguientes elementos:\n"
                    "1. 'sujetos': qué personas, rostros, objetos o figuras principales aparecen.\n"
                    "2. 'accion': qué acción física o situación está ocurriendo.\n"
                    "3. 'entorno': describe el lugar, fondo, luz o escenario.\n"
                    "4. 'emocion_visible': qué emoción o actitud transmite (mirada, energía, concentración, alegría).\n"
                    "5. 'detalles_especificos': elige de 1 a 3 detalles destacables (ej. 'esa mirada', 'la postura', 'las manos', 'el entorno', 'el momento').\n"
                    "6. 'puntos_conexion': qué conecta la imagen con el texto del post.\n\n"
                    "Responde STRICT JSON con las claves: sujetos, accion, entorno, emocion_visible, detalles_especificos (lista de strings), puntos_conexion."
                )

                payload = {
                    "model": self.vision_model,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                    "format": "json"
                }
                res = requests.post(self.base_url, json=payload, timeout=min(self.timeout, 25))
                if res.status_code == 200:
                    raw = res.json().get("response", "{}")
                    parsed = self._clean_and_parse_json(raw)
                    if parsed and "detalles_especificos" in parsed:
                        return parsed
            except Exception as e:
                logger.warning(f"analyze_visual_details failed with vision model: {e}")

        # Heuristic fallback based on caption
        cap_l = caption.lower()
        if any(w in cap_l for w in ["tigres", "rayados", "futbol", "partido", "estadio", "cancha", "gol"]):
            return {
                "sujetos": "jugadores y afición deportiva",
                "accion": "jugada decisiva o concentración deportiva",
                "entorno": "la cancha y el estadio con ambiente de partido",
                "emocion_visible": "pasión, concentración y entrega competitiva",
                "detalles_especificos": ["esa mirada", "la postura", "el momento"],
                "puntos_conexion": "el fervor del partido y la entrega en el juego"
            }
        elif any(w in cap_l for w in ["entrenamiento", "correr", "chipinque", "ejercicio", "gym", "rutina"]):
            return {
                "sujetos": "atleta en pleno esfuerzo",
                "accion": "entrenamiento y superación física",
                "entorno": "el entorno natural de entrenamiento y subida",
                "emocion_visible": "disciplina, perseverancia y enfoque",
                "detalles_especificos": ["la postura", "el entorno", "las manos"],
                "puntos_conexion": "el trabajo constante y la resistencia diaria"
            }
        else:
            return {
                "sujetos": "protagonista de la publicación",
                "accion": "momento capturado en la imagen",
                "entorno": "el entorno de la escena",
                "emocion_visible": "naturalidad y franqueza",
                "detalles_especificos": ["el momento", "el entorno", "esa mirada"],
                "puntos_conexion": "la armonía entre lo vivido y lo escrito"
            }

    def locate_follow_button_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to inspect a Facebook page/profile screenshot, verify if the
        'Seguir' (Follow) button is visible, detect whether the account is already following,
        and calculate the pixel coordinates [x, y] of the button center.
        """
        logger.info(f"Inspeccionando captura con modelo de visión '{self.vision_model}' para ubicar botón 'Seguir'...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo imagen para visión de botón seguir: {e}")
            return {"found": False, "error": str(e)}

        prompt = (
            "You are an expert AI vision system for mobile UI navigation on Facebook Lite.\n"
            "Analyze this Android screenshot of a Facebook page, creator, or profile.\n"
            f"Screen dimensions are {screen_width}x{screen_height} pixels.\n\n"
            "CRITICAL ACCOUNT AND PAGE VARIATIONS:\n"
            "The follow button visual style, position, and label vary depending on the account and page settings:\n"
            "1. UNFOLLOWED STATE (Offer to follow/like):\n"
            "   - Grey pill/rectangle button with 'Seguir', '+ Seguir', or '➕ Seguir'.\n"
            "   - Blue rectangular button with white text 'Seguir' or '+ Seguir'.\n"
            "   - Classic Page button: 'Me gusta' or '+ Me gusta' with a thumbs up 👍 icon.\n"
            "   - English UI: 'Follow', '+ Follow', 'Like'.\n"
            "   - Position: Located in the profile action bar below the bio (typically between Y=600 and Y=1100).\n"
            "     Can be on the RIGHT of 'Mensaje', on the LEFT of 'Mensaje', or centered full-width.\n\n"
            "2. ALREADY FOLLOWING STATE (Already follows):\n"
            "   - Grey button with 'Siguiendo', 'Following', or dropdown 'Siguiendo ▾'.\n"
            "   - Bell icon 🔔 (active notification / follow bell).\n"
            "   - Checkmark ✔️ next to or inside 'Siguiendo'.\n"
            "   - Classic Page: 'Te gusta' or 'Liked' with highlighted thumb 👍.\n\n"
            "3. EXCLUSIONS (Do NOT confuse with follow button):\n"
            "   - 'Mensaje' / 'Enviar mensaje' (Message button - NEVER tap this for follow).\n"
            "   - '...' (Three dots / More options button).\n"
            "   - Follower counters in the bio text like '6 seguidores · 3 seguidos' (pure text, NOT a button).\n\n"
            "TASK:\n"
            "- Locate the follow or like button in the profile action bar.\n"
            "- Measure its exact bounding box and center pixel coordinates [x, y] on this image.\n"
            "- Determine if the account is already following or offers to follow.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\n"
            '  "found": boolean,\n'
            '  "is_already_following": boolean,\n'
            '  "action_type": "seguir" | "me_gusta" | "already_following" | "not_found",\n'
            '  "label": "string",\n'
            '  "box_2d": [ymin, xmin, ymax, xmax],\n'
            '  "center_coordinates": [x, y],\n'
            '  "button_style": "string",\n'
            '  "reasoning": "string"\n'
            "}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict):
                    label = (parsed.get("label") or "").strip().lower()
                    reasoning = (parsed.get("reasoning") or "").strip().lower()
                    style = (parsed.get("button_style") or "").strip().lower()
                    action_type = (parsed.get("action_type") or "").strip().lower()

                    # 1. Determinación de no seguimiento explícito (ofrece seguir o dar me gusta)
                    explicit_not_following = (
                        action_type in ["seguir", "me_gusta"] or
                        "seguir" in label or
                        "follow" in label or
                        label in ["me gusta", "like", "+ me gusta"] or
                        any(neg in reasoning for neg in [
                            "no ha seguido", "no sigue", "no está siguiendo",
                            "no esta siguiendo", "aun no", "aún no", "no se sigue",
                            "para seguir", "opción de seguir", "ofrece seguir"
                        ])
                    )

                    # 2. Determinación de seguimiento activo previo
                    is_already_following = bool(parsed.get("is_already_following", False))
                    if action_type == "already_following":
                        is_already_following = True
                    elif any(w in label for w in ["siguiendo", "following", "te gusta", "liked"]):
                        is_already_following = True
                    elif "bell" in style or "campana" in style:
                        is_already_following = True

                    if explicit_not_following:
                        is_already_following = False
                    elif not is_already_following:
                        # Solo si no hay negaciones y la etiqueta no es Seguir
                        if any(pos in reasoning for pos in [
                            "ya sigue", "está siguiendo", "esta siguiendo",
                            "campana activa", "icono de campana", "botón siguiendo", "boton siguiendo"
                        ]):
                            is_already_following = True

                    found = (
                        bool(parsed.get("found", False)) or
                        is_already_following or
                        ("seguir" in label) or
                        ("follow" in label) or
                        ("me gusta" in label)
                    )
                    parsed["found"] = found
                    parsed["is_already_following"] = is_already_following
                    if is_already_following and not parsed.get("label"):
                        parsed["label"] = "Siguiendo"

                    if found:
                        box_2d = parsed.get("box_2d")
                        center_coords = parsed.get("center_coordinates") or parsed.get("center_pct")
                        calc_x = None
                        calc_y = None

                        # 1. Parse box_2d with intelligent axis detection
                        if isinstance(box_2d, (list, tuple)) and len(box_2d) == 4:
                            try:
                                v0, v1, v2, v3 = [float(v) for v in box_2d]
                                # Check if normalized 0..1
                                if max(v0, v1, v2, v3) <= 1.0:
                                    calc_x = int(((v0 + v2) / 2.0) * screen_width)
                                    calc_y = int(((v1 + v3) / 2.0) * screen_height)
                                else:
                                    # Pixel coordinates
                                    if v1 > screen_width or v3 > screen_width or (v0 < v1 and v3 > v2):
                                        calc_x = int((v0 + v2) / 2.0)
                                        calc_y = int((v1 + v3) / 2.0)
                                    elif v0 > screen_width or v2 > screen_width:
                                        calc_x = int((v1 + v3) / 2.0)
                                        calc_y = int((v0 + v2) / 2.0)
                                    else:
                                        calc_x = int((v1 + v3) / 2.0)
                                        calc_y = int((v0 + v2) / 2.0)
                            except Exception as e_b:
                                logger.warning(f"Error parseando box_2d: {e_b}")

                        # 2. Parse center_coordinates or center_pct if box_2d wasn't resolved or to cross-verify
                        if isinstance(center_coords, (list, tuple)) and len(center_coords) == 2:
                            cx, cy = float(center_coords[0]), float(center_coords[1])
                            if cx <= 1.0 and cy <= 1.0:
                                cx_px = int(cx * screen_width)
                                cy_px = int(cy * screen_height)
                            else:
                                cx_px = int(cx)
                                cy_px = int(cy)
                            # Si center_coordinates está en la zona de acción del perfil (30% a 75%), es altamente confiable
                            if int(screen_height * 0.30) <= cy_px <= int(screen_height * 0.75):
                                calc_x = cx_px
                                calc_y = cy_px
                            elif calc_x is None or calc_y is None:
                                calc_x = cx_px
                                calc_y = cy_px

                        # 3. Fallback coordinates based on state if missing
                        if calc_x is None or calc_y is None:
                            if is_already_following:
                                calc_x = int(screen_width * 0.25)
                                calc_y = int(screen_height * 0.55)
                            else:
                                calc_x = int(screen_width * 0.74)
                                calc_y = int(screen_height * 0.55)

                        # Sanity check: Ensure coordinates are on screen
                        calc_x = max(0, min(screen_width - 1, calc_x))
                        calc_y = max(0, min(screen_height - 1, calc_y))
                        parsed["tap_coordinates"] = (calc_x, calc_y)

                        logger.info(f"Qwen 2.5-VL detectó botón (siguiendo={is_already_following}): {parsed}")
                        return parsed
        except Exception as ex:
            logger.warning(f"Error consultando Qwen 2.5-VL para botón seguir: {ex}")

        return {"found": False, "label": "No detectado", "is_already_following": False}

    def locate_post_reactions_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to inspect a Facebook post screenshot dynamically.
        Determines whether the post's action/reaction bar (Like 👍 and Comment 💬) is visible,
        distinguishing it from the static bottom 'Escribe algo...' input bar and post text/flyers.
        Returns pixel coordinates [x, y] of like_center and comment_center.
        """
        logger.info(f"Inspeccionando pantalla con Qwen 2.5-VL ({self.vision_model}) para ubicar botones de reacción (Like/Comentar)...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo captura para visión de reacciones: {e}")
            return {"buttons_visible": False, "error": str(e)}

        prompt = (
            "You are an expert AI vision system analyzing mobile screenshots of Facebook Lite.\n"
            f"Screen dimensions: {screen_width}x{screen_height} pixels.\n\n"
            "TASK:\n"
            "1. Determine whether the post's reaction/action bar is currently visible on screen.\n"
            "   The reaction bar sits below the post text/photo/video and contains:\n"
            "   - Like button (thumbs up icon 👍, optionally with reaction count or text 'Me gusta' / 'Like')\n"
            "   - Comment button (speech bubble icon 💬, optionally with comment count or text 'Comentar')\n"
            "   - Share button (curved right arrow ↗️)\n"
            "   IMPORTANT: The bottom input bar 'Escribe algo...' or 'Añade un comentario...' is NOT the Like button.\n"
            "   IMPORTANT: Directly ABOVE the action row there is a small REACTION SUMMARY (tiny colored blue 👍 and red ❤️ circles\n"
            "   followed by a number, e.g. '👍❤️ 6'). That summary is NOT the Like button. Never return it.\n"
            "   The real Like button is a large rounded gray pill containing an OUTLINED thumbs-up icon, located in the LEFT third\n"
            "   of the screen, on the SAME horizontal row (same Y) as the Comment pill (speech bubble) and the Share pill (arrow).\n"
            "2. If the action row is visible, calculate the exact pixel coordinates [x, y] of:\n"
            "   - like_center: center of the Like pill (outlined thumbs up 👍) in the action row\n"
            "   - comment_center: center of the Comment pill (speech bubble 💬) in the same row\n"
            "   like_center and comment_center MUST have practically the same Y value.\n"
            "3. If the post is long and only post text/images are shown without the 👍 Like / 💬 Comment row, return buttons_visible: false.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\n"
            '  "buttons_visible": boolean,\n'
            '  "like_center": [x, y] or null,\n'
            '  "comment_center": [x, y] or null,\n'
            '  "confidence": float,\n'
            '  "reasoning": "string"\n'
            "}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict):
                    buttons_visible = bool(parsed.get("buttons_visible", False))
                    like_c = parsed.get("like_center")
                    comm_c = parsed.get("comment_center")
                    if buttons_visible and isinstance(like_c, list) and len(like_c) == 2:
                        lx = max(0, min(screen_width - 1, int(like_c[0])))
                        ly = max(0, min(screen_height - 1, int(like_c[1])))
                        parsed["like_center"] = (lx, ly)
                        if isinstance(comm_c, list) and len(comm_c) == 2:
                            cx = max(0, min(screen_width - 1, int(comm_c[0])))
                            cy = max(0, min(screen_height - 1, int(comm_c[1])))
                            parsed["comment_center"] = (cx, cy)
                        logger.info(f"Qwen 2.5-VL confirmó botones de reacción visibles: Like={parsed.get('like_center')}, Comentar={parsed.get('comment_center')}")
                        return parsed
                    else:
                        logger.info(f"Qwen 2.5-VL: Botones de reacción aún no visibles ({parsed.get('reasoning', '')})")
                        return {"buttons_visible": False, "reasoning": parsed.get("reasoning", "")}
        except Exception as e:
            logger.warning(f"Error consultando Qwen 2.5-VL para botones de reacción: {e}")

        return {"buttons_visible": False, "reasoning": "Error o respuesta inválida"}

    def locate_blue_send_button_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612,
        is_keyboard_open: bool = False
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to locate the blue send button (circular or pill button with white paper plane / send arrow ➤)
        situated on the RIGHT side of the comment input bar.
        Guarantees that it never targets keyboard keys or the letter 'Z'.
        """
        logger.info(f"Inspeccionando con Qwen 2.5-VL ({self.vision_model}) para ubicar el botón azul de envío (Send) [kb_open={is_keyboard_open}]...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo captura para visión de botón de envío: {e}")
            return {"found": False, "error": str(e)}

        kb_instruction = (
            "IMPORTANT: The soft keyboard is currently OPEN and visible on the lower ~40% of the screen.\n"
            "The comment input bar and the blue send button have floated UP, located directly above the top edge of the keyboard (around Y between 50% and 65% of screen height, or ~900-1000px).\n"
            "Under NO circumstances should you select coordinates inside the virtual keyboard (Y > 65%, such as the keyboard Enter key, Shift, or letter keys like Z)!\n"
            if is_keyboard_open else
            "IMPORTANT: The soft keyboard is CLOSED. The comment input bar and the blue send button are located along the bottom edge of the screen (around Y >= 85%).\n"
        )

        prompt = (
            "You are an expert AI vision system for mobile UI navigation on Facebook Lite.\n"
            f"Screen dimensions: {screen_width}x{screen_height} pixels.\n\n"
            "TASK:\n"
            "Locate the blue SEND button (flecha azul de envío / avión de papel blanco dentro de círculo o píldora azul ➤).\n"
            "It is positioned on the FAR RIGHT side of the text input bar where the user typed their comment.\n"
            f"{kb_instruction}"
            "STRICT RULES:\n"
            "- It is located in the rightmost portion of the comment input row (X >= 560 on a 720 wide screen).\n"
            "- Do NOT select any key on the soft keyboard (e.g. do NOT select Enter, Shift, or letter keys like Z).\n"
            "- Provide its center pixel coordinates [x, y].\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\n"
            '  "found": boolean,\n'
            '  "send_center": [x, y],\n'
            '  "confidence": float,\n'
            '  "reasoning": "string"\n'
            "}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict) and parsed.get("found"):
                    sc = parsed.get("send_center")
                    if isinstance(sc, list) and len(sc) == 2:
                        sx = max(0, min(screen_width - 1, int(sc[0])))
                        sy = max(0, min(screen_height - 1, int(sc[1])))
                        # Sanity check: Send button MUST be on the right side (x >= 75% width)
                        if sx >= int(screen_width * 0.75):
                            if is_keyboard_open and (int(screen_height * 0.45) <= sy <= int(screen_height * 0.68)):
                                parsed["send_center"] = (sx, sy)
                                logger.info(f"Qwen 2.5-VL ubicó botón de envío azul (con teclado abierto) en: ({sx}, {sy})")
                                return parsed
                            elif not is_keyboard_open and (sy >= int(screen_height * 0.75)):
                                parsed["send_center"] = (sx, sy)
                                logger.info(f"Qwen 2.5-VL ubicó botón de envío azul (con teclado cerrado) en: ({sx}, {sy})")
                                return parsed
                            elif not is_keyboard_open and (int(screen_height * 0.45) <= sy <= int(screen_height * 0.68)):
                                parsed["send_center"] = (sx, sy)
                                logger.info(f"Qwen 2.5-VL ubicó botón de envío azul en: ({sx}, {sy})")
                                return parsed
                            else:
                                logger.warning(f"Coordenadas devueltas por visión ({sx}, {sy}) no concuerdan con estado de teclado (kb_open={is_keyboard_open}).")
                        else:
                            logger.warning(f"Coordenada X devuelta por visión ({sx}) está muy a la izquierda para el botón de envío.")
        except Exception as e:
            logger.warning(f"Error consultando Qwen 2.5-VL para botón de envío: {e}")

    def analyze_instagram_post_layout(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Analiza dinámicamente la captura de pantalla de Instagram / Instagram Lite usando Qwen 2.5-VL.
        Determina automáticamente la disposición de la publicación:
          - 'reel_vertical_rail': botones de acción alineados verticalmente en el margen derecho (x >= 75%).
          - 'post_horizontal_row': botones de acción en fila horizontal debajo de la foto/carrusel (y >= 65%).
        Retorna las coordenadas exactas de Like (❤️) y Comentar (💬), si ya tiene Like, y si se permite scroll.
        """
        logger.info(f"Analizando dinámicamente layout y botones de Instagram con Qwen 2.5-VL ({self.vision_model})...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo imagen para análisis de layout de Instagram: {e}")
            return {"success": False, "error": str(e)}

        prompt = (
            "You are an expert mobile UI computer vision system analyzing an Instagram post or Reel on Android.\n"
            f"Screen dimensions: {screen_width}x{screen_height} pixels (width x height).\n\n"
            "TASK:\n"
            "Analyze the layout of the post/reel and locate the interactive action buttons:\n"
            "1. Determine 'layout_type':\n"
            "   - 'reel_vertical_rail': If action buttons (heart, comment, share) are arranged in a vertical column on the right side of the screen (X >= 550px on 720w screen).\n"
            "   - 'post_horizontal_row': If action buttons (heart, comment, share) are arranged horizontally in a row below the image/carousel (Like on the left, Comment next to it).\n"
            "2. Locate 'like_button':\n"
            "   - The heart icon button (❤️). Provide center coordinates [x, y].\n"
            "   - 'already_liked': boolean, true if the heart is solid/filled red, false if it is white/outline.\n"
            "3. Locate 'comment_button':\n"
            "   - The speech bubble / comment icon button (💬). Provide center coordinates [x, y].\n"
            "4. STRICT RULES:\n"
            "   - In 'reel_vertical_rail', the Like heart is above the Comment bubble in the right margin.\n"
            "   - DO NOT select the bottom navigation bar icons (Home, Search, Create, Reels, Profile at the very bottom).\n"
            "   - DO NOT confuse the Comment button with floating green WhatsApp icons at the bottom-right.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\n"
            '  "layout_type": "reel_vertical_rail" | "post_horizontal_row",\n'
            '  "like_button": {"found": boolean, "center": [x, y], "already_liked": boolean},\n'
            '  "comment_button": {"found": boolean, "center": [x, y]},\n'
            '  "confidence": float,\n'
            '  "reasoning": "string"\n'
            "}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict):
                    layout = parsed.get("layout_type", "post_horizontal_row")
                    media_type = "reel" if layout == "reel_vertical_rail" else "post"
                    allow_caption_expand = (media_type != "reel")

                    like_data = parsed.get("like_button", {})
                    comm_data = parsed.get("comment_button", {})

                    like_c = like_data.get("center")
                    if not like_c and "x" in like_data and "y" in like_data:
                        like_c = [like_data["x"], like_data["y"]]

                    comm_c = comm_data.get("center")
                    if not comm_c and "x" in comm_data and "y" in comm_data:
                        comm_c = [comm_data["x"], comm_data["y"]]

                    already_liked = bool(like_data.get("already_liked", False))

                    like_pt = None
                    comment_pt = None

                    if isinstance(like_c, (list, tuple)) and len(like_c) == 2:
                        raw_x, raw_y = float(like_c[0]), float(like_c[1])
                        if 0.0 <= raw_x <= 1.0 and 0.0 <= raw_y <= 1.0:
                            lx = int(raw_x * screen_width)
                            ly = int(raw_y * screen_height)
                        elif raw_x > screen_width and screen_width <= 1000:
                            lx = int(raw_x * (screen_width / 1000.0))
                            ly = int(raw_y * (screen_height / 1000.0))
                        else:
                            lx = int(raw_x)
                            ly = int(raw_y)
                        like_pt = (max(0, min(screen_width - 1, lx)), max(0, min(screen_height - 1, ly)))

                    if isinstance(comm_c, (list, tuple)) and len(comm_c) == 2:
                        raw_x, raw_y = float(comm_c[0]), float(comm_c[1])
                        if 0.0 <= raw_x <= 1.0 and 0.0 <= raw_y <= 1.0:
                            cx = int(raw_x * screen_width)
                            cy = int(raw_y * screen_height)
                        elif raw_x > screen_width and screen_width <= 1000:
                            cx = int(raw_x * (screen_width / 1000.0))
                            cy = int(raw_y * (screen_height / 1000.0))
                        else:
                            cx = int(raw_x)
                            cy = int(raw_y)
                        comment_pt = (max(0, min(screen_width - 1, cx)), max(0, min(screen_height - 1, cy)))

                    logger.info(
                        f"Qwen 2.5-VL layout detectado: {layout} (media_type={media_type}) | "
                        f"Like={like_pt} (already_liked={already_liked}), Comment={comment_pt}"
                    )
                    return {
                        "success": True,
                        "layout_type": layout,
                        "media_type": media_type,
                        "like": like_pt,
                        "comment": comment_pt,
                        "already_liked": already_liked,
                        "allow_caption_expand": allow_caption_expand,
                        "confidence": float(parsed.get("confidence", 0.9)),
                        "reasoning": str(parsed.get("reasoning", ""))
                    }
        except Exception as e:
            logger.warning(f"Aviso consultando Qwen 2.5-VL para layout de Instagram: {e}")

        return {"success": False}

    def verify_following_state_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to inspect a Facebook page/profile screenshot after tapping 'Seguir',
        verifying if the button transitioned to 'Siguiendo' (with bell icon or 'Siguiendo' text)
        or 'Te gusta'.
        """
        logger.info(f"Verificando transición a 'Siguiendo' con modelo de visión '{self.vision_model}'...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo imagen para verificar estado 'Siguiendo': {e}")
            return {"is_following": False, "error": str(e)}

        prompt = (
            "You are an expert AI vision system for mobile UI navigation on Facebook Lite.\n"
            "Analyze this Android screenshot of a Facebook page or profile.\n"
            f"Screen dimensions are {screen_width}x{screen_height} pixels.\n"
            "Goal: Verify if the follow/like button has now transitioned to 'Siguiendo' (Following), shows a bell 🔔 / checkmark icon, or transitioned to 'Te gusta'.\n"
            "Look at the action buttons in the profile header (where 'Seguir' or 'Me gusta' was located).\n"
            "Determine:\n"
            "1. 'is_following': boolean, true if 'Siguiendo', 'Following', 'Te gusta', or bell 🔔 icon is now active/visible.\n"
            "2. 'label': string, detected text on button (e.g. 'Siguiendo', 'Te gusta', 'Seguir').\n"
            "3. 'box_2d': [ymin, xmin, ymax, xmax] bounding box in screen pixels.\n"
            "4. 'confidence': float between 0.0 and 1.0.\n"
            "5. 'reasoning': short explanation in Spanish.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\n"
            '  "is_following": boolean,\n'
            '  "label": "string",\n'
            '  "box_2d": [ymin, xmin, ymax, xmax],\n'
            '  "confidence": float,\n'
            '  "reasoning": "string"\n'
            "}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict):
                    label = (parsed.get("label") or "").strip().lower()
                    reasoning = (parsed.get("reasoning") or "").strip().lower()
                    is_fol = bool(parsed.get("is_following", False))
                    if "seguir" in label or "follow" in label or label == "me gusta" or any(neg in reasoning for neg in ["no ha seguido", "no sigue", "no está siguiendo", "aun no", "aún no"]):
                        is_fol = False
                    elif any(w in label for w in ["siguiendo", "following", "te gusta", "liked"]):
                        is_fol = True
                    elif "no est" not in reasoning and "no sigue" not in reasoning:
                        if any(w in reasoning for w in ["ya sigue", "está siguiendo", "campana", "siguiendo", "te gusta"]):
                            is_fol = True
                    parsed["is_following"] = is_fol
                    logger.info(f"Qwen 2.5-VL verificación 'Siguiendo': {parsed}")
                    return parsed
        except Exception as ex:
            logger.warning(f"Error consultando Qwen 2.5-VL para verificación 'Siguiendo': {ex}")

        return {"is_following": False, "label": "No verificado", "reasoning": "Fallo en inferencia"}

    def locate_back_button_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to detect the Back button icon ('<' or left-arrow) in the
        top-left header bar of Facebook Lite (as shown in user Image 2).
        """
        logger.info(f"Localizando icono de regreso ('<') con modelo de visión '{self.vision_model}'...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo imagen para ubicar botón de regreso: {e}")
            return {"found": False, "error": str(e)}

        prompt = (
            "You are an expert AI vision system for mobile UI navigation on Facebook Lite.\n"
            "Analyze this Android screenshot of a Facebook page.\n"
            f"Screen dimensions are {screen_width}x{screen_height} pixels.\n"
            "Goal: Locate the Back button / icon (an arrow '<' or back chevron pointing left) in the top-left navigation bar.\n"
            "Determine:\n"
            "1. 'found': boolean, true if the back arrow icon '<' in the top-left corner is visible.\n"
            "2. 'box_2d': [xmin, ymin, xmax, ymax] bounding box in screen pixels.\n"
            "3. 'center_coordinates': [x, y] integer pixel center of the back button to tap with ADB.\n"
            "4. 'confidence': float between 0.0 and 1.0.\n"
            "5. 'reasoning': brief explanation in Spanish.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\"found\": true, \"box_2d\": [0, 76, 96, 170], \"center_coordinates\": [48, 123], \"confidence\": 0.98, \"reasoning\": \"...\"}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict) and parsed.get("found"):
                    box_2d = parsed.get("box_2d")
                    center_coords = parsed.get("center_coordinates")

                    calc_x = None
                    calc_y = None

                    if isinstance(box_2d, (list, tuple)) and len(box_2d) == 4:
                        try:
                            v0, v1, v2, v3 = [float(v) for v in box_2d]
                            if max(v0, v1, v2, v3) <= 1.0:
                                calc_x = int(((v0 + v2) / 2.0) * screen_width)
                                calc_y = int(((v1 + v3) / 2.0) * screen_height)
                            else:
                                if v1 > screen_width or v3 > screen_width or (v0 < v1 and v3 > v2):
                                    calc_x = int((v0 + v2) / 2.0)
                                    calc_y = int((v1 + v3) / 2.0)
                                elif v0 > screen_width or v2 > screen_width:
                                    calc_x = int((v1 + v3) / 2.0)
                                    calc_y = int((v0 + v2) / 2.0)
                                else:
                                    calc_x = int((v0 + v2) / 2.0)
                                    calc_y = int((v1 + v3) / 2.0)
                        except Exception as e_b:
                            logger.warning(f"Error parseando box_2d de botón atrás: {e_b}")

                    if (calc_x is None or calc_y is None) and isinstance(center_coords, (list, tuple)) and len(center_coords) == 2:
                        cx, cy = float(center_coords[0]), float(center_coords[1])
                        if cx <= 1.0 and cy <= 1.0:
                            calc_x = int(cx * screen_width)
                            calc_y = int(cy * screen_height)
                        else:
                            calc_x = int(cx)
                            calc_y = int(cy)

                    if calc_x is not None and calc_y is not None:
                        calc_x = max(0, min(screen_width - 1, calc_x))
                        calc_y = max(0, min(screen_height - 1, calc_y))
                        parsed["tap_coordinates"] = (calc_x, calc_y)

                    logger.info(f"Qwen 2.5-VL detectó botón 'Atrás (<)': {parsed}")
                    return parsed
        except Exception as ex:
            logger.warning(f"Error consultando Qwen 2.5-VL para botón atrás: {ex}")

        return {"found": False, "reasoning": "No detectado"}

    def verify_facebook_home_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to inspect a Facebook Lite screenshot and verify if the app
        has returned to the Home / News Feed screen (checking for 'facebook' title/logo,
        top navigation tabs: Home/Friends/Watch, and "¿Qué estás pensando?" composer).
        """
        logger.info(f"Verificando pantalla Home de Facebook con modelo de visión '{self.vision_model}'...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo imagen para verificar Home de Facebook: {e}")
            return {"is_home": False, "error": str(e)}

        prompt = (
            "You are an expert AI vision system for mobile UI navigation on Facebook Lite.\n"
            "Analyze this Android screenshot.\n"
            f"Screen dimensions are {screen_width}x{screen_height} pixels.\n"
            "Goal: Verify if the screen is currently at the Facebook Home / News Feed screen.\n"
            "Look for: 'facebook' title/logo in the top header, navigation tabs (Home icon, Friends, Watch/Video, Notifications, Marketplace), post composer ('¿Qué estás pensando?'), stories, or feed posts.\n"
            "Determine:\n"
            "1. 'is_home': boolean, true if the screen is the Facebook Home / News Feed.\n"
            "2. 'confidence': float between 0.0 and 1.0.\n"
            "3. 'reasoning': short explanation in Spanish.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\"is_home\": true, \"confidence\": 0.98, \"reasoning\": \"...\"}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict):
                    is_home = bool(parsed.get("is_home", False))
                    reasoning = (parsed.get("reasoning") or "").strip().lower()
                    if not is_home and any(w in reasoning for w in ["inicio", "news feed", "feed", "facebook", "estás pensando"]):
                        is_home = True
                    parsed["is_home"] = is_home
                    logger.info(f"Qwen 2.5-VL verificación Facebook Home: {parsed}")
                    return parsed
        except Exception as ex:
            logger.warning(f"Error consultando Qwen 2.5-VL para Home de Facebook: {ex}")

        return {"is_home": False, "reasoning": "Fallo en inferencia"}

    def locate_facebook_menu_button_with_vision(
        self,
        image_path: str,
        screen_width: int = 720,
        screen_height: int = 1612
    ) -> Dict[str, Any]:
        """
        Uses Qwen 2.5-VL to detect the 3 horizontal lines (hamburger menu '☰') icon
        in the top-right header of Facebook Lite (as circled by user in media_1790704649899.jpg).
        """
        logger.info(f"Localizando icono de menú de 3 líneas ('☰') con modelo de visión '{self.vision_model}'...")
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error leyendo imagen para ubicar menú de 3 líneas: {e}")
            return {"found": False, "error": str(e)}

        prompt = (
            "You are an expert AI vision system for mobile UI navigation on Facebook Lite.\n"
            "Analyze this Android screenshot of Facebook Lite Home.\n"
            f"Screen dimensions are {screen_width}x{screen_height} pixels.\n"
            "Goal: Locate the 3 horizontal lines hamburger menu icon (☰) in the top-right header (next to the search magnifying glass icon).\n"
            "Determine:\n"
            "1. 'found': boolean, true if the 3 lines menu icon (☰) is visible in the top-right header.\n"
            "2. 'box_2d': [xmin, ymin, xmax, ymax] bounding box in screen pixels.\n"
            "3. 'center_coordinates': [x, y] integer pixel center of the 3 lines menu button to tap with ADB.\n"
            "4. 'confidence': float between 0.0 and 1.0.\n"
            "5. 'reasoning': short explanation in Spanish.\n\n"
            "Return STRICT JSON only matching this schema:\n"
            "{\"found\": true, \"box_2d\": [615, 76, 705, 170], \"center_coordinates\": [660, 123], \"confidence\": 0.98, \"reasoning\": \"...\"}"
        )

        payload = {
            "model": self.vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "format": "json"
        }

        try:
            res = requests.post(self.base_url, json=payload, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json().get("response", "{}")
                parsed = self._clean_and_parse_json(raw)
                if isinstance(parsed, dict) and parsed.get("found"):
                    box_2d = parsed.get("box_2d")
                    center_coords = parsed.get("center_coordinates")

                    calc_x = None
                    calc_y = None

                    if isinstance(box_2d, (list, tuple)) and len(box_2d) == 4:
                        try:
                            v0, v1, v2, v3 = [float(v) for v in box_2d]
                            if max(v0, v1, v2, v3) <= 1.0:
                                calc_x = int(((v0 + v2) / 2.0) * screen_width)
                                calc_y = int(((v1 + v3) / 2.0) * screen_height)
                            else:
                                if v1 > screen_width or v3 > screen_width or (v0 < v1 and v3 > v2):
                                    calc_x = int((v0 + v2) / 2.0)
                                    calc_y = int((v1 + v3) / 2.0)
                                elif v0 > screen_width or v2 > screen_width:
                                    calc_x = int((v1 + v3) / 2.0)
                                    calc_y = int((v0 + v2) / 2.0)
                                else:
                                    calc_x = int((v0 + v2) / 2.0)
                                    calc_y = int((v1 + v3) / 2.0)
                        except Exception as e_b:
                            logger.warning(f"Error parseando box_2d de menú: {e_b}")

                    if (calc_x is None or calc_y is None) and isinstance(center_coords, (list, tuple)) and len(center_coords) == 2:
                        cx, cy = float(center_coords[0]), float(center_coords[1])
                        if cx <= 1.0 and cy <= 1.0:
                            calc_x = int(cx * screen_width)
                            calc_y = int(cy * screen_height)
                        else:
                            calc_x = int(cx)
                            calc_y = int(cy)

                    if calc_x is not None and calc_y is not None:
                        calc_x = max(0, min(screen_width - 1, calc_x))
                        calc_y = max(0, min(screen_height - 1, calc_y))
                        parsed["tap_coordinates"] = (calc_x, calc_y)

                    logger.info(f"Qwen 2.5-VL detectó botón 'Menú (☰)': {parsed}")
                    return parsed
        except Exception as ex:
            logger.warning(f"Error consultando Qwen 2.5-VL para menú de 3 líneas: {ex}")

        return {"found": False, "reasoning": "No detectado"}

    NARRATIVE_GUIDELINES: Dict[str, str] = NARRATIVE_GUIDELINES

    def generate_human_comment(
        self,
        caption: str,
        image_path: str = "",
        archetype_name: str = "",
        account: str = "",
        contest_info: Optional[Dict[str, Any]] = None,
        narrative: str = "a_favor",
        recent_comments: Optional[List[str]] = None,
        reference_comments: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Generates an authentic human comment with integrated visual and textual analysis,
        guided by persistent memory (agents.md / agent.md).
        Rotates across the 5 human archetypes:
          1. Testigo Silencioso
          2. Buscador
          3. Guerrero Interior
          4. Caminante
          5. Heredero
        Enforces:
          - Máximo 3 líneas
          - Cero emojis (estricto)
          - Sin pronombres de género / neutro
          - Mención orgánica de elementos visuales ("esa mirada", "el entorno", "la postura", "las manos", "el momento")
          - Tono espontáneo desde el celular
          - Memoria persistente: comprueba arquetipos y elementos no usados en los últimos 3 comentarios.
        """
        import random
        caption_clean = caption.strip() if caption else "Publicación en redes sociales"

        # 1. Load persistent memory from agents.md / agent.md
        mem = self.load_persistent_memory()
        recent_archs = mem.get("recent_archetypes", [])
        stored_comments = mem.get("recent_comments", [])
        recent_elements = mem.get("recent_elements", [])

        # Combined recent comments
        all_recent = list(stored_comments[:4])
        if recent_comments:
            for c in recent_comments:
                if c not in all_recent:
                    all_recent.insert(0, c)

        # 2. Select archetype: Rule 1 (¿Qué arquetipo no he usado en los últimos 3 comentarios?)
        assigned_arch = None
        if archetype_name:
            for a in self.HUMAN_ARCHETYPES:
                if a["name"].lower() == str(archetype_name).lower():
                    assigned_arch = a
                    break

        if not assigned_arch:
            candidate_archetypes = [
                a for a in self.HUMAN_ARCHETYPES
                if a["name"] not in recent_archs[:3]
            ]
            if not candidate_archetypes:
                candidate_archetypes = self.HUMAN_ARCHETYPES.copy()
            random.shuffle(candidate_archetypes)
            assigned_arch = candidate_archetypes[0]

        # 3. Visual details analysis: Rule 2 (¿Qué elemento de texto o imagen aún no he mencionado?)
        active_img = image_path
        if not active_img or not os.path.exists(active_img):
            for candidate in ["current_post_screen.png", "screen.png"]:
                if os.path.exists(candidate):
                    active_img = candidate
                    break

        visual_details = self.analyze_visual_details(active_img, caption_clean)
        specific_details = visual_details.get("detalles_especificos", [])
        if not isinstance(specific_details, list) or not specific_details:
            specific_details = assigned_arch.get("elements", ["el momento", "el entorno", "esa mirada"])

        # Pick an unmentioned element
        available_elements = [e for e in specific_details if e not in recent_elements[:4]]
        if not available_elements:
            available_elements = specific_details
        chosen_element = random.choice(available_elements) if available_elements else "el entorno"

        # 4. Memory context for prompt: Rule 3 (¿Estoy repitiendo estructuras o palabras?)
        recent_comments_block = ""
        if all_recent:
            recent_comments_block = "\n".join([f"  - \"{c}\"" for c in all_recent[:3]])
        else:
            recent_comments_block = "  - Ninguno registrado aún."

        # Contest instruction if dynamic is detected
        contest_instruction = ""
        if contest_info and contest_info.get("is_contest"):
            req = contest_info.get("contest_requirement") or "Participar activamente en la dinámica"
            topic = contest_info.get("teams_or_topic") or "Dinámica de la publicación"
            score_pool = ["2-1", "1-0", "2-0", "3-1", "1-1", "3-2"]
            random.shuffle(score_pool)
            diverse_scores = ", ".join(score_pool[:3])
            contest_instruction = (
                f"DINÁMICA / CONCURSO ACTIVO (OBLIGATORIO):\n"
                f"- Requisito del concurso: \"{req}\".\n"
                f"- Tema/Equipos: \"{topic}\".\n"
                f"- Integra tu participación cumpliendo el requisito (ej. pronóstico realista como {diverse_scores} o respuesta a la consigna) "
                f"manteniendo la esencia del arquetipo {assigned_arch['name']}.\n\n"
            )

        # Reference comments provided by user to support context & inspiration
        reference_instruction = ""
        if reference_comments:
            clean_refs = [rc.strip() for rc in reference_comments if isinstance(rc, str) and rc.strip()][:10]
            if clean_refs:
                ref_items = "\n".join([f"  * \"{cr}\"" for cr in clean_refs])
                reference_instruction = (
                    "COMENTARIOS DE REFERENCIA (APOYO AL CONTEXTO):\n"
                    "El usuario proporcionó estos comentarios de ejemplo para apoyar el contexto y estilo. "
                    "Inspírate en estas temáticas o tono, pero genera un comentario propio, original y adaptado a tu arquetipo "
                    "(no copies textualmente ninguno de ellos, mantén 0 emojis y máx 3 líneas):\n"
                    f"{ref_items}\n\n"
                )

        # 5. Build prompt
        prompt = (
            "Actúa como un sistema de comentarios humanos para redes sociales con capacidad de análisis visual y textual integrado.\n\n"
            "PROCESO DE ANÁLISIS:\n"
            "1. Lee el texto del post y extrae: tema, emoción, tono, palabras clave\n"
            "2. Analiza la imagen e identifica: sujetos, acción, entorno, emoción visible, detalles específicos\n"
            "3. Cruza ambas informaciones para encontrar puntos de conexión\n\n"
            "REGLAS INQUEBRANTABLES:\n"
            "- Máximo 3 líneas por comentario\n"
            "- Cero emojis (ESTRICTAMENTE PROHIBIDO CUALQUIER EMOJI O EMOTICONO)\n"
            "- Evitar pronombres de género; si es necesario, usar estructuras neutras o alternar naturalmente\n"
            "- Usar palabras emotivas pero cotidianas\n"
            "- ROTAR entre arquetipos: Testigo Silencioso, Buscador, Guerrero Interior, Caminante, Heredero\n"
            "- Cuando la imagen muestre algo relevante, mencionarlo de forma orgánica (no forzada): \"esa mirada\", \"el entorno\", \"la postura\", \"las manos\", \"el momento\"\n"
            "- Variar estructura: pregunta / afirmación / imagen mental / conexión personal\n"
            "- El tono debe ser de alguien que vio la imagen, leyó el texto, sintió algo y escribió desde el celular sin pensarlo demasiado\n\n"
            "MEMORIA PERSISTENTE:\n"
            "Antes de escribir cada comentario, revisa:\n"
            f"1. ¿Qué arquetipo no he usado en los últimos 3 comentarios?\n"
            f"   - Arquetipos usados recientemente: {', '.join(recent_archs[:3]) or 'ninguno'}\n"
            f"   - Arquetipo seleccionado para ti ahora: '{assigned_arch['name']}' ({assigned_arch['description']})\n"
            f"2. ¿Qué elemento (del texto O de la imagen) aún no he mencionado?\n"
            f"   - Elemento visual sugerido para integrar orgánicamente: \"{chosen_element}\"\n"
            f"3. ¿Estoy repitiendo estructuras o palabras de comentarios anteriores?\n"
            f"   - Comentarios previos a NO repetir:\n{recent_comments_block}\n"
            f"4. ¿Esto suena como algo que escribiría una persona real después de ver la publicación completa (texto + imagen)?\n\n"
            "DATOS DE LA PUBLICACIÓN:\n"
            f"- TEXTO DEL POST: \"{caption_clean}\"\n"
            f"- ANÁLISIS DE LA IMAGEN: Sujetos: {visual_details.get('sujetos')}. Acción: {visual_details.get('accion')}. Entorno: {visual_details.get('entorno')}. Emoción: {visual_details.get('emocion_visible')}.\n"
            f"- PUNTOS DE CONEXIÓN: {visual_details.get('puntos_conexion')}\n\n"
            f"{contest_instruction}"
            f"{reference_instruction}"
            "FORMATO DE SALIDA (Genera exactamente estas 3 líneas, sin texto extra):\n"
            "Comentario: [texto del comentario]\n"
            f"Arquetipo usado: {assigned_arch['name']}\n"
            f"Elemento visual referenciado: {chosen_element}"
        )

        comment_text = ""
        referenced_element = chosen_element
        used_arch_name = assigned_arch["name"]

        ollama_available = self.is_ollama_online(self.base_url, timeout=0.8)
        if ollama_available:
            payload = {
                "model": self.text_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.82,
                    "top_p": 0.9
                }
            }
            try:
                response = requests.post(self.base_url, json=payload, timeout=min(self.timeout, 25))
                if response.status_code == 200:
                    raw = response.json().get("response", "").strip()
                    cleaned_raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

                    for line in cleaned_raw.split("\n"):
                        l_strip = line.strip()
                        if l_strip.lower().startswith("comentario:"):
                            comment_text = l_strip.split(":", 1)[1].strip()
                        elif l_strip.lower().startswith("arquetipo usado:"):
                            used_arch_name = l_strip.split(":", 1)[1].strip()
                        elif l_strip.lower().startswith("elemento visual referenciado:") or l_strip.lower().startswith("elemento visual:"):
                            referenced_element = l_strip.split(":", 1)[1].strip()

                    if not comment_text and cleaned_raw:
                        lines = [l for l in cleaned_raw.split("\n") if l.strip() and not l.lower().startswith("arquetipo") and not l.lower().startswith("elemento")]
                        comment_text = " ".join(lines)
            except Exception as e:
                logger.warning(f"generate_human_comment LLM call failed: {e}")

        # Fallback if empty
        if not comment_text:
            fallbacks = assigned_arch.get("fallbacks", [
                f"Ese instante en {chosen_element} lo resume todo con sinceridad y entrega.",
                f"Hay momentos que conectan de inmediato, se percibe en {chosen_element}."
            ])
            comment_text = random.choice(fallbacks)

        # Enforce Non-Negotiable Rules:
        # Rule: Cero emojis
        comment_text = self.strip_emojis(comment_text)
        # Rule: Máximo 3 líneas
        comment_text = self.enforce_max_3_lines(comment_text)
        # Rule: Clean quotes
        if (comment_text.startswith('"') and comment_text.endswith('"')) or (comment_text.startswith("'") and comment_text.endswith("'")):
            comment_text = comment_text[1:-1].strip()
        # Rule: Evitar pronombres de género / primera persona
        for fp in [r"\byo\b", r"\ba m[ií]\b", r"\bme parece\b", r"\ben mi opini[oó]n\b", r"\bpara m[ií]\b"]:
            comment_text = re.sub(fp, "", comment_text, flags=re.IGNORECASE).strip()
            comment_text = re.sub(r"\s+", " ", comment_text)

        # 6. Record interaction in persistent memory (agents.md / agent.md)
        self.record_agent_memory(
            caption=caption_clean,
            comment=comment_text,
            archetype=assigned_arch,
            narrative=narrative,
            account=account,
            visual_element=referenced_element,
            visual_details=visual_details,
            thought=(
                f"Arquetipo '{used_arch_name}' (rotación sin repetir últimos 3). "
                f"Elemento visual integrado: '{referenced_element}'. "
                f"Verificación: Cero emojis, máximo 3 líneas, sin marcas de género ni 'yo'."
            ),
            filepath="agents.md"
        )

        self.last_archetype = assigned_arch
        self.last_visual_element = referenced_element

        logger.info(
            f"Human comment generated: Archetype='{used_arch_name}', Visual='{referenced_element}': \"{comment_text}\""
        )

        return {
            "comment": comment_text,
            "archetype_name": used_arch_name,
            "visual_element": referenced_element,
            "visual_details": visual_details,
            "memory_verified": True
        }

    def preview_human_archetypes(
        self,
        caption: str,
        image_path: str = "current_post_screen.png",
        contest_info: Optional[Dict[str, Any]] = None,
        narrative: str = "a_favor"
    ) -> List[Dict[str, Any]]:
        """
        Generates preview comments for all 5 Human Archetypes
        (Testigo Silencioso, Buscador, Guerrero Interior, Caminante, Heredero)
        with integrated visual and textual analysis.
        """
        results = []
        recent = []
        for arch in self.HUMAN_ARCHETYPES:
            res = self.generate_human_comment(
                caption=caption,
                image_path=image_path,
                archetype_name=arch["name"],
                contest_info=contest_info,
                narrative=narrative,
                recent_comments=recent
            )
            recent.append(res["comment"])
            results.append({
                "id": arch["id"],
                "name": arch["name"],
                "description": arch["description"],
                "visual_element": res.get("visual_element", "el entorno"),
                "comment": res.get("comment", "")
            })
        return results

    def generate_persona_comments(
        self,
        persona: str,
        caption: str,
        image_path: str = "",
        narrative: str = "a_favor",
        count: int = 3
    ) -> Dict[str, Any]:
        """
        Genera `count` opciones de comentario (por defecto 3) para un PERSONAJE
        definido por el usuario. La persona define la VOZ (edad, tono, vocabulario,
        regionalismo) y cada opción usa un arquetipo distinto como ÁNGULO
        (estructura + elemento visual). No escribe memoria persistente (es una vista previa).
        """
        import random
        persona_clean = (persona or "").strip()
        caption_clean = caption.strip() if caption else "Publicación en redes sociales"
        if not persona_clean:
            return {"options": [], "error": "Debes escribir un personaje."}

        # 1. Elegir arquetipos por rotación (sin repetir los últimos usados)
        mem = self.load_persistent_memory()
        recent_archs = mem.get("recent_archetypes", [])
        candidate = [a for a in self.HUMAN_ARCHETYPES if a["name"] not in recent_archs[:3]]
        if not candidate:
            candidate = self.HUMAN_ARCHETYPES.copy()
        random.shuffle(candidate)
        n = max(1, min(int(count or 3), len(candidate)))
        selected = candidate[:n]

        # 2. Análisis visual (opcional)
        active_img = image_path
        if not active_img or not os.path.exists(active_img):
            for cand in ["current_post_screen.png", "screen.png"]:
                if os.path.exists(cand):
                    active_img = cand
                    break
        visual_details: Dict[str, Any] = {}
        if active_img and os.path.exists(active_img):
            try:
                visual_details = self.analyze_visual_details(active_img, caption_clean)
            except Exception as e:
                logger.warning(f"generate_persona_comments visual analysis failed: {e}")

        # 3. Guía de narrativa
        narrative_guide = self.NARRATIVE_GUIDELINES.get(narrative, self.NARRATIVE_GUIDELINES["a_favor"])

        options = []
        for arch in selected:
            text = self._generate_persona_single(persona_clean, arch, caption_clean, narrative_guide, visual_details)
            options.append({
                "comment": text,
                "archetype_name": arch["name"],
                "persona": persona_clean,
            })

        return {"options": options, "persona": persona_clean}

    def _generate_persona_single(
        self,
        persona: str,
        arch: Dict[str, Any],
        caption: str,
        narrative_guide: str,
        visual_details: Dict[str, Any]
    ) -> str:
        """Genera un único comentario combinando una persona (voz) y un arquetipo (ángulo)."""
        import random
        vis_block = ""
        if visual_details:
            vis_block = (
                f"- ANÁLISIS DE LA IMAGEN: Sujetos: {visual_details.get('sujetos')}. "
                f"Entorno: {visual_details.get('entorno')}. Emoción: {visual_details.get('emocion_visible')}.\n"
            )

        prompt = (
            "Actúa como un generador de comentarios para redes sociales.\n\n"
            f"PERSONAJE / VOZ: {persona}.\n"
            "Escribe exactamente como lo haría ese personaje: su edad, tono, vocabulario, "
            "regionalismo y forma de expresarse deben notarse en el texto.\n\n"
            f"ÁNGULO DEL ARQUETIPO: '{arch['name']}' — {arch['description']}.\n"
            f"Estructura sugerida: {', '.join(arch.get('structures', []))}.\n"
            f"POSICIÓN / NARRATIVA: {narrative_guide}\n\n"
            "REGLAS INQUEBRANTABLES:\n"
            "- Máximo 3 líneas\n"
            "- Cero emojis (prohibido cualquier emoji o emoticono)\n"
            "- Sin primera persona ('yo', 'a mí', 'me parece', 'en mi opinión')\n"
            "- Neutro de género\n"
            "- Si la imagen muestra algo relevante, menciónalo de forma orgánica (\"esa mirada\", \"el entorno\", \"la postura\", \"las manos\", \"el momento\")\n\n"
            "DATOS DE LA PUBLICACIÓN:\n"
            f"- TEXTO DEL POST: \"{caption}\"\n"
            f"{vis_block}"
            "Responde SOLO con el texto del comentario, sin etiquetas, sin comillas y sin texto extra."
        )

        comment_text = ""
        if self.is_ollama_online(self.base_url, timeout=0.8):
            cached = memory_store.llm_get(self.text_model, prompt)
            if cached:
                comment_text = cached
            else:
                payload = {
                    "model": self.text_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.85, "top_p": 0.9}
                }
                try:
                    response = requests.post(self.base_url, json=payload, timeout=min(self.timeout, 25))
                    if response.status_code == 200:
                        raw = response.json().get("response", "").strip()
                        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                        cleaned = re.sub(r"^\s*(comentario|comment)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
                        comment_text = cleaned.strip()
                        if comment_text:
                            memory_store.llm_set(self.text_model, prompt, comment_text, ttl=86400)
                except Exception as e:
                    logger.warning(f"_generate_persona_single LLM call failed: {e}")

        if not comment_text:
            comment_text = random.choice(arch.get("fallbacks", ["Ese instante en el entorno lo resume todo."]))

        # Reglas no negociables
        comment_text = self.strip_emojis(comment_text)
        comment_text = self.enforce_max_3_lines(comment_text)
        if (comment_text.startswith('"') and comment_text.endswith('"')) or (comment_text.startswith("'") and comment_text.endswith("'")):
            comment_text = comment_text[1:-1].strip()
        for fp in [r"\byo\b", r"\ba m[ií]\b", r"\bme parece\b", r"\ben mi opini[oó]n\b", r"\bpara m[ií]\b"]:
            comment_text = re.sub(fp, "", comment_text, flags=re.IGNORECASE).strip()
            comment_text = re.sub(r"\s+", " ", comment_text)

        return comment_text.strip()

    def generate_post_comment(
        self,
        caption: str,
        author: str = "",
        tone: str = "positive",
        recent_comments: Optional[List[str]] = None,
        archetype_id: Optional[Any] = None,
        narrative: str = "a_favor",
        contest_info: Optional[Dict[str, Any]] = None,
        image_path: str = "",
        reference_comments: Optional[List[str]] = None
    ) -> str:
        """
        Generates an authentic, human-like comment for social media posts.
        Defaults to the integrated Human Commenting System (5 human archetypes,
        visual element extraction, zero emojis, and persistent memory in agents.md).
        Maintains backward compatibility if an integer archetype_id (1-6) is explicitly passed.
        """
        # Route to Human Comment System if archetype_id is human archetype name or None/auto/0
        human_names = [a["name"].lower() for a in self.HUMAN_ARCHETYPES]
        if isinstance(archetype_id, str) and archetype_id.lower() in human_names:
            h_res = self.generate_human_comment(
                caption=caption,
                image_path=image_path,
                archetype_name=archetype_id,
                account=author or "",
                contest_info=contest_info,
                narrative=narrative,
                recent_comments=recent_comments,
                reference_comments=reference_comments
            )
            return h_res["comment"]

        if archetype_id is None or archetype_id == "auto" or archetype_id == 0 or (isinstance(archetype_id, str) and not archetype_id.isdigit()):
            h_res = self.generate_human_comment(
                caption=caption,
                image_path=image_path,
                account=author or "",
                contest_info=contest_info,
                narrative=narrative,
                recent_comments=recent_comments,
                reference_comments=reference_comments
            )
            return h_res["comment"]

        import random
        recent_comments = recent_comments or []
        caption_clean = caption.strip() if caption else "Publicación destacada en redes sociales"
        narrative_guide = self.NARRATIVE_GUIDELINES.get(narrative, self.NARRATIVE_GUIDELINES["a_favor"])

        # Auto-detect contest dynamic from caption if not explicitly provided
        if not contest_info:
            caption_lower = caption_clean.lower()
            contest_triggers = [
                "pronóstico", "pronostico", "jersey", "autografiada", "clásico regio",
                "clasico regio", "sorteo", "concurso", "dinámica", "dinamica",
                "quién se la lleva", "quien se la lleva", "ganador", "participar es muy fácil"
            ]
            if any(t in caption_lower for t in contest_triggers):
                is_prediction = any(w in caption_lower for w in ["pronostico", "pronóstico", "marcador", "clásico", "clasico"])
                contest_info = {
                    "is_contest": True,
                    "contest_type": "pronostico" if is_prediction else "sorteo",
                    "contest_requirement": "Comenta tu pronóstico para el partido / Clásico Regio" if is_prediction else "Participar en la dinámica / sorteo",
                    "teams_or_topic": "Tigres vs Rayados (Clásico Regio 143)" if ("tigres" in caption_lower or "rayados" in caption_lower or "clasico" in caption_lower) else "Dinámica interactiva",
                    "sample_comments": ["2-1 Tigres", "1-0 Rayados", "Gana la U 2-0", "Empate 1-1"]
                }

        # Build contest participation instruction if active
        contest_instruction = ""
        if contest_info and contest_info.get("is_contest"):
            req = contest_info.get("contest_requirement") or "Participar activamente en la dinámica"
            topic = contest_info.get("teams_or_topic") or "Clásico Regio Tigres vs Rayados"
            samples = ", ".join(contest_info.get("sample_comments", [])) or "pronósticos del partido"
            score_pool = ["2-1", "1-0", "2-0", "3-1", "1-1", "3-2", "0-1", "1-2"]
            random.shuffle(score_pool)
            diverse_scores = ", ".join(score_pool[:3])

            contest_instruction = (
                f"PARTICIPACIÓN EN CONCURSO / RETO ACTIVO (OBLIGATORIO):\n"
                f"- Esta publicación es un concurso, sorteo o dinámica con el requisito: \"{req}\".\n"
                f"- Tema: \"{topic}\".\n"
                f"- Comentarios de otros usuarios: {samples}.\n"
                f"- Tu comentario DEBE cumplir y participar activamente en el concurso (por ejemplo, incluir un pronóstico con marcador realista como {diverse_scores} o responder a la consigna), "
                f"combinándolo con la personalidad del arquetipo y su regionalismo obligatorio.\n"
                f"- NO uses primera persona ('yo') y sé neutro de género (ejemplo: en vez de 'yo opino que ganamos 2-1' di 'Ese 2-1 a favor de Tigres se ve clarito' o 'Rayados se lo lleva 1-0 con garra').\n\n"
            )

        # Select archetype ensuring diversity
        used_archetype_ids = getattr(self, "_used_archetype_ids", [])
        available_archetypes = [
            a for a in self.REGIOMONTANO_ARCHETYPES
            if a["id"] not in used_archetype_ids[-3:]  # Avoid repeating the last 3 used
        ] or self.REGIOMONTANO_ARCHETYPES

        if archetype_id and 1 <= archetype_id <= 6:
            candidate_archetypes = [a for a in self.REGIOMONTANO_ARCHETYPES if a["id"] == archetype_id]
        else:
            candidate_archetypes = available_archetypes.copy()
            random.shuffle(candidate_archetypes)

        def compute_similarity(s1: str, s2: str) -> float:
            w1 = set(re.findall(r"\b\w{3,}\b", s1.lower()))
            w2 = set(re.findall(r"\b\w{3,}\b", s2.lower()))
            if not w1 or not w2:
                return 0.0
            return len(w1 & w2) / len(w1 | w2)

        # Check Ollama online status before running LLM attempts
        ollama_available = self.is_ollama_online(self.base_url, timeout=0.8)
        if not ollama_available:
            selected_arch = candidate_archetypes[0] if candidate_archetypes else self.REGIOMONTANO_ARCHETYPES[0]
            used_archetype_ids.append(selected_arch["id"])
            self._used_archetype_ids = used_archetype_ids[-6:]
            self.last_archetype = selected_arch
            fallback = self.get_archetype_fallback_comment(
                archetype=selected_arch,
                narrative=narrative,
                contest_info=contest_info,
                caption=caption_clean
            )
            logger.info(
                f"Ollama offline/unreachable. Generated archetype {selected_arch['id']} fallback: \"{fallback}\""
            )
            return fallback

        for attempt, archetype in enumerate(candidate_archetypes[:3]):
            selected_arch = archetype
            logger.info(
                f"Generating comment for @{author} using Archetype {selected_arch['id']}: "
                f"'{selected_arch['name']}' (Regionalism: '{selected_arch['regionalism']}', Narrative: '{narrative}'). Attempt {attempt + 1}."
            )

            prompt = (
                f"Actúa como un generador de comentarios para redes sociales. Tu única tarea es leer una publicación "
                f"o comentario y responder con EXACTAMENTE UN comentario corto, natural y auténtico, adoptando el siguiente arquetipo regiomontano "
                f"y la narrativa indicada:\n\n"
                f"{contest_instruction}"
                f"{narrative_guide}\n\n"
                f"ARQUETIPO ASIGNADO:\n"
                f"{selected_arch['id']}. {selected_arch['name']}: {selected_arch['description']}\n"
                f"Regionalismo OBLIGATORIO: \"{selected_arch['regionalism']}\"\n"
                f"Ejemplo de referencia: {selected_arch['example']}\n\n"
                f"REGLAS OBLIGATORIAS:\n"
                f"1) Longitud: Máximo 1 a 3 líneas (15 a 40 palabras).\n"
                f"2) Naturalidad: Debe sonar como un comentario real de un humano en redes sociales, NUNCA como un texto generado por IA.\n"
                f"3) Regionalismo: DEBES incluir obligatoriamente el modismo \"{selected_arch['regionalism']}\" de forma fluida y natural.\n"
                f"4) Sin meta-texto: Genera SOLAMENTE el comentario final. No expliques qué arquetipo usaste, no pongas etiquetas, no uses comillas al inicio o final.\n"
                f"5) Sin género: El comentario debe ser completamente neutro de género. No uses adjetivos ni palabras que definan el género de quien comenta (evita palabras como 'cansado/a', 'agradecido/a', 'listo/a').\n"
                f"6) Prohibida la primera persona ('yo'): NUNCA hables de ti mismo. Queda estrictamente PROHIBIDO usar 'yo', 'a mí', 'me parece', 'en mi opinión', 'para mí', 'estoy', 'tengo', 'mis tiempos'. Habla de forma impersonal ('se nota', 'se ve'), colectiva ('raza') o dirigiéndote al autor ('sigue así', 'checa bien').\n\n"
                f"FORMATO DE EJECUCIÓN:\n"
                f"Responde ÚNICAMENTE con el comentario adaptado al texto provisto a continuación:\n"
                f"COMENTARIO A RESPONDER: {caption_clean}"
            )

            payload = {
                "model": self.text_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.82,
                    "top_p": 0.9,
                }
            }

            try:
                response = requests.post(self.base_url, json=payload, timeout=min(self.timeout, 20))
                response.raise_for_status()
                api_data = response.json()
                raw_comment = api_data.get("response", "").strip()

                # Clean <think>...</think>
                cleaned = re.sub(r"<think>.*?</think>", "", raw_comment, flags=re.DOTALL).strip()

                # Clean wrapping quotes
                if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
                    cleaned = cleaned[1:-1].strip()

                # Clean common meta prefixes
                for prefix in [
                    "comentario:", "comment:", "respuesta:", "arquetipo:",
                    f"arquetipo {selected_arch['id']}:", f"{selected_arch['name']}:"
                ]:
                    if cleaned.lower().startswith(prefix):
                        cleaned = cleaned[len(prefix):].strip()

                if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
                    cleaned = cleaned[1:-1].strip()

                # Rule check 1: Disallow first-person pronouns
                first_person_patterns = [
                    r"\byo\b", r"\ba m[ií]\b", r"\bme parece\b", r"\ben mi opini[oó]n\b",
                    r"\bpara m[ií]\b", r"\bmis tiempos\b", r"\bcreo que yo\b"
                ]
                has_first_person = any(re.search(pat, cleaned, flags=re.IGNORECASE) for pat in first_person_patterns)
                if has_first_person:
                    logger.warning(f"Comment contains forbidden first-person expressions: '{cleaned}'. Retrying...")
                    continue

                # Rule check 2: Check similarity against recent comments in the cycle
                is_duplicate = False
                for prev in recent_comments:
                    sim = compute_similarity(cleaned, prev)
                    if sim > 0.65 or cleaned.lower() == prev.lower():
                        logger.warning(f"Comment too similar ({sim:.2f}) to previous comment '{prev}'. Retrying...")
                        is_duplicate = True
                        break
                if is_duplicate:
                    continue

                # Validate word count (allow 8 to 45 words)
                words = cleaned.split()
                if len(words) < 8:
                    logger.warning(f"Comment too short ({len(words)} words): '{cleaned}'. Retrying...")
                    continue

                # Success! Record archetype and return
                used_archetype_ids.append(selected_arch["id"])
                self._used_archetype_ids = used_archetype_ids[-6:]
                self.last_archetype = selected_arch

                logger.info(
                    f"Generated verified unique comment with '{self.text_model}' "
                    f"(Archetype {selected_arch['id']} '{selected_arch['name']}', Narrative '{narrative}'): \"{cleaned}\""
                )
                return cleaned

            except Exception as e:
                logger.error(f"Error calling LLM for comment generation (attempt {attempt + 1}): {e}")

        # Fallback ensuring uniqueness and neutrality
        fallback_archetype = candidate_archetypes[0] if candidate_archetypes else self.REGIOMONTANO_ARCHETYPES[1]
        self.last_archetype = fallback_archetype
        fallback = self.get_archetype_fallback_comment(
            archetype=fallback_archetype,
            narrative=narrative,
            contest_info=contest_info,
            caption=caption_clean
        )
        logger.warning(f"Using archetype dynamic fallback: '{fallback}'")
        return fallback

    @classmethod
    def get_archetype_fallback_comment(
        cls,
        archetype: Dict[str, Any],
        narrative: str = "a_favor",
        contest_info: Optional[Dict[str, Any]] = None,
        caption: str = ""
    ) -> str:
        """
        Generates an authentic, gender-neutral, non-first-person comment using
        structured archetype knowledge even if Ollama is unavailable or slow.
        """
        import random
        arch_id = archetype.get("id", 1)
        reg = archetype.get("regionalism", "")

        is_contest = bool(contest_info and contest_info.get("is_contest"))
        is_pred = is_contest and (
            contest_info.get("contest_type") == "pronostico" or
            any(w in (caption + (contest_info.get("contest_requirement") or "")).lower() for w in ["pronostico", "pronóstico", "marcador", "clásico", "clasico"])
        )

        scores = ["2-1 Tigres", "1-0 Rayados", "Gana Tigres 2-0", "Rayados 2-1", "Empate 1-1", "3-1 Tigres", "1-0 Tigres", "Rayados 2-0"]
        chosen_score = random.choice(scores)

        archetype_fallbacks = {
            1: {  # El Escéptico Observador (Chale)
                "a_favor": [
                    f"{reg}, hay que reconocer cuando las cosas están bien hechas y con sustancia. Gran nivel.",
                    f"{reg}, da gusto encontrarse contenido que realmente aporta y no solo busca vistas fáciles.",
                    f"{reg}, buen trabajo en esa publicación, se nota la preparación detrás de cada detalle."
                ],
                "participacion": [
                    f"{reg}, ¿y qué opinan del resultado final? Estaría bueno contrastar con más datos.",
                    f"{reg}, interesante planteamiento, aunque falta ver cómo reacciona la gente en la práctica.",
                    f"{reg}, ¿alguien más tiene una perspectiva diferente o todos coinciden con lo publicado?"
                ],
                "hate": [
                    f"{reg}, otra vez con la misma historia mal contada. Mejor investigar un poco más antes de publicar eso.",
                    f"{reg}, mucho ruido y pocas nueces con esa narrativa, le falta bastante fundamento real.",
                    f"{reg}, vender eso como novedad cuando lleva años igual es querer tapar el sol con un dedo."
                ],
                "neutro": [
                    f"{reg}, los números y hechos expuestos hablan por sí solos, nada más que agregar.",
                    f"{reg}, un recuento directo de la situación actual sin tanto adorno ni exageración."
                ],
                "concurso": [
                    f"{reg}, ese marcador {chosen_score} se ve cantado para el partido, no le busquen más.",
                    f"{reg}, difícil que termine diferente a un {chosen_score}, apunten bien ese pronóstico."
                ]
            },
            2: {  # El Animador Leal (Fierro)
                "a_favor": [
                    f"¡{reg}! Así se hace, sin miedo al éxito. Sigue echándole que ese contenido tiene mucho nivel.",
                    f"¡{reg} con todo! Da orgullo ver publicaciones con tanta energía positiva y dedicación.",
                    f"¡{reg}! Excelente proyecto y excelente vibra, a seguir creciendo con todo el entusiasmo."
                ],
                "participacion": [
                    f"¡{reg}! ¿Quién más se suma a la dinámica? ¡A ver quién saca la mejor respuesta!",
                    f"¡{reg}! Esto se va a poner buenísimo, ¿cómo ven las posibilidades para lo que viene?"
                ],
                "hate": [
                    f"¡{reg}, pero para abajo! Mucho show armado y al final del día no deja nada útil.",
                    f"¡{reg}, qué decepción! Con tanto potencial y terminaron sacando algo tan flojo."
                ],
                "neutro": [
                    f"{reg}, la actividad está en marcha y cada quien sacará sus propias conclusiones del evento.",
                    f"{reg}, los detalles presentados marcan el inicio de una nueva etapa a seguir de cerca."
                ],
                "concurso": [
                    f"¡{reg}! Ese {chosen_score} se viene con garra y entrega total en la cancha.",
                    f"¡{reg} con todo! Apunten {chosen_score} para llevarse ese premio merecido."
                ]
            },
            3: {  # El Consejero Nostálgico (Morro)
                "a_favor": [
                    f"Mira {reg.lower()}, cuando las cosas se trabajan con constancia y pasión los frutos caen solos.",
                    f"Así mero, {reg.lower()}, cuidando cada paso es como se construyen los proyectos duraderos.",
                    f"Da gusto ver proyectos con esa seriedad, {reg.lower()}, sigan con ese mismo ritmo."
                ],
                "participacion": [
                    f"Ojo ahí {reg.lower()}, ¿qué camino convendría tomar ahora para mantener ese impacto?",
                    f"Interesante dinámica, {reg.lower()}, ¿quién recuerda cuándo fue la última vez que pasó algo así?"
                ],
                "hate": [
                    f"Aguanta el viaje {reg.lower()}, no todo lo que brilla en redes es oro ni conviene aplaudir a ciegas.",
                    f"Con calma, {reg.lower()}, antes de celebrar conviene ver si de verdad sostienen ese nivel."
                ],
                "neutro": [
                    f"Cada etapa tiene sus retos, {reg.lower()}, y lo expuesto aquí refleja claramente ese proceso.",
                    f"Las experiencias previas marcan la pauta, {reg.lower()}, habrá que seguir observando."
                ],
                "concurso": [
                    f"Mira {reg.lower()}, ese clásico huele a {chosen_score} trabado de principio a fin, apúntenle.",
                    f"Con calma {reg.lower()}, ese {chosen_score} tiene toda la lógica para este partido."
                ]
            },
            4: {  # El Realista Pragmático (Está cañón)
                "a_favor": [
                    f"Lograr ese nivel de detalle con tan poco presupuesto {reg.lower()}. Se nota el desvelo, respeto total.",
                    f"Coordinar todo eso impecable {reg.lower()}. Muy buen resultado y excelente ejecución.",
                    f"Mantener esa consistencia {reg.lower()}. Gran mérito a todo el equipo involucrado."
                ],
                "participacion": [
                    f"Equilibrar calidad y tiempo en algo así {reg.lower()}, ¿cuánto tiempo tomó planearlo?",
                    f"Resolver ese reto {reg.lower()}, ¿qué método funcionó mejor para sacarlo adelante?"
                ],
                "hate": [
                    f"Vender eso como gran novedad {reg.lower()}, si lleva años viéndose exactamente igual.",
                    f"Disimular la falta de contenido con tanto filtro {reg.lower()}, no convence a nadie."
                ],
                "neutro": [
                    f"Analizar los resultados fríos {reg.lower()}, pero los números reportados son claros.",
                    f"La logística de un evento así {reg.lower()}, se aprecian los hechos tal cual son."
                ],
                "concurso": [
                    f"Pronosticar el partido {reg.lower()} pero ese {chosen_score} se perfila como el más certero.",
                    f"Acertar el marcador {reg.lower()}, pero {chosen_score} tiene argumentos de sobra."
                ]
            },
            5: {  # El Guardián de la Comunidad (Ponte trucha)
                "a_favor": [
                    f"{reg} raza, este tipo de publicaciones son las que aportan valor real y vale la pena apoyar.",
                    f"{reg} gente, cuando se apoya el talento de verdad toda la comunidad sale ganando.",
                    f"{reg} raza, iniciativas con tanta transparencia merecen ser compartidas y reconocidas."
                ],
                "participacion": [
                    f"{reg} con las fechas y bases, ¿hasta cuándo hay oportunidad de sumarse?",
                    f"{reg} raza, ¿qué opinan de los términos que se están manejando para esta ocasión?"
                ],
                "hate": [
                    f"{reg} raza, no se dejen llevar por el puro título que el contenido viene bastante flojo.",
                    f"{reg} gente, no todo lo que prometen se cumple, conviene revisar bien antes de confiar."
                ],
                "neutro": [
                    f"{reg} raza, tomen nota de las indicaciones oficiales compartidas en el post.",
                    f"{reg} comunidad, la información está dada de forma directa y sin rodeos."
                ],
                "concurso": [
                    f"{reg} raza con el pronóstico para el sorteo: {chosen_score} bien plantado.",
                    f"{reg} gente, apunten {chosen_score} para participar y asegurar lugar en la dinámica."
                ]
            },
            6: {  # El Buscador de Autenticidad (La neta)
                "a_favor": [
                    f"{reg}: da gusto ver contenido honesto, bien planteado y sin pretensiones exageradas.",
                    f"{reg}: publicaciones así de claras y directas se agradecen bastante entre tanto relleno.",
                    f"{reg}: cuando hay calidad genuina no hacen falta trucos, gran trabajo."
                ],
                "participacion": [
                    f"{reg}, ¿alguien aquí ha vivido una experiencia similar con este tipo de proyectos?",
                    f"{reg}, ¿cuál consideran que fue el factor decisivo en todo lo que se muestra?"
                ],
                "hate": [
                    f"{reg}: ese discurso suena muy bonito en papel, pero en la práctica no le sirve a nadie.",
                    f"{reg}: se nota bastante forzado todo el asunto, un poco más de autenticidad no vendría mal."
                ],
                "neutro": [
                    f"{reg}: los hechos descritos corresponden fielmente a lo sucedido, sin más ni menos.",
                    f"{reg}: una perspectiva directa y mesurada de lo que aconteció."
                ],
                "concurso": [
                    f"{reg}: {chosen_score} y no hay para dónde hacerse, ese marcador se cobra.",
                    f"{reg}: ese partido pinta clarito para {chosen_score}, apunten la jugada."
                ]
            }
        }

        arch_data = archetype_fallbacks.get(arch_id, archetype_fallbacks[1])
        if is_pred and "concurso" in arch_data:
            options = arch_data["concurso"]
        else:
            options = arch_data.get(narrative, arch_data.get("a_favor", []))

        return random.choice(options)

    def analyze_post_context(self, text: str) -> Dict[str, Any]:
        """
        Analyzes the post text/caption to determine what the post is about,
        its key topic, emotional/commercial tone, whether it involves a contest/raffle/prediction,
        and provides an actionable summary and guidance before running automations.
        """
        import re
        text_clean = text.strip() if text else ""
        if not text_clean:
            return {
                "summary": "Sin descripción proporcionada. La IA operará con arquetipos regios estándar.",
                "topic": "General / Social",
                "tone": "Neutro",
                "is_contest": False,
                "contest_requirement": "",
                "teams_or_topic": "",
                "suggested_approach": "Escribe o pega la descripción del post para un análisis específico.",
                "keywords": []
            }

        if self.is_ollama_online(self.base_url, timeout=0.8):
            prompt = (
                "Eres un analista experto de redes sociales. Analiza la siguiente publicación de Instagram en español:\n\n"
                f'"{text_clean[:2000]}"\n\n'
                "Responde ÚNICAMENTE un JSON válido con la siguiente estructura:\n"
                "{\n"
                '  "summary": "Resumen claro de 1 a 2 oraciones de qué trata la publicación",\n'
                '  "topic": "Tema principal (ej. Fútbol / Deportes, Moda, Negocios, Sorteo, Humor, etc.)",\n'
                '  "tone": "Tono de la publicación (ej. Entusiasta, Informativo, Competitivo, Comercial, Polémico)",\n'
                '  "is_contest": true si incluye sorteo, concurso, dinámicas, premios o pronósticos; false en caso contrario,\n'
                '  "contest_requirement": "Requisito específico si es concurso (ej. pronóstico, etiquetar amigos) o vacío",\n'
                '  "teams_or_topic": "Equipos, figuras o marcas clave involucradas (o vacío)",\n'
                '  "suggested_approach": "Consejo breve de 1 frase para enfocar los comentarios de forma natural y efectiva",\n'
                '  "keywords": ["palabra1", "palabra2", "palabra3"]\n'
                "}"
            )
            payload = {
                "model": self.text_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.3}
            }
            try:
                res = requests.post(self.base_url, json=payload, timeout=12)
                if res.status_code == 200:
                    raw = res.json().get("response", "{}")
                    parsed = self._clean_and_parse_json(raw)
                    if parsed and isinstance(parsed, dict) and parsed.get("summary"):
                        parsed["summary"] = (parsed.get("summary") or "Publicación analizada.").strip()
                        parsed["topic"] = parsed.get("topic") or "General / Social"
                        parsed["tone"] = parsed.get("tone") or "Neutro"
                        parsed["is_contest"] = bool(parsed.get("is_contest", False))
                        parsed["contest_requirement"] = (parsed.get("contest_requirement") or "").strip()
                        parsed["teams_or_topic"] = (parsed.get("teams_or_topic") or "").strip()
                        if "keywords" not in parsed or not parsed["keywords"]:
                            parsed["keywords"] = re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{5,}\b", text_clean)[:5]
                        return parsed
            except Exception as e:
                logger.warning(f"Ollama analyze_post_context exception: {e}")

        # Intelligent heuristic fallback
        t_low = text_clean.lower()
        is_c = any(w in t_low for w in [
            "sorteo", "concurso", "dinamica", "dinámica", "giveaway", "jersey", "gana",
            "ganador", "pronostico", "pronóstico", "marcador", "boletos", "quién se la lleva"
        ])
        is_fut = any(w in t_low for w in [
            "tigres", "rayados", "clasico", "clásico", "futbol", "fútbol", "partido", "estadio", "cancha", "gol", "jornada"
        ])
        is_promo = any(w in t_low for w in ["descuento", "promoción", "promocion", "código", "codigo", "tienda", "compra", "oferta"])

        if is_fut:
            topic = "Deportes / Fútbol (Clásico Regio)"
            tone = "Pasional / Competitivo"
        elif is_c:
            topic = "Concurso / Dinámica Social"
            tone = "Festivo / Interactivo"
        elif is_promo:
            topic = "Comercial / Promoción"
            tone = "Promocional"
        else:
            topic = "Social / Entretenimiento"
            tone = "Informativo / Cotidiano"

        req = ""
        if is_c:
            if any(w in t_low for w in ["pronostico", "pronóstico", "marcador", "resultado"]):
                req = "Comentar pronóstico del partido / marcador exacto"
            elif any(w in t_low for w in ["etiqueta", "amigos", "menciona"]):
                req = "Mencionar / etiquetar a amigos en el comentario"
            else:
                req = "Comentar en la publicación para entrar al sorteo"

        teams = ""
        if "tigres" in t_low and "rayados" in t_low:
            teams = "Tigres UANL vs Rayados de Monterrey"
        elif "tigres" in t_low:
            teams = "Tigres UANL"
        elif "rayados" in t_low:
            teams = "Rayados de Monterrey"

        first_sentence = text_clean.split("\n")[0].strip()
        if len(first_sentence) > 140:
            first_sentence = first_sentence[:140] + "..."

        approach = "Comentar con entusiasmo regiomontano apoyando la dinámica." if is_c else "Comentar con opiniones directas y auténticas destacando puntos clave."

        return {
            "summary": f"Publicación de {topic.lower()}. Contenido principal: '{first_sentence}'",
            "topic": topic,
            "tone": tone,
            "is_contest": is_c,
            "contest_requirement": req,
            "teams_or_topic": teams,
            "suggested_approach": approach,
            "keywords": re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{5,}\b", text_clean)[:6]
        }

    def generate_comment_samples_by_narrative(
        self,
        caption: str,
        contest_info: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generates 4 distinct, representative comment examples covering each narrative stance:
        1. A favor (apoyo positivo con arquetipo regio animador/leal)
        2. Participación (conversacional/debate con arquetipo consejero)
        3. Crítico / Hate (escepticismo/cuestionamiento con arquetipo observador)
        4. Neutro (hechos y análisis pragmático)
        """
        configs = [
            {
                "narrative": "a_favor",
                "label": "A Favor / Apoyo",
                "badge": "👍 Apoyo Entusiasta",
                "badge_color": "emerald",
                "archetype_id": 2,  # El Animador Leal ("Fierro")
                "description": "Destaca lo bien hecho, motiva y aporta energía positiva."
            },
            {
                "narrative": "participacion",
                "label": "Participación / Pregunta",
                "badge": "💬 Conversacional",
                "badge_color": "sky",
                "archetype_id": 3,  # El Consejero Nostálgico ("Morro")
                "description": "Lanza una pregunta reflexiva o invita a opinar a la comunidad."
            },
            {
                "narrative": "hate",
                "label": "Crítico / Desacuerdo",
                "badge": "⚡ Crítico / Hate",
                "badge_color": "rose",
                "archetype_id": 1,  # El Escéptico Observador ("Chale")
                "description": "Cuestiona con ironía o mordacidad sin usar groserías baneables."
            },
            {
                "narrative": "neutro",
                "label": "Neutro / Hechos",
                "badge": "⚖️ Observación",
                "badge_color": "slate",
                "archetype_id": 4,  # El Realista Pragmático ("Está cañón")
                "description": "Comenta datos objetivos y realistas de forma equilibrada."
            }
        ]

        results = []
        recent = []
        for c in configs:
            comment = self.generate_post_comment(
                caption=caption,
                archetype_id=c["archetype_id"],
                narrative=c["narrative"],
                recent_comments=recent,
                contest_info=contest_info
            )
            recent.append(comment)
            arch = self.get_archetype(c["archetype_id"])
            results.append({
                "narrative": c["narrative"],
                "label": c["label"],
                "badge": c["badge"],
                "badge_color": c["badge_color"],
                "description": c["description"],
                "archetype_id": arch["id"],
                "archetype_name": arch["name"],
                "regionalism": arch["regionalism"],
                "comment": comment
            })
        return results

    def generate_account_cycle_preview(
        self,
        caption: str,
        accounts_count: int = 3,
        narrative: str = "a_favor",
        contest_info: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generates simulated comments for N rotating accounts, ensuring diverse archetypes
        and unique non-repetitive phrasing for each account before running the cycle.
        """
        accounts_count = max(1, min(accounts_count, 12))
        results = []
        recent = []
        for i in range(accounts_count):
            arch_id = (i % len(self.REGIOMONTANO_ARCHETYPES)) + 1
            arch = self.get_archetype(arch_id)
            comment = self.generate_post_comment(
                caption=caption,
                archetype_id=arch_id,
                narrative=narrative,
                recent_comments=recent,
                contest_info=contest_info
            )
            recent.append(comment)
            results.append({
                "account_index": i + 1,
                "account_label": f"Cuenta {i + 1}",
                "archetype_id": arch["id"],
                "archetype_name": arch["name"],
                "regionalism": arch["regionalism"],
                "narrative": narrative,
                "comment": comment
            })
        return results

    def get_archetype(self, arch_id: int) -> Dict[str, Any]:
        """Returns the dictionary definition for the specified archetype ID (1-6)."""
        for a in self.REGIOMONTANO_ARCHETYPES:
            if a["id"] == int(arch_id):
                return a
        return self.REGIOMONTANO_ARCHETYPES[0]

    def preview_all_archetypes(
        self,
        caption: str,
        narrative: str = "a_favor",
        contest_info: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generates a preview of comments for all 6 Regiomontano archetypes according
        to the specified narrative (a_favor, participacion, hate, neutro).
        """
        results = []
        recent = []
        for arch in self.REGIOMONTANO_ARCHETYPES:
            comment = self.generate_post_comment(
                caption=caption,
                archetype_id=arch["id"],
                narrative=narrative,
                recent_comments=recent,
                contest_info=contest_info
            )
            recent.append(comment)
            results.append({
                "id": arch["id"],
                "name": arch["name"],
                "archetype_id": arch["id"],
                "archetype_name": arch["name"],
                "regionalism": arch["regionalism"],
                "description": arch["description"],
                "narrative": narrative,
                "comment": comment,
                "word_count": len(comment.split())
            })
        return results

    @staticmethod
    def record_agent_memory(
        caption: str,
        narrative: str = "a_favor",
        archetype: Any = None,
        comment: str = "",
        account: str = "",
        thought: str = "",
        filepath: str = "agents.md",
        visual_element: str = "",
        visual_details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Appends an autonomous reflection entry to agents.md (and agent.md),
        preserving operational parameters, chosen archetype, referenced visual element,
        and persistent memory verification.
        """
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        arch_name = "Human Persona"
        regionalism = ""
        if isinstance(archetype, dict):
            arch_name = archetype.get("name", "Arquetipo")
            regionalism = archetype.get("regionalism", "")
        elif isinstance(archetype, str) and archetype:
            arch_name = archetype

        vis_elem = visual_element.strip() if visual_element else "el entorno"

        vis_summary = ""
        if visual_details and isinstance(visual_details, dict):
            suj = visual_details.get("sujetos", "")
            ent = visual_details.get("entorno", "")
            emo = visual_details.get("emocion_visible", "")
            vis_summary = f"Sujetos: {suj} | Entorno: {ent} | Emoción: {emo}"
        else:
            vis_summary = "Análisis visual y textual cruzado."

        if not thought:
            thought = (
                f"Arquetipo '{arch_name}' seleccionado por rotación de memoria persistente. "
                f"Referencia visual orgánica: '{vis_elem}'. Verificado: Cero emojis, máximo 3 líneas, "
                f"estricta neutralidad de género y sin referencias en primera persona ('yo')."
            )

        entry = (
            f"\n### Interacción [{timestamp}] - {account or 'Cuenta Activa'}\n"
            f"- **Contexto de Publicación:** \"{caption[:250].strip()}...\"\n"
            f"- **Análisis Visual:** {vis_summary}\n"
            f"- **Arquetipo Usado:** {arch_name}" + (f" (`{regionalism}`)" if regionalism else "") + "\n"
            f"- **Elemento Visual Referenciado:** \"{vis_elem}\"\n"
            f"- **Comentario Generado:** \"{comment}\"\n"
            f"- **Memoria Persistente / Verificación:** {thought}\n"
            f"---\n"
        )

        # Registrar también en SQLite (agent_reflections) como fuente de verdad consultable
        try:
            memory_store.record_reflection(
                account=account or "Cuenta Activa",
                caption=(caption or "")[:250],
                archetype=arch_name,
                visual_element=vis_elem,
                comment=comment,
                thought=thought,
            )
        except Exception as e:
            logger.warning(f"Could not record reflection in SQLite: {e}")

        for target in [filepath, "agents.md", "agent.md"]:
            try:
                real_p = os.path.realpath(target)
                with open(real_p, "a", encoding="utf-8") as f:
                    f.write(entry)
                logger.info(f"Agent memory recorded in '{real_p}' for {account or 'cycle'}.")
                break
            except Exception as e:
                logger.warning(f"Could not append to '{target}': {e}")


# Module-level aliases
record_agent_memory = AIAgent.record_agent_memory

