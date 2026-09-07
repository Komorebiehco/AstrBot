"""会话解析与日志格式化模块。"""

from __future__ import annotations

from typing import Any


class SessionMixin:
    """会话解析与日志格式化混入类。"""

    context: Any

    def _parse_session_id(self, session_id: str) -> tuple[str, str, str] | None:
        """
        解析会话 UMO，返回 (platform, message_type, target_id)。

        该方法仅用于解析与展示，不对 UMO 做任何修正或重写。
        """
        # 仅接受字符串类型的 UMO
        if not isinstance(session_id, str):
            return None

        # 优先匹配标准消息类型锚点
        known_types = [
            "FriendMessage",
            "GroupMessage",
            "PrivateMessage",
            "GuildMessage",
        ]

        # 先走锚点匹配，避免 platform/target 中包含冒号导致误切分
        for msg_type in known_types:
            search_pattern = f":{msg_type}:"
            idx = session_id.find(search_pattern)
            if idx != -1:
                platform = session_id[:idx]
                after_type = session_id[idx + len(search_pattern) :]
                return platform, msg_type, after_type

        # 兼容普通三段式 UMO
        parts = session_id.split(":")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]

        # 兼容多段 platform 或 target_id 的情况
        if len(parts) > 3:
            return ":".join(parts[:-2]), parts[-2], parts[-1]

        return None

    def _get_session_name(
        self, session_id: str, session_config: dict | None = None
    ) -> str:
        """获取会话备注名（用于日志与前端展示）。"""
        normalized_session_id = self._normalize_session_id(session_id)

        def _pick_name(payload: dict | None) -> str:
            if not isinstance(payload, dict):
                return ""
            for key in ("session_name", "_session_name", "alias"):
                raw = payload.get(key)
                if raw is None:
                    continue
                text = str(raw).strip()
                if text:
                    return text
            return ""

        # 1) 优先用已传入的会话配置（减少重复查询）
        name = _pick_name(session_config)
        if name:
            return name

        # 2) 再尝试读取当前生效配置
        try:
            resolved_config = self._get_session_config(normalized_session_id)
            name = _pick_name(resolved_config)
            if name:
                return name
        except Exception:
            pass

        # 3) 读取会话覆写记录（兼容仅保存在 override 中的备注名）
        manager = getattr(self, "session_override_manager", None)
        if manager:
            try:
                override = manager.get_override(normalized_session_id)
                name = _pick_name(override)
                if name:
                    return name
            except Exception:
                pass

        # 4) 兼容历史运行态数据中的备注名
        data = getattr(self, "session_data", {})
        if isinstance(data, dict):
            name = _pick_name(data.get(normalized_session_id))
            if name:
                return name
            name = _pick_name(data.get(session_id))
            if name:
                return name

        return ""

    def _get_session_display_name(
        self, session_id: str, session_config: dict | None = None
    ) -> str:
        """获取会话展示名：备注名优先，缺失时回退 UMO。"""
        name = self._get_session_name(session_id, session_config)
        return name if name else session_id

    def _get_session_log_str(
        self, session_id: str, session_config: dict | None = None
    ) -> str:
        """
        获取统一格式的会话日志字符串。

        格式：私聊/群聊 ID (备注名)
        """
        parsed = self._parse_session_id(session_id)
        session_name = self._get_session_name(session_id, session_config)

        if not parsed:
            return f"{session_id} ({session_name})" if session_name else session_id

        # 仅用于日志展示，不参与业务逻辑
        _, msg_type, target_id = parsed
        type_str = "未知类型"
        if "Friend" in msg_type or "Private" in msg_type:
            type_str = "私聊"
        elif "Group" in msg_type or "Guild" in msg_type:
            type_str = "群聊"

        log_str = f"{type_str} {target_id}"
        if session_name:
            log_str += f" ({session_name})"
        return log_str

    def _resolve_full_umo(
        self, target_id: str, msg_type: str, preferred_platform: str | None = None
    ) -> str:
        """Resolve legacy identifiers without rewriting an explicit platform.

        Args:
            target_id: Recipient identifier.
            msg_type: AstrBot message type.
            preferred_platform: Persisted or explicitly configured platform ID.

        Returns:
            A full UMO, retaining ``default`` if the legacy route is ambiguous.
        """
        # Plugins initialize before platforms; availability is not identity.
        if preferred_platform and preferred_platform != "default":
            return f"{preferred_platform}:{msg_type}:{target_id}"

        known_ids = set(getattr(self, "session_data", {}))
        config = getattr(self, "config", {})
        for scope in ("friend_settings", "group_settings"):
            known_ids.update(config.get(scope, {}).get("session_list", []))
        candidates = set()
        for existing_id in known_ids:
            parsed = self._parse_session_id(existing_id)
            if (
                parsed
                and parsed[0] not in ("", "default")
                and parsed[1:]
                == (
                    msg_type,
                    target_id,
                )
            ):
                candidates.add(parsed[0])
        if len(candidates) == 1:
            return f"{next(iter(candidates))}:{msg_type}:{target_id}"
        # Never guess between platforms with a coincidentally identical user ID.
        return f"default:{msg_type}:{target_id}"

    def _normalize_session_id(self, session_id: str) -> str:
        """Normalize legacy UMOs while preserving explicit platform identity."""
        parsed = self._parse_session_id(session_id)
        if not parsed:
            return session_id

        platform, msg_type, target_id = parsed
        return self._resolve_full_umo(target_id, msg_type, platform)
