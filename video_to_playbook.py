# SPDX-License-Identifier: GPL-3.0-or-later
"""
video_to_playbook.py - Autonomous SOP & Playbook Generator from User Demonstration
Converts user screen recordings or demonstrations into structured Standard Operating Procedures (Playbooks)
and saves them directly into the agent's persistent memory (agent_brain.json).
"""

import os
import re
import json
import time
from typing import Dict, Any, List, Optional
from agent_brain import brain
from media_transcriber import transcriber


class VideoToPlaybookEngine:
    def __init__(self, workspace_dir: str = None):
        self.workspace_dir = workspace_dir or os.path.join(os.path.expanduser("~"), "Downloads", "agent_workspace", "playbooks")
        os.makedirs(self.workspace_dir, exist_ok=True)

    def process_demonstration(
        self,
        media_path_or_url: str,
        playbook_name: str,
        description: str = "",
        platform: str = "general",
        manual_steps: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Takes a recorded video, audio demonstration, or manual steps and produces
        a persistent, structured Playbook for any autonomous AI model.
        """
        playbook_name = re.sub(r"[^a-zA-Z0-9_-]", "_", playbook_name.strip().lower())
        steps = manual_steps or []

        # 1. Attempt transcription if media exists
        transcript_data = None
        if os.path.exists(media_path_or_url) or "youtu" in media_path_or_url:
            transcript_data = transcriber.get_transcript(media_path_or_url)
            if transcript_data.get("ok") and transcript_data.get("full_text"):
                spoken_text = transcript_data["full_text"]
                # Convert speech sentences into procedure steps if steps weren't manually provided
                if not steps:
                    sentences = [s.strip() for s in re.split(r"[.;\n]+", spoken_text) if len(s.strip()) > 10]
                    steps = sentences[:12] if sentences else ["1. Iniciar sesión en la plataforma.", "2. Ejecutar la acción indicada."]

        if not steps:
            steps = [
                f"1. Acceder al recurso o URL indicada para '{playbook_name}'.",
                "2. Localizar el elemento principal de acción.",
                "3. Confirmar la operación y guardar el resultado."
            ]

        # 2. Persist to Brain
        saved = brain.save_playbook(
            name=playbook_name,
            description=description or f"Procedimiento autónomo para '{playbook_name}' ({platform})",
            steps=steps,
            platform=platform
        )

        return {
            "ok": True,
            "playbook_name": playbook_name,
            "platform": platform,
            "steps_count": len(steps),
            "steps": steps,
            "has_media_transcript": bool(transcript_data and transcript_data.get("ok")),
            "brain_saved": bool(saved),
            "message": f"Playbook '{playbook_name}' generado y guardado en el cerebro del agente con {len(steps)} pasos."
        }


# Singleton instance
video_to_playbook = VideoToPlaybookEngine()
