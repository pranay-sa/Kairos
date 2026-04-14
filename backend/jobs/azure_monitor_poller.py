import asyncio
from datetime import datetime, timedelta, timezone

from services.azure_monitor_service import azure_monitor_service
from services.qdrant_service import qdrant_service


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def run_azure_monitor_poll_loop(stop_event: asyncio.Event, interval_minutes: int) -> None:
    # Persist a checkpoint so restarts don’t re-ingest the same time window.
    last = azure_monitor_service.load_checkpoint()
    if last is None:
        last = _utc_now() - timedelta(minutes=interval_minutes)

    while not stop_event.is_set():
        until = _utc_now()
        try:
            rows = await azure_monitor_service.query_logs(since=last, until=until)
            if rows:
                texts = [r.text for r in rows]
                payloads = [r.payload for r in rows]
                ids = [r.stable_id for r in rows]
                await qdrant_service.upsert_documents(texts, payloads, ids=ids)

                # Advance checkpoint to max(TimeGenerated) we saw, but never beyond `until`.
                max_t = last
                for r in rows:
                    try:
                        ts = r.time_generated
                        if ts.endswith("Z"):
                            ts = ts[:-1] + "+00:00"
                        dt = datetime.fromisoformat(ts)
                    except Exception:
                        continue
                    if dt > max_t:
                        max_t = dt
                if max_t > last:
                    last = max_t
                    azure_monitor_service.save_checkpoint(last)
            else:
                # No rows; still advance window to avoid repeatedly querying the same span.
                last = until
                azure_monitor_service.save_checkpoint(last)
        except Exception as exc:
            # Don’t kill the API if Azure temporarily errors; retry next interval.
            print(f"[kairos] Azure Monitor poll warning: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(5, interval_minutes * 60))
        except asyncio.TimeoutError:
            pass

