"""RabbitMQ case queue — the "Case Queue" box in the architecture diagram.

UiPath (or any producer) submits a case by POSTing the AnalyzeReq payload to
/api/enqueue, which publishes it here. worker.py consumes the queue and runs
the analysis pipeline. Topology, declared idempotently on every connection:

  case-queue          durable work queue; messages are persistent, so nothing
                      is lost across a broker restart
  case-queue-failed   dead-letter target: a case that failed twice (or was
                      rejected) lands here for inspection instead of retrying
                      forever and jamming the pipeline

Configuration (env):
  RABBITMQ_URL   amqp(s) connection string. Local docker default:
                 amqp://guest:guest@localhost:5672/%2F
                 CloudAMQP gives you an amqps://... URL on its dashboard.
  CASE_QUEUE     queue name, default "case-queue".
"""
import json
import os

import pika

QUEUE = os.environ.get("CASE_QUEUE", "case-queue")
FAILED_QUEUE = QUEUE + "-failed"
RESULTS_QUEUE = QUEUE + "-results"
DLX = QUEUE + "-dlx"
URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")


def connect() -> tuple[pika.BlockingConnection, "pika.adapters.blocking_connection.BlockingChannel"]:
    """Open a connection + channel with the topology declared."""
    params = pika.URLParameters(URL)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    declare(ch)
    return conn, ch


def declare(ch) -> None:
    """Idempotent — safe to call on every connect, from API and worker alike."""
    ch.exchange_declare(exchange=DLX, exchange_type="fanout", durable=True)
    ch.queue_declare(queue=FAILED_QUEUE, durable=True)
    ch.queue_bind(queue=FAILED_QUEUE, exchange=DLX)
    ch.queue_declare(queue=QUEUE, durable=True,
                     arguments={"x-dead-letter-exchange": DLX})
    # Results travel back to the API process here so /api/job/{id} can answer
    # the UI's polling. TTL: a result nobody collects within an hour (API
    # restarted, job forgotten) is dropped rather than piling up forever.
    ch.queue_declare(queue=RESULTS_QUEUE, durable=True,
                     arguments={"x-message-ttl": 3_600_000})


def publish(case: dict) -> None:
    """Enqueue one case. A fresh connection per publish is deliberate: the API
    enqueues at human rates, and a shared long-lived connection would need
    heartbeat upkeep between requests for no measurable gain."""
    conn, ch = connect()
    try:
        ch.basic_publish(
            exchange="",                      # default exchange → routing key is the queue name
            routing_key=QUEUE,
            body=json.dumps(case).encode(),
            properties=pika.BasicProperties(
                delivery_mode=2,              # persistent — survives broker restart
                content_type="application/json",
            ),
        )
    finally:
        conn.close()


def publish_result(payload: dict) -> None:
    """Worker → API: the finished outcome for a job_id-tagged message."""
    conn, ch = connect()
    try:
        ch.basic_publish(
            exchange="", routing_key=RESULTS_QUEUE,
            body=json.dumps(payload).encode(),
            properties=pika.BasicProperties(delivery_mode=2,
                                            content_type="application/json"),
        )
    finally:
        conn.close()


def depth() -> dict:
    """Message counts for /api/queue/status — passive declare, never creates."""
    conn = pika.BlockingConnection(pika.URLParameters(URL))
    try:
        ch = conn.channel()
        q = ch.queue_declare(queue=QUEUE, durable=True,
                             arguments={"x-dead-letter-exchange": DLX})
        f = ch.queue_declare(queue=FAILED_QUEUE, durable=True)
        return {"queue": QUEUE, "ready": q.method.message_count,
                "failed": f.method.message_count,
                "consumers": q.method.consumer_count}
    finally:
        conn.close()
