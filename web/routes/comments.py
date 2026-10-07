"""
web.routes.comments - Blueprint de generacion de comentarios con IA local.

Rutas:
    POST /api/comments/preview          -> vista previa por arquetipo/narrativa
    POST /api/comments/generate-persona -> 3 opciones para un personaje del usuario
"""

from flask import Blueprint, jsonify, request, Response

import config
from config import logger
from core.ai.agent import AIAgent

comments_bp = Blueprint("comments", __name__, url_prefix="/api/comments")


@comments_bp.route("/preview", methods=["POST"])
def api_comments_preview() -> Response:
    """Generates a preview of comments for all 6 Regiomontano archetypes according to chosen narrative and contest mode."""
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    narrative = data.get("narrative", "a_favor").strip()
    archetype_id = data.get("archetype_id", "all")
    t_model = data.get("text_model", "").strip() or config.TEXT_MODEL
    is_contest = bool(data.get("is_contest", False))
    contest_req = data.get("contest_requirement", "").strip()
    teams_topic = data.get("teams_or_topic", "").strip()

    if not text:
        return jsonify({"success": False, "error": "Debes ingresar o pegar el texto de una publicación para simular."}), 400

    contest_info = None
    if is_contest or contest_req:
        contest_info = {
            "is_contest": True,
            "contest_type": "pronostico" if any(w in (contest_req + text).lower() for w in ["pronostico", "pronóstico", "marcador", "clásico", "clasico"]) else "sorteo",
            "contest_requirement": contest_req or "Comentar pronóstico del partido",
            "teams_or_topic": teams_topic or "Clásico Regio Tigres vs Rayados",
            "sample_comments": ["2-1 Tigres", "1-0 Rayados", "Gana la U 2-0", "Empate 1-1"]
        }

    try:
        agent = AIAgent(text_model=t_model)
        human_names = [a["name"].lower() for a in agent.HUMAN_ARCHETYPES]

        if str(archetype_id).lower() == "human_all":
            h_samples = agent.preview_human_archetypes(
                caption=text,
                image_path="current_post_screen.png",
                contest_info=contest_info,
                narrative=narrative
            )
            previews = []
            for hs in h_samples:
                previews.append({
                    "id": hs["id"],
                    "name": hs["name"],
                    "archetype_id": hs["id"],
                    "archetype_name": hs["name"],
                    "regionalism": hs.get("visual_element", "el entorno"),
                    "visual_element": hs.get("visual_element", "el entorno"),
                    "description": hs.get("description", ""),
                    "narrative": narrative,
                    "comment": hs["comment"],
                    "word_count": len(hs["comment"].split()),
                    "contest_info": contest_info,
                    "is_human": True
                })
        elif str(archetype_id).lower() in human_names:
            h_res = agent.generate_human_comment(
                caption=text,
                image_path="current_post_screen.png",
                archetype_name=str(archetype_id),
                contest_info=contest_info,
                narrative=narrative
            )
            matched = next((a for a in agent.HUMAN_ARCHETYPES if a["name"].lower() == str(archetype_id).lower()), {})
            item = {
                "id": matched.get("id", 1),
                "name": matched.get("name", str(archetype_id)),
                "archetype_id": matched.get("id", 1),
                "archetype_name": matched.get("name", str(archetype_id)),
                "regionalism": h_res.get("visual_element", "el entorno"),
                "visual_element": h_res.get("visual_element", "el entorno"),
                "description": matched.get("description", ""),
                "narrative": narrative,
                "comment": h_res["comment"],
                "word_count": len(h_res["comment"].split()),
                "contest_info": contest_info,
                "is_human": True
            }
            previews = [item]
        elif archetype_id and str(archetype_id).isdigit():
            arch_int = int(archetype_id)
            arch_obj = agent.get_archetype(arch_int)
            comment = agent.generate_post_comment(
                caption=text,
                archetype_id=arch_int,
                narrative=narrative,
                contest_info=contest_info
            )
            item = {
                "id": arch_obj["id"],
                "name": arch_obj["name"],
                "archetype_id": arch_obj["id"],
                "archetype_name": arch_obj["name"],
                "regionalism": arch_obj["regionalism"],
                "description": arch_obj["description"],
                "narrative": narrative,
                "comment": comment,
                "word_count": len(comment.split()),
                "contest_info": contest_info
            }
            previews = [item]
        else:
            previews = agent.preview_all_archetypes(
                caption=text,
                narrative=narrative,
                contest_info=contest_info
            )

        return jsonify({
            "success": True,
            "narrative": narrative,
            "total": len(previews),
            "results": previews,
            "previews": previews,
            "contest_info": contest_info
        })
    except Exception as e:
        logger.error(f"Error generating comment previews: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@comments_bp.route("/generate-persona", methods=["POST"])
def api_comments_generate_persona() -> Response:
    """Genera hasta 3 opciones de comentario para un personaje definido por el usuario."""
    data = request.get_json() or {}
    persona = (data.get("persona") or "").strip()
    text = (data.get("text") or data.get("caption") or "").strip()
    narrative = (data.get("narrative") or "a_favor").strip()
    count = int(data.get("count") or 3)
    t_model = (data.get("text_model") or "").strip() or config.TEXT_MODEL

    if not persona:
        return jsonify({"success": False, "error": "Debes escribir un personaje."}), 400
    if not text:
        return jsonify({"success": False, "error": "Debes ingresar el texto de una publicación."}), 400

    try:
        agent = AIAgent(text_model=t_model)
        result = agent.generate_persona_comments(
            persona=persona,
            caption=text,
            image_path="current_post_screen.png",
            narrative=narrative,
            count=count
        )
        return jsonify({
            "success": True,
            "persona": result.get("persona", persona),
            "narrative": narrative,
            "total": len(result.get("options", [])),
            "options": result.get("options", [])
        })
    except Exception as e:
        logger.error(f"Error generating persona comments: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

