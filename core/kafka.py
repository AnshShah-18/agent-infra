from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from loguru import logger
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential


def _get_producer() -> Producer:
    return Producer({"bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")})


def _get_consumer(group_id: str, topics: list[str]) -> Consumer:
    c = Consumer(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "group.id": group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "session.timeout.ms": "6000",
            "max.poll.interval.ms": "300000",
            "metadata.max.age.ms": "1000",
            "socket.timeout.ms": "10000",
        }
    )
    c.subscribe([t.value if hasattr(t, "value") else str(t) for t in topics])
    # Wait until Kafka propagates partition metadata and assigns partitions.
    for _ in range(20):
        c.poll(timeout=0.5)
        if c.assignment():
            break
        time.sleep(0.5)
    else:
        logger.warning(f"Consumer assignment still empty after retries for topics={topics}")
    return c


def ensure_topics(topics: list[str]) -> None:
    admin = AdminClient({"bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")})
    new_topics = [NewTopic(t, num_partitions=3, replication_factor=1) for t in topics]
    fs = admin.create_topics(new_topics)
    for topic, f in fs.items():
        try:
            f.result()
            logger.info(f"Topic created: {topic}")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.debug(f"Topic already exists: {topic}")
            else:
                logger.warning(f"Failed to create topic {topic}: {e}")


class KafkaPublisher:
    """Thin wrapper around confluent_kafka Producer."""

    def __init__(self) -> None:
        self._producer = _get_producer()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
    def publish(self, topic: str, payload: BaseModel | dict[str, Any], key: str | None = None) -> None:
        value = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
        self._producer.produce(
            topic,
            key=key.encode() if key else None,
            value=value.encode(),
            callback=self._delivery_report,
        )
        self._producer.poll(0)

    def flush(self) -> None:
        self._producer.flush()

    @staticmethod
    def _delivery_report(err: Any, msg: Any) -> None:
        if err:
            logger.error(f"Delivery failed for {msg.topic()}: {err}")
        else:
            logger.debug(f"Delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")


class KafkaConsumerLoop:
    """Blocking consumer loop. Call run() in a thread or process."""

    def __init__(self, group_id: str, topics: list[str], handler: Callable[[dict], None]) -> None:
        self._consumer = _get_consumer(group_id, topics)
        self._handler = handler
        self._running = False

    def run(self) -> None:
        self._running = True
        logger.info(f"Consumer started, topics={self._consumer.assignment()}")
        try:
            while self._running:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.error(f"Consumer error: {msg.error()}")
                    continue
                try:
                    payload = json.loads(msg.value().decode())
                    self._handler(payload)
                except Exception as e:
                    logger.exception(f"Handler error: {e}")
        finally:
            self._consumer.close()

    def stop(self) -> None:
        self._running = False
