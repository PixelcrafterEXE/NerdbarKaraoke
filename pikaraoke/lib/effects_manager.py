"""Effects configuration and OSC control for mixers."""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

from pythonosc.udp_client import SimpleUDPClient

from pikaraoke.lib.get_platform import get_data_directory
from pikaraoke.lib.microphone_manager import MicrophoneManager


class EffectsManager:
    """Manages effect configurations, state, and OSC messages to the mixer."""

    def __init__(self, karaoke, config_dir: str | None = None, state_path: str | None = None) -> None:
        self.karaoke = karaoke
        self.config_dir = config_dir or os.path.join(get_data_directory(), "effects")
        self.state_path = state_path or os.path.join(get_data_directory(), "effects_mic_state.json")
        self.effects: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] = {"microphones": {}}

        self.load_effects_config()
        self.load_state()

    def load_effects_config(self) -> None:
        """Load effect configuration files from disk."""
        self.effects = {}
        self._ensure_config_dir()
        if not os.path.isdir(self.config_dir):
            logging.warning("Effects config directory not found: %s", self.config_dir)
            return

        for filename in sorted(os.listdir(self.config_dir)):
            if not filename.endswith(".json"):
                continue
            file_path = os.path.join(self.config_dir, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as config_file:
                    data = json.load(config_file)
            except (OSError, json.JSONDecodeError) as exc:
                logging.warning("Failed to load effect config %s: %s", file_path, exc)
                continue

            effect_id = str(data.get("id") or os.path.splitext(filename)[0])
            effect_name = str(data.get("name") or effect_id)
            try:
                effect_type = int(data.get("type", 0))
            except (TypeError, ValueError):
                effect_type = 0

            visible = bool(data.get("visible", True))
            user_editable = data.get("user_editable", {})
            if not isinstance(user_editable, dict):
                user_editable = {}

            parameters: list[dict[str, Any]] = []
            for param in data.get("parameters", []):
                try:
                    index = int(param.get("index"))
                except (TypeError, ValueError):
                    continue
                if index < 1 or index > 64:
                    continue
                parameters.append(
                    {
                        "index": index,
                        "name": str(param.get("name") or f"Param {index}"),
                        "default": float(param.get("default", 0.0)),
                        "min": float(param.get("min", 0.0)),
                        "max": float(param.get("max", 1.0)),
                        "step": float(param.get("step", 0.01)),
                    }
                )

            parameters.sort(key=lambda item: item["index"])
            self.effects[effect_id] = {
                "id": effect_id,
                "name": effect_name,
                "type": effect_type,
                "parameters": parameters,
                "visible": visible,
                "user_editable": {
                    str(key): bool(value) for key, value in user_editable.items()
                },
                "file_path": file_path,
            }

    def load_state(self) -> None:
        """Load persisted effect state from disk if available, otherwise initialize."""
        self.state = {"microphones": {}}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "microphones" in data and isinstance(data["microphones"], dict):
                        self.state = data
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Failed to load effects state %s: %s", self.state_path, exc)
        # Ensure state matches current configs/microphones
        self._sync_state()

    def save_state(self) -> None:
        """Persist in-memory state to disk (best-effort)."""
        try:
            tmp_path = self.state_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp_path, self.state_path)
        except OSError as exc:
            logging.warning("Failed to persist effects state %s: %s", self.state_path, exc)

    def _sync_state(self) -> None:
        """Ensure state matches current microphone list and effect configs."""
        # Default: no effect selected for microphones (user must choose)
        default_effect_id = None

        for mic_color in MicrophoneManager.MICROPHONE_COLORS:
            mic_state = self.state["microphones"].get(mic_color, {}) or {}
            effect_id = mic_state.get("effect_id")
            if effect_id not in self.effects:
                effect_id = None

            mic_state.setdefault("enabled", False)
            mic_state["effect_id"] = effect_id
            mic_state.setdefault("parameters", {})
            self._normalize_parameters(mic_state, effect_id)
            self.state["microphones"][mic_color] = mic_state

    def _normalize_parameters(self, mic_state: dict[str, Any], effect_id: str | None) -> None:
        defaults = self._default_parameters(effect_id)
        parameters = mic_state.get("parameters", {}) or {}

        for key, default_value in defaults.items():
            parameters.setdefault(key, default_value)

        for key in list(parameters.keys()):
            if key not in defaults:
                parameters.pop(key, None)

        mic_state["parameters"] = parameters

    def _default_parameters(self, effect_id: str | None) -> dict[str, float]:
        if not effect_id or effect_id not in self.effects:
            return {}
        return {
            str(param["index"]): float(param.get("default", 0.0))
            for param in self.effects[effect_id]["parameters"]
        }

    def _effect_state_defaults(self, effect: dict[str, Any]) -> dict[str, Any]:
        """Return a default 'state' structure for an effect (used by APIs)."""
        return {
            "visible": effect.get("visible", True),
            "parameters": {
                str(param["index"]): float(param.get("default", 0.0))
                for param in effect.get("parameters", [])
            },
            "user_editable": copy.deepcopy(effect.get("user_editable", {})),
        }

    def get_effects_config(self) -> list[dict[str, Any]]:
        """Return effect configs for API use."""
        return [copy.deepcopy(effect) for effect in self.effects.values()]

    def get_effects_for_admin(self) -> list[dict[str, Any]]:
        """Return effect configs merged with admin state."""
        payload = []
        for effect_id, effect in self.effects.items():
            payload.append({**copy.deepcopy(effect), "state": self._effect_state_defaults(effect)})
        return payload

    def get_effects_for_user(self, mic_color: str) -> dict[str, Any]:
        """Return allowed effects and current microphone selection."""
        mic_state = self.state["microphones"].get(mic_color, {})
        allowed_effects: list[dict[str, Any]] = []
        for effect_id, effect in self.effects.items():
            if not effect.get("visible", True):
                continue
            allowed_effects.append({**copy.deepcopy(effect), "state": self._effect_state_defaults(effect)})
        return {
            "microphone": mic_color,
            "effect_id": mic_state.get("effect_id"),
            "parameters": copy.deepcopy(mic_state.get("parameters", {})),
            "effects": allowed_effects,
        }

    def get_state(self) -> dict[str, Any]:
        """Return a deep copy of all microphone effect state."""
        return copy.deepcopy(self.state)

    def get_user_state(self, mic_color: str) -> dict[str, Any]:
        """Return state for a single microphone."""
        return self.get_effects_for_user(mic_color)

    def set_microphone_effect(self, mic_color: str, effect_id: str) -> tuple[bool, str]:
        logging.debug("set_microphone_effect: mic=%s effect=%s", mic_color, effect_id)
        if effect_id not in self.effects:
            logging.debug("Attempt to set invalid effect %s for mic %s", effect_id, mic_color)
            return False, "Invalid effect"
        if not self.effects[effect_id].get("visible", True):
            logging.debug("Attempt to set unavailable effect %s for mic %s", effect_id, mic_color)
            return False, "Effect not available"
        mic_state = self.state["microphones"].setdefault(mic_color, {})
        mic_state["effect_id"] = effect_id
        mic_state["parameters"] = mic_state.get("parameters", {}) or self._default_parameters(effect_id)
        # Enabling microphone when a user selects an effect so OSC messages are applied
        mic_state["enabled"] = True
        self.state["microphones"][mic_color] = mic_state
        self.save_state()
        return True, "Effect updated"

    def set_microphone_enabled(self, mic_color: str, enabled: bool) -> None:
        mic_state = self.state["microphones"].setdefault(mic_color, {})
        mic_state["enabled"] = bool(enabled)
        self.state["microphones"][mic_color] = mic_state
        self.save_state()

    def update_effect_defaults(self, effect_id: str, parameters: dict[str, Any]) -> None:
        effect = self.effects.get(effect_id)
        if not effect:
            return
        param_defs = {str(param["index"]): param for param in effect["parameters"]}
        for key, value in parameters.items():
            try:
                key_str = str(int(key))
            except (TypeError, ValueError):
                continue
            if key_str not in param_defs:
                continue
            try:
                float_value = float(value)
            except (TypeError, ValueError):
                continue

            param_def = param_defs[key_str]
            min_val = param_def.get("min", 0.0)
            max_val = param_def.get("max", 1.0)
            if float_value < min_val:
                float_value = min_val
            if float_value > max_val:
                float_value = max_val
            for param in effect["parameters"]:
                if str(param["index"]) == key_str:
                    param["default"] = float_value
                    break

        self._write_effect_config(effect)

    def update_user_parameters(
        self,
        mic_color: str,
        parameters: dict[str, Any],
    ) -> None:
        mic_state = self.state["microphones"].setdefault(mic_color, {})
        effect_id = mic_state.get("effect_id")
        effect = self.effects.get(effect_id)
        if not effect:
            logging.debug("No active effect for mic %s; skipping parameter update", mic_color)
            return
        user_editable = effect.get("user_editable", {})
        param_defs = {str(param["index"]): param for param in effect["parameters"]}

        for key, value in parameters.items():
            try:
                key_str = str(int(key))
            except (TypeError, ValueError):
                continue
            if key_str not in param_defs:
                continue
            if not user_editable.get(key_str, False):
                continue
            try:
                float_value = float(value)
            except (TypeError, ValueError):
                continue

            param_def = param_defs[key_str]
            min_val = param_def.get("min", 0.0)
            max_val = param_def.get("max", 1.0)
            if float_value < min_val:
                float_value = min_val
            if float_value > max_val:
                float_value = max_val

            mic_state.setdefault("parameters", {})[key_str] = float_value

        # Enabling microphone when a user changes parameters so changes are applied
        mic_state["enabled"] = True

        self.state["microphones"][mic_color] = mic_state
        self.save_state()

    def update_user_editable(self, effect_id: str, user_editable: dict[str, Any]) -> None:
        effect = self.effects.get(effect_id)
        if not effect:
            return

        current = effect.get("user_editable", {})
        param_defs = {str(param["index"]): param for param in effect["parameters"]}

        for key, value in user_editable.items():
            try:
                key_str = str(int(key))
            except (TypeError, ValueError):
                continue
            if key_str not in param_defs:
                continue
            current[key_str] = bool(value)
        effect["user_editable"] = current
        self._write_effect_config(effect)

    def update_effect_visibility(self, effect_id: str, visible: bool) -> None:
        effect = self.effects.get(effect_id)
        if not effect:
            return
        effect["visible"] = bool(visible)
        self._write_effect_config(effect)

    def disable_microphone_input(self, mic_color: str) -> None:
        """Disable microphone input via OSC by setting FX source to OFF (0)."""
        if not getattr(self.karaoke, "effects_enabled", True):
            return

        mixer_ip = getattr(self.karaoke, "mixer_ip", "")
        mixer_port = getattr(self.karaoke, "mixer_port", None)
        if not mixer_ip or not mixer_port:
            logging.debug("Mixer IP/port not configured, skipping OSC send")
            return

        rack = self._get_mic_fx_rack(mic_color)

        try:
            client = SimpleUDPClient(mixer_ip, int(mixer_port))
            # Set FX source to OFF (0) for both left and right channels
            logging.debug("Sending OSC %s -> %s", f"/fx/{rack}/source/l", 0)
            print(f"OSC SEND: /fx/{rack}/source/l 0", flush=True)
            client.send_message(f"/fx/{rack}/source/l", 0)
            logging.debug("Sending OSC %s -> %s", f"/fx/{rack}/source/r", 0)
            print(f"OSC SEND: /fx/{rack}/source/r 0", flush=True)
            client.send_message(f"/fx/{rack}/source/r", 0)
            logging.info("Disabled microphone input for %s (FX rack %d)", mic_color, rack)
        except Exception:
            logging.exception("Failed to send OSC to mixer")

        # Clear the effect_id from state
        mic_state = self.state["microphones"].setdefault(mic_color, {})
        mic_state["effect_id"] = None
        self.save_state()

    def apply_effect_to_mixer(self, mic_color: str) -> None:
        """Send OSC commands for the given microphone if enabled."""
        if not getattr(self.karaoke, "effects_enabled", True):
            return
        mic_state = self.state["microphones"].get(mic_color)
        if not mic_state or not mic_state.get("enabled"):
            return

        effect_id = mic_state.get("effect_id")
        effect = self.effects.get(effect_id)
        if not effect:
            logging.debug("Effect %s not found for mic %s; skipping OSC send", effect_id, mic_color)
            return
        if not effect.get("visible", True):
            logging.debug("Effect %s not visible for mic %s; skipping OSC send", effect_id, mic_color)
            return

        mixer_ip = getattr(self.karaoke, "mixer_ip", "")
        mixer_port = getattr(self.karaoke, "mixer_port", None)
        if not mixer_ip or not mixer_port:
            logging.debug("Mixer IP/port not configured, skipping OSC send")
            return

        rack = self._get_mic_fx_rack(mic_color)
        source = self._get_mic_source(mic_color)

        try:
            client = SimpleUDPClient(mixer_ip, int(mixer_port))
            logging.debug("Sending OSC %s -> %s", f"/fx/{rack}/type", int(effect["type"]))
            print(f"OSC SEND: /fx/{rack}/type {int(effect['type'])}", flush=True)
            client.send_message(f"/fx/{rack}/type", int(effect["type"]))
            logging.debug("Sending OSC %s -> %s", f"/fx/{rack}/source/l", int(source))
            print(f"OSC SEND: /fx/{rack}/source/l {int(source)}", flush=True)
            client.send_message(f"/fx/{rack}/source/l", int(source))
            logging.debug("Sending OSC %s -> %s", f"/fx/{rack}/source/r", int(source))
            print(f"OSC SEND: /fx/{rack}/source/r {int(source)}", flush=True)
            client.send_message(f"/fx/{rack}/source/r", int(source))

            param_defs = {str(param["index"]): param for param in effect["parameters"]}
            for key, param in param_defs.items():
                default_value = param.get("default", 0.0)
                value = mic_state.get("parameters", {}).get(key, default_value)
                logging.debug("Sending OSC %s -> %s", f"/fx/{rack}/par/{int(key):02d}", float(value))
                print(f"OSC SEND: /fx/{rack}/par/{int(key):02d} {float(value)}", flush=True)
                client.send_message(f"/fx/{rack}/par/{int(key):02d}", float(value))
        except Exception:
            logging.exception("Failed to send OSC to mixer")

    def _get_mic_fx_rack(self, mic_color: str) -> int:
        color_key = mic_color.lower()
        rack = getattr(self.karaoke, f"mic_fx_rack_{color_key}", 1)
        try:
            rack_int = int(rack)
        except (TypeError, ValueError):
            rack_int = 1
        if rack_int < 1 or rack_int > 4:
            rack_int = 1
        return rack_int

    def _get_mic_source(self, mic_color: str) -> int:
        color_key = mic_color.lower()
        channel = getattr(self.karaoke, f"mic_channel_{color_key}", 0)
        try:
            channel_int = int(channel)
        except (TypeError, ValueError):
            channel_int = 0
        if channel_int < 0:
            channel_int = 0
        if channel_int > 17:
            channel_int = 17
        return channel_int

    def _ensure_config_dir(self) -> None:
        """Ensure effects config directory exists."""
        os.makedirs(self.config_dir, exist_ok=True)

    def _write_effect_config(self, effect: dict[str, Any]) -> None:
        """Persist effect configuration back to its JSON file."""
        file_path = effect.get("file_path")
        if not file_path:
            return

        output = {
            "id": effect.get("id"),
            "name": effect.get("name"),
            "type": effect.get("type"),
            "visible": effect.get("visible", True),
            "user_editable": effect.get("user_editable", {}),
            "parameters": [
                {
                    "index": param.get("index"),
                    "name": param.get("name"),
                    "default": param.get("default", 0.0),
                    "min": param.get("min", 0.0),
                    "max": param.get("max", 1.0),
                    "step": param.get("step", 0.01),
                }
                for param in effect.get("parameters", [])
            ],
        }

        try:
            with open(file_path, "w", encoding="utf-8") as config_file:
                json.dump(output, config_file, indent=2)
        except OSError as exc:
            logging.warning("Failed to write effect config %s: %s", file_path, exc)
