# SPDX-License-Identifier: GPL-3.0-or-later
"""
agent_brain.py - Persistent Memory, Self-Reflective Learning & Playbook Engine for GHS
Stores procedural playbooks (from video demonstrations), lessons learned,
and UI interaction rules so the autonomous agent improves over time without model fine-tuning.
"""

import os
import json
import time
from typing import List, Dict, Any, Optional

DEFAULT_WORKSPACE_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "agent_workspace")
DEFAULT_BRAIN_PATH = os.path.join(DEFAULT_WORKSPACE_DIR, "agent_brain.json")


class AgentBrain:
    def __init__(self, brain_file: str = DEFAULT_BRAIN_PATH):
        self.brain_file = brain_file
        self.data: Dict[str, Any] = {
            "version": 2,
            "updated_at": 0,
            "lessons": [],
            "playbooks": {},
            "procedures": {},
            "criteria": {}
        }
        self._load()

    def _ensure_dir(self):
        folder = os.path.dirname(self.brain_file)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

    def _load(self):
        try:
            if os.path.exists(self.brain_file):
                with open(self.brain_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        self.data = json.loads(content)
        except Exception as e:
            print(f"[AgentBrain] Error loading brain: {e}")

    def _save(self):
        try:
            self._ensure_dir()
            self.data["updated_at"] = int(time.time())
            with open(self.brain_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[AgentBrain] Error saving brain: {e}")

    def add_lesson(self, category: str, situation: str, issue_or_mistake: str, solution: str, golden_rule: str) -> Dict[str, Any]:
        """
        Records a mistake, quirk, or edge case and the discovered solution so the AI avoids it in the future.
        Works across ANY platform (e-commerce, university, CRM, desktop app).
        """
        lesson = {
            "id": len(self.data.get("lessons", [])) + 1,
            "timestamp": int(time.time()),
            "category": category,  # e.g., 'web_ui', 'auth', 'scraping', 'grading', 'e_commerce'
            "situation": situation,
            "issue": issue_or_mistake,
            "solution": solution,
            "golden_rule": golden_rule
        }
        self.data.setdefault("lessons", []).append(lesson)
        self._save()
        return lesson

    def get_lessons(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        lessons = self.data.get("lessons", [])
        if category:
            return [l for l in lessons if l.get("category") == category]
        return lessons

    def save_playbook(self, name: str, description: str, steps: List[str], platform: str = "general") -> Dict[str, Any]:
        """
        Saves a procedural recipe (SOP/Playbook) extracted from user instructions or video demonstration.
        """
        playbook = {
            "name": name,
            "description": description,
            "platform": platform,
            "steps": steps,
            "updated_at": int(time.time())
        }
        self.data.setdefault("playbooks", {})[name] = playbook
        self._save()
        return playbook

    def get_playbook(self, name: str) -> Optional[Dict[str, Any]]:
        return self.data.get("playbooks", {}).get(name) or self.data.get("procedures", {}).get(name)

    def list_playbooks(self) -> List[Dict[str, Any]]:
        pbs = self.data.get("playbooks", {})
        return [{"name": k, "description": v.get("description", ""), "platform": v.get("platform", "general")} for k, v in pbs.items()]

    def generate_brain_prompt(self) -> str:
        """
        Generates a concise Markdown block summarizing all learned golden rules and known playbooks,
        ready to be appended to any AI session so it operates with past wisdom.
        """
        lessons = self.data.get("lessons", [])
        playbooks = self.data.get("playbooks", {})
        
        if not lessons and not playbooks:
            return ""

        lines = ["\n[CEREBRO DEL AGENTE - REGLAS APRENDIDAS Y PLAYBOOKS DISPONIBLES]"]
        
        if lessons:
            lines.append("Lecciones aprendidas para no repetir errores:")
            for l in lessons[-15:]:
                lines.append(f"- [{l.get('category', 'general')}] REGLA: {l.get('golden_rule')} (Causa: {l.get('situation')})")
        
        if playbooks:
            lines.append("\nPlaybooks y Procedimientos aprendidos:")
            for name, pb in playbooks.items():
                lines.append(f"• Playbook '{name}' ({pb.get('platform', 'general')}): {pb.get('description', '')}")

        lines.append("[FIN DEL CEREBRO]\n")
        return "\n".join(lines)


# Singleton instance
brain = AgentBrain()
