"""Case-queue worker: consumes case-queue and runs the analysis pipeline.

Run:  python worker.py   (from the backend/ directory; needs RABBITMQ_URL)

Design notes, in the order they bite people:

- prefetch=1. A run takes minutes; each worker holds exactly one case so a
  restart re-queues at most one message and the broker load-balances cleanly
  across however many worker processes are running.

- The analysis runs in a thread while pika's I/O loop keeps servicing
  heartbeats. Blocking the consumer callback for minutes would get the
  connection declared dead by the broker mid-run, the message re-queued, and
  the case run twice. ack/nack are marshalled back onto the connection's
  thread via add_callback_threadsafe — pika channels are not thread-safe.

- ack ONLY after the outcome is recorded. Success and analysis failure both
  count as handled (a failed run is persisted as such, same policy as
  /api/export/save: losing it would make "failed" indistinguishable from
  "never ran"). Only infrastructure trouble — can't even record the outcome —
  rejects the message: first time back onto the queue for one retry, second
  time (redelivered) to case-queue-failed for inspection.

- Idempotency: redelivery is a fact of life with at-least-once queues, so a
  case that already has a non-error run saved is skipped, not re-run.
"""
import functools
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import pika

import case_queue
import supa
from app import AnalyzeReq, ExportSaveReq, _build_record, _run_one, _PROVIDERS, RUN_DEADLINE
from providers import mode

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("case-worker")
logging.getLogger("pika").setLevel(logging.WARNING)


def _already_done(case_id: str) -> bool:
    if not case_id or not supa.available():
        return False
    try:
        return any(r.get("case_id") == case_id and r.get("band") != "error"
                   for r in supa.list_runs())
    except Exception:
        return False    # can't tell — run it; persistence is idempotent enough


def _persist(req: AnalyzeReq, data: dict, error: str = "") -> None:
    """Save the outcome the same way the UI does via /api/export/save."""
    if not supa.available():
        log.warning("Supabase not configured — result for %s not persisted", req.case_id)
        return
    imgs = req.suspect_images or ([req.suspect_image] if req.suspect_image else [])
    save = ExportSaveReq(engine="GPT-5.5", product=req.product,
                         product_id=req.product_id or "",
                         suspect_images=[b for b in imgs if b],
                         upc_image=req.upc_image, data=data,
                         case_id=req.case_id, brand=req.brand, error=error)
    supa.save_run(_build_record(save))


def process(body: bytes) -> None:
    """Handle one message end-to-end. Raising = infrastructure failure →
    the caller nacks and the broker retries/dead-letters.

    Two message flavors share the queue:
    - with "job_id": submitted by /api/analyze for the UI. The result travels
      back on the results queue so /api/job/{id} can answer the poll; the UI
      then persists via /api/export/save exactly as it always has, so the
      worker must NOT save (it would double-record every run).
    - without "job_id": an external producer (UiPath). Nobody is polling, so
      the worker persists the outcome itself."""
    msg = json.loads(body)
    jid = msg.get("job_id") or ""
    req = AnalyzeReq(**{k: v for k, v in msg.items()
                        if k in AnalyzeReq.model_fields})
    if not jid and _already_done(req.case_id):
        log.info("case %s already has a saved run — skipping (idempotency)", req.case_id)
        return
    provider = req.provider if req.provider in _PROVIDERS else "openai"
    log.info("case %s: starting analysis (provider=%s, job=%s)",
             req.case_id, provider, jid or "external")
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run_one, req, provider)
            try:
                result = fut.result(timeout=RUN_DEADLINE)
            except FuturesTimeout:
                raise RuntimeError(
                    f"{provider} exceeded the {int(RUN_DEADLINE)}s time budget")
    except Exception as e:
        log.error("case %s: analysis failed after %.0fs: %s",
                  req.case_id, time.time() - t0, e)
        if jid:
            case_queue.publish_result({"job_id": jid, "ok": False, "error": str(e)})
        else:
            _persist(req, {}, error=str(e))   # recorded as a failed run
        return
    payload = {"mode": mode(), **result}
    if jid:
        case_queue.publish_result({"job_id": jid, "ok": True, "result": payload})
    else:
        _persist(req, payload)
    log.info("case %s: done in %.0fs — verdict=%s", req.case_id,
             time.time() - t0,
             (result.get("composite") or {}).get("verdict_label", "?"))


def _on_message(ch, method, _props, body):
    conn = ch.connection

    def work():
        try:
            process(body)
            done = functools.partial(ch.basic_ack, method.delivery_tag)
        except Exception:
            log.exception("message rejected (delivery_tag=%s, redelivered=%s)",
                          method.delivery_tag, method.redelivered)
            # first failure → back on the queue for one retry;
            # a redelivered message that fails again → dead-letter queue
            done = functools.partial(ch.basic_nack, method.delivery_tag,
                                     requeue=not method.redelivered)
        conn.add_callback_threadsafe(done)

    threading.Thread(target=work, daemon=True).start()


def main():
    log.info("case-queue worker starting — queue=%s url=%s mode=%s",
             case_queue.QUEUE,
             case_queue.URL.split("@")[-1],     # never log credentials
             mode().get("openai"))
    while True:
        try:
            conn, ch = case_queue.connect()
            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(queue=case_queue.QUEUE,
                             on_message_callback=_on_message)
            log.info("consuming %s", case_queue.QUEUE)
            ch.start_consuming()
        except KeyboardInterrupt:
            log.info("stopping")
            break
        except pika.exceptions.AMQPConnectionError as e:
            log.warning("broker connection lost (%s) — reconnecting in 5s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
