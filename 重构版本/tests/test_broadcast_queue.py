from robot_modbus_lite.broadcast_queue import BroadcastMessage, BroadcastQueue


def test_publish_assigns_sequence_and_keeps_message_order():
    queue = BroadcastQueue()

    first = queue.publish(kind="receipt", text="收到")
    second = queue.publish(kind="progress", text="预检中")

    assert first.seq == 1
    assert second.seq == 2
    assert [message.text for message in queue.messages_since(0)] == ["收到", "预检中"]


def test_queue_trims_old_messages_when_limit_is_reached():
    queue = BroadcastQueue(max_messages=2)

    queue.publish(kind="receipt", text="1")
    queue.publish(kind="progress", text="2")
    queue.publish(kind="result", text="3")

    assert [message.text for message in queue.messages_since(0)] == ["2", "3"]


def test_publish_accepts_existing_message_and_assigns_sequence():
    queue = BroadcastQueue()
    message = BroadcastMessage(seq=0, kind="alert", text="报警", priority="high")

    published = queue.publish_message(message)

    assert published.seq == 1
    assert published.kind == "alert"
    assert published.priority == "high"


def test_publish_once_suppresses_duplicate_within_window():
    now = [100.0]
    queue = BroadcastQueue(clock=lambda: now[0])

    first = queue.publish_once(kind="alert", text="报警", dedupe_key="alarm:1", dedupe_window_seconds=5.0)
    duplicate = queue.publish_once(kind="alert", text="报警", dedupe_key="alarm:1", dedupe_window_seconds=5.0)
    now[0] = 106.0
    later = queue.publish_once(kind="alert", text="报警", dedupe_key="alarm:1", dedupe_window_seconds=5.0)

    assert first is not None
    assert duplicate is None
    assert later is not None
    assert [message.seq for message in queue.messages_since(0)] == [1, 2]


def test_messages_since_for_delivery_prioritizes_alerts_without_losing_sequence_cursor():
    queue = BroadcastQueue()
    queue.publish(kind="progress", text="预检中", priority="normal")
    queue.publish(kind="alert", text="报警", priority="high")
    queue.publish(kind="result", text="完成", priority="normal")

    pending = queue.messages_since_for_delivery(0)

    assert [message.text for message in pending] == ["报警", "预检中", "完成"]
    assert {message.seq for message in pending} == {1, 2, 3}
