# SPDX-License-Identifier: GPL-3.0-or-later
"""
task_pipeline.py - Universal Task Queue, Group Deduplication & State Resumption Engine
General-purpose batch processor for autonomous agents: handles items of ANY type
(assignments, e-commerce products, web scraping targets, documents, audits, or customer requests).
"""

import os
import re
import json
import sqlite3
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, parse_qs

DEFAULT_WORKSPACE_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "agent_workspace")
DEFAULT_DB_PATH = os.path.join(DEFAULT_WORKSPACE_DIR, "task_pipeline.db")


def extract_url_identifier(url: str) -> Optional[str]:
    """Extracts a normalized canonical identifier from URLs (YouTube video ID, product ID, or clean path)."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()

    # YouTube normalizer (for video tasks)
    if "youtu.be/" in url:
        part = url.split("youtu.be/")[1]
        return part.split("?")[0].split("&")[0][:11]
    if "youtube.com" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0][:11]
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/")[1][:11]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[1][:11]

    # Clean URL path for general web items (e.g. products, portals)
    try:
        parsed = urlparse(url)
        clean = (parsed.netloc + parsed.path).rstrip("/")
        return clean if clean else url
    except Exception:
        return url


class TaskPipelineEngine:
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
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    batch_type TEXT DEFAULT 'general',
                    created_at INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_name TEXT NOT NULL,
                    group_code TEXT,
                    target_url TEXT,
                    url_hash TEXT,
                    cached_data TEXT,
                    status TEXT DEFAULT 'pending',
                    result TEXT,
                    notes TEXT,
                    key_evidence TEXT,
                    completed_at INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER,
                    batch_name TEXT NOT NULL,
                    item_label TEXT NOT NULL,
                    item_identifier TEXT,
                    target_url TEXT,
                    raw_payload TEXT,
                    status TEXT DEFAULT 'pending',
                    final_result TEXT,
                    FOREIGN KEY(group_id) REFERENCES task_groups(id)
                )
            """)
            conn.commit()

    def queue_load_batch(
        self,
        batch_name: str,
        items: List[Dict[str, Any]],
        batch_type: str = "general",
        deduplicate_by: str = "url"
    ) -> Dict[str, Any]:
        """
        Universal batch loader. Automatically clusters items sharing the same target (URL or identifier)
        so the AI executes the action ONCE and results are replicated across all associated items.
        """
        batch_name = (batch_name or "General_Batch").strip()
        now = int(time.time())

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO batches (name, batch_type, created_at) VALUES (?, ?, ?)",
                (batch_name, batch_type, now)
            )

            # Map existing group keys
            target_to_group: Dict[str, int] = {}
            cursor.execute("SELECT id, url_hash FROM task_groups WHERE batch_name = ?", (batch_name,))
            for row in cursor.fetchall():
                if row["url_hash"]:
                    target_to_group[row["url_hash"]] = row["id"]

            new_items_count = 0
            new_groups_count = 0

            for it in items:
                label = str(it.get("label") or it.get("name") or "Item").strip()
                ident = str(it.get("identifier") or it.get("id") or it.get("student_code") or "").strip()
                target_url = str(it.get("target_url") or it.get("url") or it.get("submission_url") or "").strip()
                raw_payload = it.get("raw_payload") or it.get("raw_text") or it.get("metadata") or ""
                if isinstance(raw_payload, (dict, list)):
                    raw_payload = json.dumps(raw_payload, ensure_ascii=False)
                else:
                    raw_payload = str(raw_payload)

                # Cluster key determination
                if deduplicate_by == "url" and target_url:
                    group_key = extract_url_identifier(target_url) or target_url
                else:
                    group_key = f"single_{label}_{ident}_{time.time()}"

                if group_key in target_to_group:
                    gid = target_to_group[group_key]
                else:
                    cursor.execute("""
                        INSERT INTO task_groups (batch_name, target_url, url_hash, status)
                        VALUES (?, ?, ?, 'pending')
                    """, (batch_name, target_url, group_key))
                    gid = cursor.lastrowid
                    target_to_group[group_key] = gid
                    new_groups_count += 1
                    cursor.execute(
                        "UPDATE task_groups SET group_code = ? WHERE id = ?",
                        (f"TG-{gid:03d}", gid)
                    )

                cursor.execute("""
                    INSERT INTO task_items (group_id, batch_name, item_label, item_identifier, target_url, raw_payload, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """, (gid, batch_name, label, ident, target_url, raw_payload))
                new_items_count += 1

            conn.commit()

        return {
            "batch_name": batch_name,
            "batch_type": batch_type,
            "items_loaded": new_items_count,
            "total_groups_formed": len(target_to_group),
            "new_groups_created": new_groups_count,
            "message": f"Cargados {new_items_count} elementos agrupados en {len(target_to_group)} unidades de trabajo únicas."
        }

    def queue_get_status(self, batch_name: Optional[str] = None) -> Dict[str, Any]:
        """Returns live statistics for a specific batch or across the entire queue."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            where = "WHERE batch_name = ?" if batch_name else ""
            params = (batch_name,) if batch_name else ()

            cursor.execute(f"SELECT COUNT(*) as total, SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as done FROM task_items {where}", params)
            i_row = cursor.fetchone()
            total_items = i_row["total"] or 0
            done_items = i_row["done"] or 0

            cursor.execute(f"SELECT COUNT(*) as total, SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as done FROM task_groups {where}", params)
            g_row = cursor.fetchone()
            total_groups = g_row["total"] or 0
            done_groups = g_row["done"] or 0

            pct = round((done_items / total_items * 100), 1) if total_items > 0 else 0.0

            return {
                "batch_name": batch_name or "Todos los lotes",
                "total_items": total_items,
                "completed_items": done_items,
                "pending_items": total_items - done_items,
                "total_groups": total_groups,
                "completed_groups": done_groups,
                "pending_groups": total_groups - done_groups,
                "progress_percent": pct
            }

    def queue_get_next_task(self, batch_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves the next pending task group and all its attached items."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM task_groups WHERE status = 'pending'"
            params = []
            if batch_name:
                query += " AND batch_name = ?"
                params.append(batch_name)
            query += " ORDER BY id ASC LIMIT 1"

            cursor.execute(query, tuple(params))
            group = cursor.fetchone()
            if not group:
                return None

            gid = group["id"]
            cursor.execute("SELECT id, item_label, item_identifier, target_url, raw_payload FROM task_items WHERE group_id = ?", (gid,))
            items = [dict(r) for r in cursor.fetchall()]

            res = dict(group)
            res["associated_items"] = items
            return res

    def queue_save_result(
        self,
        group_id: int,
        result_data: Any,
        notes: str = "",
        key_evidence: str = ""
    ) -> Dict[str, Any]:
        """
        Saves the outcome for a task group, automatically replicating the result
        to all items clustered in that group.
        """
        now = int(time.time())
        res_str = json.dumps(result_data, ensure_ascii=False) if isinstance(result_data, (dict, list)) else str(result_data)

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE task_groups
                SET result = ?, notes = ?, key_evidence = ?, status = 'completed', completed_at = ?
                WHERE id = ?
            """, (res_str, notes, key_evidence, now, group_id))

            cursor.execute("""
                UPDATE task_items
                SET final_result = ?, status = 'completed'
                WHERE group_id = ?
            """, (res_str, group_id))

            cursor.execute("SELECT item_label FROM task_items WHERE group_id = ?", (group_id,))
            labels = [r["item_label"] for r in cursor.fetchall()]
            conn.commit()

        return {
            "ok": True,
            "group_id": group_id,
            "result": result_data,
            "replicated_items_count": len(labels),
            "replicated_labels": labels,
            "message": f"Resultado guardado para la unidad {group_id} y replicado a {len(labels)} elementos asociados."
        }

    def queue_get_resume_prompt(self, batch_name: Optional[str] = None) -> str:
        """
        Universal Hot-Swap Resumption Prompt. Injects live state to any fresh AI account
        so it seamlessly continues work without re-doing past items.
        """
        status = self.queue_get_status(batch_name)
        next_t = self.queue_get_next_task(batch_name)

        g_code = (next_t.get("group_code") or f"TG-{next_t['id']}") if next_t else ""
        items_str = ", ".join(it["item_label"] for it in next_t.get("associated_items", [])) if next_t else ""

        lines = [
            "================================================================",
            "⚡ REANUDACIÓN DE SESIÓN UNIVERSAL (Estado Local Sincronizado)",
            "================================================================",
            f"Lote activo: {status['batch_name']}",
            f"Progreso Total: {status['completed_items']}/{status['total_items']} elementos ({status['progress_percent']}%)",
            f"Grupos de Tareas: {status['completed_groups']}/{status['total_groups']} completados",
        ]

        if next_t:
            lines.extend([
                f"\n📌 SIGUIENTE TAREA A PROCESAR: {g_code}",
                f"Elementos asociados ({len(next_t.get('associated_items', []))}): {items_str}",
                f"Destino / URL / Recurso: {next_t.get('target_url') or 'N/A'}",
                "\nInstrucción: Ejecuta la acción requerida para este objetivo y llama a 'queue_save_result' para persistir el resultado."
            ])
        else:
            lines.append("\n🎉 ¡Todos los elementos de este lote han sido completados exitosamente!")

        lines.append("================================================================")
        return "\n".join(lines)

    def queue_export_csv(self, batch_name: Optional[str] = None, filename: str = "batch_results.csv") -> str:
        """Exports all task items and results to a universal CSV."""
        target_path = os.path.join(DEFAULT_WORKSPACE_DIR, filename)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            query = """
                SELECT i.item_label, i.item_identifier, g.group_code, i.final_result, g.notes, g.target_url, g.key_evidence
                FROM task_items i
                JOIN task_groups g ON i.group_id = g.id
            """
            params = []
            if batch_name:
                query += " WHERE i.batch_name = ?"
                params.append(batch_name)
            query += " ORDER BY g.id ASC, i.item_label ASC"

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        with open(target_path, "w", encoding="utf-8-sig") as f:
            f.write("Elemento,Identificador,Grupo_Codigo,Resultado,Notas,URL_Destino,Evidencia\n")
            for r in rows:
                lbl = f'"{r["item_label"]}"'
                ident = f'"{r["item_identifier"] or ""}"'
                grp = f'"{r["group_code"] or ""}"'
                res = f'"{str(r["final_result"] or "").replace(chr(34), chr(34)+chr(34))}"'
                nts = f'"{str(r["notes"] or "").replace(chr(34), chr(34)+chr(34))}"'
                url = f'"{r["target_url"] or ""}"'
                evi = f'"{r["key_evidence"] or ""}"'
                f.write(f"{lbl},{ident},{grp},{res},{nts},{url},{evi}\n")

        return target_path


# Singleton instance
task_pipeline = TaskPipelineEngine()
