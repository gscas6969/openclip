"""
batch_queue.py — Sequential background batch processing queue for OpenClip.

Allows users to submit multiple YouTube URLs at once with shared settings
(clip duration, layout preference, brand kit, custom AI prompt) and runs
each project cleanly one-by-one so local CPU/GPU/FFmpeg resources are never
overwhelmed.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import database  # type: ignore

logger = logging.getLogger(__name__)


class BatchQueueManager:
    def __init__(self) -> None:
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._queue: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._pipeline_runner: Optional[Callable[[str, str], Awaitable[None]]] = None
        self._cancelled_projects: set[str] = set()

    def configure(self, pipeline_runner: Callable[[str, str], Awaitable[None]]) -> None:
        self._pipeline_runner = pipeline_runner
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def enqueue_batch(
        self,
        urls: List[str],
        clip_min_duration: int = 30,
        clip_max_duration: int = 60,
        custom_prompt: Optional[str] = None,
        layout_preference: str = "auto",
        brand_kit_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        batch_id = f"batch_{uuid.uuid4().hex[:10]}"
        items: List[Dict[str, Any]] = []

        for raw_url in urls:
            url = raw_url.strip()
            if not url:
                continue
            created = await database.create_project(
                youtube_url=url,
                source_type="youtube",
                clip_min_duration=clip_min_duration,
                clip_max_duration=clip_max_duration,
                custom_prompt=custom_prompt,
                layout_preference=layout_preference,
                brand_kit_id=brand_kit_id,
            )
            pid = created["project_id"]
            item = {
                "project_id": pid,
                "youtube_url": url,
                "title": None,
                "status": "queued",
                "clip_count": 0,
                "error_message": None,
            }
            items.append(item)
            await self._queue.put((batch_id, pid, url))

        batch = {
            "id": batch_id,
            "created_at": time.time(),
            "clip_min_duration": clip_min_duration,
            "clip_max_duration": clip_max_duration,
            "layout_preference": layout_preference,
            "brand_kit_id": brand_kit_id,
            "custom_prompt": custom_prompt,
            "items": items,
        }
        self._batches[batch_id] = batch

        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

        return await self.get_batch(batch_id) or batch

    async def cancel_item(self, batch_id: str, project_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if not batch:
            return False
        for item in batch["items"]:
            if item["project_id"] == project_id and item["status"] == "queued":
                self._cancelled_projects.add(project_id)
                item["status"] = "cancelled"
                await database.update_project_status(project_id, "error", "Cancelled from batch queue")
                return True
        return False

    async def list_batches(self) -> List[Dict[str, Any]]:
        results = []
        for bid in sorted(self._batches.keys(), key=lambda k: self._batches[k]["created_at"], reverse=True):
            hydrated = await self.get_batch(bid)
            if hydrated:
                results.append(hydrated)
        return results

    async def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        batch = self._batches.get(batch_id)
        if not batch:
            return None

        hydrated_items = []
        done_count = 0
        error_count = 0
        active_count = 0
        queued_count = 0

        for item in batch["items"]:
            pid = item["project_id"]
            if item["status"] == "cancelled":
                hydrated_items.append(dict(item))
                error_count += 1
                continue

            proj = await database.get_project(pid)
            if proj:
                st = proj["status"]
                if st == "pending" and item["status"] == "queued":
                    st = "queued"
                hydrated = {
                    "project_id": pid,
                    "youtube_url": proj["youtube_url"],
                    "title": proj.get("title"),
                    "status": st,
                    "clip_count": len(proj.get("clips") or []),
                    "error_message": proj.get("error_message"),
                }
            else:
                hydrated = dict(item)

            if hydrated["status"] == "done":
                done_count += 1
            elif hydrated["status"] in ("error", "cancelled"):
                error_count += 1
            elif hydrated["status"] == "queued":
                queued_count += 1
            else:
                active_count += 1

            hydrated_items.append(hydrated)

        total = len(hydrated_items)
        overall = "done"
        if active_count > 0 or queued_count > 0:
            overall = "running"
        elif total > 0 and error_count == total:
            overall = "error"

        return {
            "id": batch["id"],
            "created_at": batch["created_at"],
            "status": overall,
            "total": total,
            "completed": done_count,
            "failed": error_count,
            "queued": queued_count,
            "active": active_count,
            "clip_min_duration": batch["clip_min_duration"],
            "clip_max_duration": batch["clip_max_duration"],
            "layout_preference": batch["layout_preference"],
            "custom_prompt": batch["custom_prompt"],
            "items": hydrated_items,
        }

    async def _worker_loop(self) -> None:
        logger.info("Batch queue worker loop started")
        while True:
            batch_id, project_id, url = await self._queue.get()
            try:
                if project_id in self._cancelled_projects:
                    self._cancelled_projects.discard(project_id)
                    continue

                batch = self._batches.get(batch_id)
                if batch:
                    for it in batch["items"]:
                        if it["project_id"] == project_id:
                            it["status"] = "processing"

                if self._pipeline_runner is not None:
                    logger.info("Batch %s starting project %s (%s)", batch_id, project_id, url)
                    await self._pipeline_runner(project_id, url)
            except Exception as exc:
                logger.exception("Batch worker error on project %s: %s", project_id, exc)
            finally:
                self._queue.task_done()


manager = BatchQueueManager()
