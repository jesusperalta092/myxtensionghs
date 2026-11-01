# SPDX-License-Identifier: GPL-3.0-or-later
"""
evaluation_engine.py - SQLite-backed Assessment & Group Deduplication Engine for GHS
Handles student rosters, automatic group clustering by shared YouTube URLs/mentions,
batch grading with feedback replication, and hot-swap session resumption.
"""

import os
import re
import sqlite3
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, parse_qs

DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "campus_evaluaciones")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "evaluaciones.db")


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts standard 11-char video ID from various YouTube URL formats."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    # youtu.be/VIDEO_ID
    if "youtu.be/" in url:
        part = url.split("youtu.be/")[1]
        return part.split("?")[0].split("&")[0][:11]
    # youtube.com/watch?v=VIDEO_ID
    if "youtube.com" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0][:11]
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/")[1][:11]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[1][:11]
    # Generic regex fallback for 11-character ID
    match = re.search(r"([a-zA-Z0-9_-]{11})", url)
    return match.group(1) if match else None


class EvaluationEngine:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self):
        folder = os.path.dirname(self.db_path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    created_at INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_name TEXT NOT NULL,
                    group_code TEXT,
                    submission_url TEXT,
                    url_hash TEXT,
                    transcript TEXT,
                    status TEXT DEFAULT 'pending',
                    grade REAL,
                    feedback TEXT,
                    key_moments TEXT,
                    evaluated_at INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER,
                    course_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    student_code TEXT,
                    submission_url TEXT,
                    raw_text TEXT,
                    status TEXT DEFAULT 'pending',
                    final_grade REAL,
                    FOREIGN KEY(group_id) REFERENCES evaluation_groups(id)
                )
            """)
            conn.commit()

    def load_students_batch(self, course_name: str, students: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Loads students and clusters them automatically into groups based on shared submission URLs
        or explicit partner mentions.
        """
        course_name = (course_name or "Curso General").strip()
        now = int(time.time())

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO courses (name, created_at) VALUES (?, ?)", (course_name, now))

            # Grouping map: video_id_or_hash -> group_id
            url_to_group: Dict[str, int] = {}
            # Existing groups in this course
            cursor.execute("SELECT id, url_hash FROM evaluation_groups WHERE course_name = ?", (course_name,))
            for row in cursor.fetchall():
                if row["url_hash"]:
                    url_to_group[row["url_hash"]] = row["id"]

            new_students_count = 0
            new_groups_count = 0

            for s in students:
                name = str(s.get("name", "Estudiante")).strip()
                code = str(s.get("student_code", "")).strip()
                url = str(s.get("submission_url", "")).strip()
                raw_text = str(s.get("raw_text", "")).strip()

                yt_id = extract_youtube_id(url)
                group_key = yt_id if yt_id else (url if url else f"single_{name}_{code}")

                # Check if group already exists
                if group_key in url_to_group:
                    gid = url_to_group[group_key]
                else:
                    cursor.execute("""
                        INSERT INTO evaluation_groups (course_name, submission_url, url_hash, status)
                        VALUES (?, ?, ?, 'pending')
                    """, (course_name, url, group_key))
                    gid = cursor.lastrowid
                    url_to_group[group_key] = gid
                    new_groups_count += 1
                    # Assign group code like "G-01"
                    cursor.execute("UPDATE evaluation_groups SET group_code = ? WHERE id = ?", (f"G-{gid:02d}", gid))

                # Insert or update student
                cursor.execute("""
                    INSERT INTO students (group_id, course_name, name, student_code, submission_url, raw_text, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """, (gid, course_name, name, code, url, raw_text))
                new_students_count += 1

            conn.commit()

        return {
            "course": course_name,
            "students_loaded": new_students_count,
            "total_groups_formed": len(url_to_group),
            "new_groups_created": new_groups_count,
            "message": f"Se cargaron {new_students_count} alumnos agrupados en {len(url_to_group)} grupos únicos."
        }

    def get_evaluation_status(self, course_name: Optional[str] = None) -> Dict[str, Any]:
        """Returns comprehensive progress statistics."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            where = "WHERE course_name = ?" if course_name else ""
            params = (course_name,) if course_name else ()

            cursor.execute(f"SELECT COUNT(*) as total, SUM(CASE WHEN status='evaluated' THEN 1 ELSE 0 END) as done FROM students {where}", params)
            s_row = cursor.fetchone()
            total_students = s_row["total"] or 0
            done_students = s_row["done"] or 0

            cursor.execute(f"SELECT COUNT(*) as total, SUM(CASE WHEN status='evaluated' THEN 1 ELSE 0 END) as done FROM evaluation_groups {where}", params)
            g_row = cursor.fetchone()
            total_groups = g_row["total"] or 0
            done_groups = g_row["done"] or 0

            pct = round((done_students / total_students * 100), 1) if total_students > 0 else 0.0

            return {
                "course": course_name or "Todos",
                "total_students": total_students,
                "evaluated_students": done_students,
                "pending_students": total_students - done_students,
                "total_groups": total_groups,
                "evaluated_groups": done_groups,
                "pending_groups": total_groups - done_groups,
                "progress_percent": pct
            }

    def get_next_group_to_evaluate(self, course_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Finds the next pending group and all its student members."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM evaluation_groups WHERE status = 'pending'"
            params = []
            if course_name:
                query += " AND course_name = ?"
                params.append(course_name)
            query += " ORDER BY id ASC LIMIT 1"

            cursor.execute(query, tuple(params))
            group = cursor.fetchone()
            if not group:
                return None

            gid = group["id"]
            cursor.execute("SELECT id, name, student_code, raw_text FROM students WHERE group_id = ?", (gid,))
            students = [dict(r) for r in cursor.fetchall()]

            res = dict(group)
            res["members"] = students
            return res

    def save_group_evaluation(self, group_id: int, grade: float, feedback: str, key_moments: str = "") -> Dict[str, Any]:
        """
        Saves the grade and feedback for a group, replicating it instantly
        to all student members in SQLite.
        """
        now = int(time.time())
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE evaluation_groups
                SET grade = ?, feedback = ?, key_moments = ?, status = 'evaluated', evaluated_at = ?
                WHERE id = ?
            """, (grade, feedback, key_moments, now, group_id))

            cursor.execute("""
                UPDATE students
                SET final_grade = ?, status = 'evaluated'
                WHERE group_id = ?
            """, (grade, group_id))

            cursor.execute("SELECT name FROM students WHERE group_id = ?", (group_id,))
            names = [r["name"] for r in cursor.fetchall()]
            conn.commit()

        return {
            "ok": True,
            "group_id": group_id,
            "grade": grade,
            "feedback": feedback,
            "replicated_students": names,
            "message": f"Calificación {grade} y feedback guardados para el grupo {group_id} ({len(names)} alumnos)."
        }

    def get_resume_prompt(self, course_name: Optional[str] = None) -> str:
        """
        Generates a clean hot-swap resumption prompt for when the user changes AI accounts
        or opens a fresh chat session.
        """
        status = self.get_evaluation_status(course_name)
        next_g = self.get_next_group_to_evaluate(course_name)

        lines = [
            "================================================================",
            "⚡ REANUDACIÓN DE SESIÓN DE EVALUACIÓN (Estado Local Sincronizado)",
            "================================================================",
            f"Curso: {status['course']}",
            f"Progreso Total: {status['evaluated_students']}/{status['total_students']} alumnos ({status['progress_percent']}%)",
            f"Grupos Completados: {status['evaluated_groups']}/{status['total_groups']}",
        ]

        if next_g:
            members = ", ".join(m["name"] for m in next_g.get("members", []))
            g_code = next_g.get("group_code") or f"G-{next_g['id']}"
            lines.extend([
                f"\n📌 SIGUIENTE GRUPO A EVALUAR: {g_code}",
                f"Integrantes ({len(next_g.get('members', []))}): {members}",
                f"Enlace de Entrega: {next_g.get('submission_url', 'No proporcionado')}",
                "\nInstrucción: Analiza el enlace o transcripción, califica según la rúbrica y llama a 'save_group_evaluation' para registrar la nota."
            ])
        else:
            lines.append("\n🎉 ¡Todos los alumnos y grupos de este curso ya han sido evaluados!")

        lines.append("================================================================")
        return "\n".join(lines)

    def export_report_csv(self, course_name: Optional[str] = None, filename: str = "reporte_calificaciones.csv") -> str:
        """Exports all evaluations to a clean CSV file."""
        target_path = os.path.join(DEFAULT_DB_DIR, filename)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            query = """
                SELECT s.name, s.student_code, g.group_code, s.final_grade, g.feedback, g.submission_url, g.key_moments
                FROM students s
                JOIN evaluation_groups g ON s.group_id = g.id
            """
            params = []
            if course_name:
                query += " WHERE s.course_name = ?"
                params.append(course_name)
            query += " ORDER BY g.id ASC, s.name ASC"

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        with open(target_path, "w", encoding="utf-8-sig") as f:
            f.write("Alumno,Codigo,Grupo,Nota,Feedback,Enlace_Entrega,Marcas_Tiempo\n")
            for r in rows:
                name = f'"{r["name"]}"'
                code = f'"{r["student_code"] or ""}"'
                group = f'"{r["group_code"] or ""}"'
                grade = str(r["final_grade"] if r["final_grade"] is not None else "")
                fb = f'"{str(r["feedback"] or "").replace(chr(34), chr(34)+chr(34))}"'
                url = f'"{r["submission_url"] or ""}"'
                km = f'"{r["key_moments"] or ""}"'
                f.write(f"{name},{code},{group},{grade},{fb},{url},{km}\n")

        return target_path


# Singleton instance
eval_engine = EvaluationEngine()
