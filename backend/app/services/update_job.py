"""更新任务：把 7 阶段编排放到后台线程跑，进度写进 jobs 表。

**为什么进度要落库**：进度面板可以关掉再打开，也可以刷新页面。
只存在内存字典里的进度，页面一刷新就丢了，用户会以为更新卡死。
jobs 表是现成的，一行一个任务，还能事后查这次更新到底哪一步出问题。

**为什么不用 BackgroundTasks**：它不能取消，也没法查中间进度。

**为什么单任务**：两个更新同时跑会同时读写 score_snapshots 和 pool_states，
池子周数会被算两遍。所以全局只允许一个，第二次点「更新」直接返回已经在跑。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Callable

from app.db import session
from app.services import updater
from app.services.sources import LiveSources, make_client

STAGE_ORDER = updater.STAGE_KEYS


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def percent_of(stage: str | None, current: int, total: int) -> int:
    """整体进度：阶段序号 + 阶段内进度，7 个阶段均分。"""
    if stage not in STAGE_ORDER:
        return 0
    idx = STAGE_ORDER.index(stage)            # 0-based
    inner = (current / total) if total else 0
    return int(round((idx + inner) / len(STAGE_ORDER) * 100))


class UpdateJobManager:
    """全局唯一的更新任务。"""

    def __init__(self, sources_factory: Callable[[Any], Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._job_id: int | None = None
        self._result: updater.UpdateResult | None = None
        self._last: dict | None = None
        self._sources_factory = sources_factory or self._default_sources

    # ---------------- 默认数据源 ----------------

    @staticmethod
    def _default_sources(conn) -> LiveSources:
        return LiveSources(make_client(conn))

    # ---------------- 对外 ----------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, kind: str = "manual", as_of_date: str | None = None,
              codes: list[str] | None = None) -> dict:
        with self._lock:
            if self.running:
                return {"ok": False, "reason": "already_running",
                        "message": "已经有一轮更新在跑", "status": self.status()}

            conn = session.get_conn()
            as_of = as_of_date or datetime.now().strftime("%Y-%m-%d")
            with session.transaction(conn):
                cur = conn.execute(
                    """INSERT INTO jobs(kind, status, stage, progress, total, message, started_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (kind, "running", STAGE_ORDER[0], 0, 0, "准备中", _now_iso()),
                )
                job_id = int(cur.lastrowid)

            self._job_id = job_id
            self._cancel = threading.Event()
            self._result = None
            self._last = None

            self._thread = threading.Thread(
                target=self._run, name=f"update-job-{job_id}", daemon=True,
                args=(job_id, kind, as_of, codes),
            )
            self._thread.start()
            return {"ok": True, "jobId": job_id, "status": self.status()}

    def status(self) -> dict:
        """优先读库（跨线程、跨页面刷新都准），库里没有就用内存里最后一次。"""
        conn = session.get_conn()
        row = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return {"status": "idle", "jobId": None, "percent": 0,
                    "stage": None, "stageLabel": "", "stageIndex": 0,
                    "stageCount": len(STAGE_ORDER), "current": 0, "total": 0,
                    "message": "", "cancelRequested": self._cancel.is_set(),
                    "result": None}

        job = dict(row)
        stage = job.get("stage")
        current = job.get("progress") or 0
        total = job.get("total") or 0
        result = None
        if job.get("result"):
            try:
                result = json.loads(job["result"])
            except (ValueError, TypeError):
                result = None
        return {
            "jobId": job["id"],
            "status": job["status"],
            "kind": job["kind"],
            "stage": stage,
            "stageLabel": updater.STAGE_LABELS.get(stage or "", stage or ""),
            "stageIndex": (STAGE_ORDER.index(stage) + 1) if stage in STAGE_ORDER else 0,
            "stageCount": len(STAGE_ORDER),
            "current": current,
            "total": total,
            "percent": 100 if job["status"] in ("ok", "partial", "failed", "cancelled")
            else percent_of(stage, current, total),
            "message": job.get("message") or "",
            "startedAt": job.get("started_at"),
            "finishedAt": job.get("finished_at"),
            "cancelRequested": self._cancel.is_set(),
            "result": result,
        }

    def cancel(self) -> dict:
        if not self.running:
            return {"ok": False, "reason": "not_running",
                    "message": "当前没有在跑的更新", "status": self.status()}
        self._cancel.set()
        self._write(self._job_id, message="收到取消请求，等当前阶段收尾",
                    status=None)
        return {"ok": True, "status": self.status()}

    def wait(self, timeout: float | None = None) -> bool:
        """等任务结束。测试和「更新完再导出」这类场景用。"""
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    # ---------------- 线程体 ----------------

    def _run(self, job_id: int, kind: str, as_of: str, codes: list[str] | None) -> None:
        # 每个线程一个连接：session 是 thread-local，这里拿到的是本线程专属的
        conn = session.get_conn()
        status = "failed"
        try:
            sources = self._sources_factory(conn)
            result = updater.run_update(
                conn, sources, as_of_date=as_of, kind=kind, codes=codes,
                progress=lambda p: self._on_progress(job_id, p),
                cancel=self._cancel,
            )
            self._result = result
            status = result.status
            self._write(
                job_id,
                status=status,
                message=result.summary(),
                finished_at=result.finished_at or _now_iso(),
                result=json.dumps(result.as_dict(with_works=False), ensure_ascii=False),
                progress=result.ok_count,
                total=result.total,
            )
        except Exception as exc:                      # 兜底：线程不能静默死掉
            status = "failed"
            self._write(job_id, status=status, finished_at=_now_iso(),
                        message=f"{type(exc).__name__}: {exc}")
        finally:
            # 取消或失败时，业务数据一句都没写，但 source_calls（只追加的调用日志）
            # 还挂在隐式事务里。这里提交一次，让数据源健康页能查到失败的调用。
            try:
                if conn.in_transaction:
                    conn.commit()
            except Exception:
                pass

    def _on_progress(self, job_id: int, p: updater.Progress) -> None:
        self._write(
            job_id, stage=p.stage, progress=p.current, total=p.total,
            message=p.message or p.label,
        )

    def _write(self, job_id: int | None, *, stage: str | None = None,
               progress: int | None = None, total: int | None = None,
               status: str | None = None, message: str | None = None,
               finished_at: str | None = None, result: str | None = None) -> None:
        if job_id is None:
            return
        sets: list[str] = []
        args: list[Any] = []
        for col, val in (("stage", stage), ("progress", progress), ("total", total),
                         ("status", status), ("message", message),
                         ("finished_at", finished_at), ("result", result)):
            if val is not None:
                sets.append(f"{col}=?")
                args.append(val)
        if not sets:
            return
        args.append(job_id)
        conn = session.get_conn()
        try:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", args)
            # 必须提交：进度是给另一个连接（HTTP 线程）读的，
            # 不提交就只能自己看见，前端会一直停在 0%。
            conn.commit()
        except Exception:
            pass


def mark_interrupted(conn=None) -> int:
    """启动时调用：上次进程没跑完的任务标成中断。

    线程随进程一起死，库里留着的 running 一定是假象。不清掉的话
    进度面板一打开就会永远停在某个阶段，用户以为还在跑。
    """
    c = conn or session.get_conn()
    try:
        cur = c.execute(
            """UPDATE jobs SET status='failed', finished_at=?,
                   message='上次更新被中断（应用退出）'
                WHERE status='running'""",
            (_now_iso(),),
        )
        c.commit()
        return int(cur.rowcount or 0)
    except Exception:
        return 0


# 全局单例：整个进程只允许一轮更新
manager = UpdateJobManager()
