"""
Main scheduler loop.

Continuously runs scheduled drives, persists ticks and outputs,
and updates next-run timestamps based on cadence rules.
"""

import asyncio
import os
import time
from typing import Any

from . import drives
from . import cadence as cadence_module
from . import persistence


def resolve_cadences(ctx: Any) -> dict:
    """
    Resolve cadence overrides from env > config > registry defaults.
    
    Args:
        ctx: Context object (KernelDaemon instance)
    
    Returns:
        Dict mapping task_id to resolved cadence string
    """
    resolved = {}
    
    for task_id, config in drives.REGISTRY.items():
        # Start with registry default
        resolved_cadence = config["cadence"]
        
        # Check kernel.yaml config for overrides
        if hasattr(ctx, "cfg"):
            cfg_drives = ctx.cfg.get("drives", {})
            if task_id in cfg_drives:
                resolved_cadence = cfg_drives[task_id]
        
        # Check environment variable overrides
        env_key = f"DRIVE_{task_id.upper()}"
        env_value = os.getenv(env_key)
        if env_value:
            resolved_cadence = env_value
        
        resolved[task_id] = resolved_cadence
    
    return resolved


async def run_scheduler(ctx: Any) -> None:
    """
    Main scheduler loop.
    
    Runs continuously until cancelled, executing drives on their
    cadences and persisting all activity.
    
    Args:
        ctx: Context object (typically KernelDaemon instance)
            Must have: mem.db_path, cfg (optional), tz
    """
    db_path = ctx.mem.db_path
    
    # Ensure schema exists
    print("[Scheduler] Initializing schema...")
    persistence.ensure_schema(db_path)
    
    # Resolve cadences from config/env
    resolved_cadences = resolve_cadences(ctx)
    print(f"[Scheduler] Resolved cadences: {resolved_cadences}")
    
    # Log resolved cadences
    print("[Scheduler] Resolved cadences:")
    for task_id, cadence_str in resolved_cadences.items():
        print(f"  {task_id}: {cadence_str}")
    
    # Build tasks dict with resolved cadences
    tasks_config = {}
    for task_id in drives.REGISTRY.keys():
        tasks_config[task_id] = {
            "cadence": resolved_cadences[task_id]
        }
    
    # Upsert scheduled tasks
    persistence.upsert_scheduled_tasks(db_path, tasks_config)
    
    print("[Scheduler] Autonomy loop started")
    
    # Main loop
    while True:
        try:
            now_ts = int(time.time())
            
            # Get next due task
            due_task = persistence.next_due_task(db_path, now_ts)
            
            if not due_task:
                # No tasks due, sleep briefly
                await asyncio.sleep(5)
                continue
            
            task_id = due_task["id"]
            scheduled_ts = due_task["next_run_ts"]
            cadence_str = due_task["cadence"]
            
            # Build idempotency key using scheduled time
            idempotency_key = f"{task_id}:{scheduled_ts}"
            
            # Check if this tick already exists (restart protection)
            try:
                with persistence.wal_db(
                    db_path, timeout=5.0
                ) as conn:
                    conn.execute("PRAGMA busy_timeout = 3000")
                    cur = conn.execute(
                        "SELECT id FROM ticks WHERE idempotency_key = ?",
                        (idempotency_key,)
                    )
                    if cur.fetchone():
                        # Already ran, just update next_run and continue
                        next_ts, new_window_state = \
                            cadence_module.compute_next_run(
                                last_run_ts=scheduled_ts,
                                scheduled_ts=scheduled_ts,
                                cadence_str=cadence_str,
                                now_ts=now_ts,
                                window_state=due_task["window_state"]
                            )
                        persistence.update_next_run(
                            db_path,
                            task_id,
                            next_ts,
                            scheduled_ts,
                            new_window_state
                        )
                        continue
            except Exception:
                # If check fails, proceed anyway (idempotency in INSERT)
                pass
            
            # Record tick start
            started_ts = int(time.time())
            
            # Execute drive
            drive_fn = drives.REGISTRY[task_id]["fn"]
            success = 0
            result_meta = {}
            nudge = None
            
            try:
                nudge = await drive_fn(ctx)
                success = 1
            except Exception as e:
                print(f"[Scheduler] Error in {task_id}: {e}")
                result_meta["error"] = str(e)
            
            finished_ts = int(time.time())
            dur_ms = (finished_ts - started_ts) * 1000
            
            # Persist tick
            try:
                persistence.insert_tick(
                    db_path,
                    task_id,
                    started_ts,
                    finished_ts,
                    success,
                    idempotency_key,
                    result_meta
                )
            except Exception as e:
                # If insert fails due to duplicate key, that's OK
                if "unique" not in str(e).lower():
                    print(
                        f"[Scheduler] Error inserting tick for "
                        f"{task_id}: {e}"
                    )
            
            # Persist nudge if emitted
            if nudge:
                try:
                    persistence.insert_nudge(
                        db_path,
                        nudge.kind,
                        nudge.message,
                        nudge.actions,
                        nudge.reason,
                        nudge.created_ts
                    )
                except Exception as e:
                    print(
                        f"[Scheduler] Error inserting nudge "
                        f"from {task_id}: {e}"
                    )
            
            # Compute next run time
            next_ts, new_window_state = cadence_module.compute_next_run(
                last_run_ts=scheduled_ts,
                scheduled_ts=scheduled_ts,
                cadence_str=cadence_str,
                now_ts=now_ts,
                window_state=due_task["window_state"]
            )
            
            # Update scheduled task
            persistence.update_next_run(
                db_path,
                task_id,
                next_ts,
                scheduled_ts,
                new_window_state
            )
            
            # Log tick execution
            print(
                f"[Scheduler] tick={task_id} ok={success} "
                f"dur_ms={dur_ms} next={next_ts}"
            )
            
        except asyncio.CancelledError:
            print("[Scheduler] Shutdown requested")
            break
        except Exception as e:
            print(f"[Scheduler] Unexpected error in loop: {e}")
            await asyncio.sleep(5)
    
    print("[Scheduler] Autonomy loop stopped")
