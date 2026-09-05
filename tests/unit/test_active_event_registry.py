import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from astrbot.core.message.components import File, Image, Node, Nodes, Plain, Reply
from astrbot.core.pipeline.process_stage.follow_up import (
    register_active_runner,
    try_capture_follow_up,
    unregister_active_runner,
)
from astrbot.core.utils.active_event_registry import (
    ActiveEventRegistry,
    active_event_registry,
)


class StubEvent:
    """Minimal event implementation used by ActiveEventRegistry tests."""

    def __init__(
        self,
        umo: str,
        *,
        sender_id: str = "alice",
        components: list | None = None,
        message_str: str = "hello",
        message_id: str = "message-1",
    ) -> None:
        self.unified_msg_origin = umo
        self.extras: dict[str, object] = {}
        self.sender_id = sender_id
        self.message_str = message_str
        self.message_obj = SimpleNamespace(
            message=components if components is not None else [Plain(message_str)],
            message_id=message_id,
        )

    def set_extra(self, key: str, value: object) -> None:
        """Store an event extra.

        Args:
            key: Extra field name.
            value: Extra field value.
        """
        self.extras[key] = value

    def get_extra(self, key: str, default=None):
        """Read an event extra.

        Args:
            key: Extra field name.
            default: Value returned when the field is absent.

        Returns:
            Stored field value or the supplied default.
        """
        return self.extras.get(key, default)

    def get_sender_id(self) -> str:
        """Return the event sender identifier.

        Returns:
            Sender identifier used for follow-up ownership checks.
        """
        return self.sender_id

    def get_message_str(self) -> str:
        """Return the plain message text.

        Returns:
            Plain message text.
        """
        return self.message_str

    def get_message_outline(self) -> str:
        """Return a compact message outline.

        Returns:
            Plain text or a media placeholder.
        """
        return self.message_str or "[media]"


def test_request_agent_stop_invokes_registered_callback() -> None:
    """Agent stop requests immediately invoke the active execution callback."""
    registry = ActiveEventRegistry()
    event = StubEvent("webchat:FriendMessage:webchat!alice!session")
    callback = Mock()
    registry.register(event)
    registry.register_agent_stop_callback(event, callback)

    stopped_count = registry.request_agent_stop_all(event.unified_msg_origin)

    assert stopped_count == 1
    assert event.extras["agent_stop_requested"] is True
    callback.assert_called_once_with()


def test_unregister_removes_agent_stop_callback() -> None:
    """Unregistered events cannot retain stale Agent cancellation callbacks."""
    registry = ActiveEventRegistry()
    event = StubEvent("webchat:FriendMessage:webchat!alice!session")
    callback = Mock()
    registry.register(event)
    registry.register_agent_stop_callback(event, callback)

    registry.unregister(event)
    stopped_count = registry.request_agent_stop_all(event.unified_msg_origin)

    assert stopped_count == 0
    callback.assert_not_called()


def test_active_runner_wires_immediate_stop_callback() -> None:
    """Active Runner registration connects registry stop to Runner cancellation."""
    event = StubEvent("webchat:FriendMessage:webchat!alice!runner-session")
    runner = SimpleNamespace(
        run_context=SimpleNamespace(context=SimpleNamespace(event=event)),
        request_stop=Mock(),
    )
    active_event_registry.register(event)
    register_active_runner(event.unified_msg_origin, runner)

    try:
        stopped_count = active_event_registry.request_agent_stop_all(
            event.unified_msg_origin
        )

        assert stopped_count == 1
        runner.request_stop.assert_called_once_with()
    finally:
        unregister_active_runner(event.unified_msg_origin, runner)
        active_event_registry.unregister(event)


@pytest.mark.asyncio
async def test_plain_text_follow_up_is_captured_by_active_runner() -> None:
    """Text-only follow-ups remain eligible for immediate Agent injection."""
    event = StubEvent(
        "webchat:FriendMessage:webchat!alice!follow-up",
        components=[Plain("more details")],
        message_str="more details",
        message_id="follow-up",
    )
    active_event = StubEvent(
        event.unified_msg_origin,
        message_id="active-run",
    )
    ticket = SimpleNamespace(seq=7, resolved=asyncio.Event())
    runner = SimpleNamespace(
        run_context=SimpleNamespace(context=SimpleNamespace(event=active_event)),
        request_stop=Mock(),
        follow_up=Mock(return_value=ticket),
    )
    register_active_runner(event.unified_msg_origin, runner)

    try:
        capture = try_capture_follow_up(event)

        assert capture is not None
        assert capture.target_run_id == "active-run"
        runner.follow_up.assert_called_once_with(message_text="more details")
        capture.monitor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await capture.monitor_task
    finally:
        unregister_active_runner(event.unified_msg_origin, runner)


@pytest.mark.parametrize(
    "components,message_str",
    [
        ([Image(file="file:///tmp/anime.jpg")], ""),
        ([Plain("what anime"), Image(file="file:///tmp/anime.jpg")], "what anime"),
        ([Image(file="file:///tmp/anime.jpg"), Plain("what anime")], "what anime"),
        ([File(name="archive.zip", file="file:///tmp/archive.zip")], ""),
        (
            [
                Reply(
                    id="quoted-image",
                    chain=[Image(file="file:///tmp/anime.jpg")],
                ),
                Plain("what anime"),
            ],
            "what anime",
        ),
    ],
)
def test_rich_follow_up_preserves_original_message_for_normal_processing(
    components: list,
    message_str: str,
) -> None:
    """Media and replies must not collapse into text-only Agent follow-ups."""
    event = StubEvent(
        "weixin:FriendMessage:alice",
        components=components,
        message_str=message_str,
        message_id="rich-follow-up",
    )
    active_event = StubEvent(event.unified_msg_origin, message_id="active-run")
    runner = SimpleNamespace(
        run_context=SimpleNamespace(context=SimpleNamespace(event=active_event)),
        request_stop=Mock(),
        follow_up=Mock(),
    )
    register_active_runner(event.unified_msg_origin, runner)

    try:
        assert try_capture_follow_up(event) is None
        runner.follow_up.assert_not_called()
        assert event.message_obj.message == components
    finally:
        unregister_active_runner(event.unified_msg_origin, runner)


def test_nested_rich_follow_up_preserves_original_message_for_normal_processing() -> (
    None
):
    """Media nested inside forwarded nodes must not be reduced to plain text."""
    image = Image(file="file:///tmp/nested-anime.jpg")
    components = [
        Nodes(
            [
                Node(content=[Plain("caption"), Nodes([Node(content=[image])])]),
            ]
        )
    ]
    event = StubEvent(
        "weixin:FriendMessage:nested-rich",
        components=components,
        message_str="caption",
        message_id="nested-rich-follow-up",
    )
    active_event = StubEvent(event.unified_msg_origin, message_id="active-run")
    runner = SimpleNamespace(
        run_context=SimpleNamespace(context=SimpleNamespace(event=active_event)),
        request_stop=Mock(),
        follow_up=Mock(),
    )
    register_active_runner(event.unified_msg_origin, runner)

    try:
        assert try_capture_follow_up(event) is None
        runner.follow_up.assert_not_called()
        assert event.message_obj.message == components
    finally:
        unregister_active_runner(event.unified_msg_origin, runner)
