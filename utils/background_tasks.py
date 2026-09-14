import threading
import traceback
from typing import Optional, Dict, Any, List, Callable


class ScanTaskManager:
    """
    Singleton manager for running file and media scans in background worker threads.
    Tracks live progress, current step, results, and supports cooperative cancellation.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._status: str = "idle"  # idle | running | done | error | cancelled
        self._task_type: Optional[str] = None  # "media" | "files"
        self._progress: float = 0.0
        self._current_step: str = ""
        self._result: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None

    def is_running(self) -> bool:
        with self._lock:
            return self._status == "running"

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "task_type": self._task_type,
                "progress": self._progress,
                "current_step": self._current_step,
                "result": self._result,
                "error": self._error,
            }

    def cancel(self):
        with self._lock:
            if self._status == "running":
                self._cancel_event.set()
                self._current_step = "Cancelling scan..."

    def clear(self):
        with self._lock:
            if self._status != "running":
                self._status = "idle"
                self._task_type = None
                self._progress = 0.0
                self._current_step = ""
                self._result = None
                self._error = None
                self._cancel_event.clear()

    def update_progress(self, current: int, total: int, step_desc: str):
        with self._lock:
            if total > 0:
                self._progress = min(1.0, max(0.0, current / total))
            else:
                self._progress = 0.0
            self._current_step = step_desc

    def start_scan(
        self,
        task_type: str,
        scan_paths: List[str],
        prune: bool = True,
    ) -> bool:
        """
        Starts a scan in a background thread if not already running.
        task_type: 'media' (image scan + SigLIP embeddings) or 'files' (general files)
        """
        with self._lock:
            if self._status == "running":
                return False

            self._cancel_event.clear()
            self._status = "running"
            self._task_type = task_type
            self._progress = 0.0
            self._current_step = "Initializing scan..."
            self._result = None
            self._error = None

        def _worker():
            try:
                progress_cb = lambda cur, tot, desc: self.update_progress(cur, tot, desc)

                if task_type == "media":
                    from utils.image_search_utils import scan_images
                    res = scan_images(
                        scan_paths,
                        prune=prune,
                        progress_callback=progress_cb,
                        cancel_event=self._cancel_event,
                    )
                else:
                    from utils.fileops_utils import scan_files
                    res = scan_files(
                        scan_paths,
                        prune=prune,
                        progress_callback=progress_cb,
                        cancel_event=self._cancel_event,
                    )

                with self._lock:
                    if self._cancel_event.is_set():
                        self._status = "cancelled"
                        self._current_step = "Scan cancelled by user."
                        self._result = res
                    else:
                        self._status = "done"
                        self._progress = 1.0
                        self._current_step = "Scan completed successfully."
                        self._result = res

            except Exception as e:
                with self._lock:
                    self._status = "error"
                    self._error = str(e)
                    self._current_step = f"Scan failed: {e}"

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        return True


# Global singleton instance
scan_manager = ScanTaskManager()
