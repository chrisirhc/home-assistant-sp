"""Runtime regressions: run with unittest inside HA, or pytest with HA installed.

The lightweight client test environment has no Home Assistant. These checks
must also run in a Home Assistant 2026.11+ environment: unlike inspecting a
voluptuous schema, its real form serializer rejects opaque validator functions.
No SP credentials or network requests are used.
"""

from __future__ import annotations

import importlib
import importlib.util
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, PropertyMock, patch


@unittest.skipUnless(importlib.util.find_spec("homeassistant"), "requires HA runtime")
class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("custom_components.sp_group.config_flow")
        self.probatio = importlib.import_module("probatio")
        self.cv = importlib.import_module("homeassistant.helpers.config_validation")
        self.entry = SimpleNamespace(
            data={"username": "user@example.com"},
            options={"electricity_price": 0.29, "unrelated": "preserved"},
        )
        self.flow = self.module.SpGroupConfigFlow()
        # Keep these checks independent of config-entry persistence and HTTP.
        self.flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
        self.flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
        self.flow.async_set_unique_id = AsyncMock()
        self.flow._abort_if_unique_id_configured = Mock()
        self.flow._get_reauth_entry = Mock(return_value=self.entry)
        self.flow._get_reconfigure_entry = Mock(return_value=self.entry)
        self.flow.hass = Mock()

    def assert_serializable(self, result: dict[str, Any]) -> None:
        fields = self.probatio.to_field_list(
            result["data_schema"], custom_serializer=self.cv.custom_serializer
        )
        self.assertTrue(fields)

    async def test_all_credential_forms_serialize(self) -> None:
        for step in ("user", "reauth_confirm", "reconfigure"):
            with self.subTest(step=step):
                self.assert_serializable(
                    await getattr(self.flow, f"async_step_{step}")()
                )
        self.assert_serializable(self.flow._mfa_form())

    async def test_invalid_username_never_reaches_login(self) -> None:
        for step in ("user", "reauth_confirm", "reconfigure"):
            for username in (
                "",
                "a" * 255,
                "\U0001f600" * 64,
                "user\ud800@example.com",
            ):
                with self.subTest(step=step, username=repr(username)):
                    with patch.object(
                        self.module, "_validate", new_callable=AsyncMock
                    ) as login:
                        result = await getattr(self.flow, f"async_step_{step}")(
                            {"username": username, "password": "fixture-password"}
                        )
                    self.assertEqual(result["errors"], {"username": "invalid_username"})
                    login.assert_not_awaited()
                    self.assert_serializable(result)
        self.flow.async_set_unique_id.assert_not_awaited()

    async def test_valid_username_still_logs_in(self) -> None:
        credentials = {"username": "user@example.com", "password": "fixture-password"}
        with patch.object(self.module, "_validate", new_callable=AsyncMock) as login:
            login.return_value = {"fixture": "session"}
            result = await self.flow.async_step_user(credentials)
        login.assert_awaited_once_with(self.flow.hass, *credentials.values())
        self.assertEqual(result["data"], {"fixture": "session"})

    async def test_options_serialize_and_validate_on_submission(self) -> None:
        flow = self.module.SpGroupOptionsFlow()
        flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
        flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
        with patch.object(
            type(flow),
            "config_entry",
            new_callable=PropertyMock,
            return_value=self.entry,
        ):
            self.assert_serializable(await flow.async_step_init())
            for raw in ("free", "-0.29", "nan", "inf", True):
                with self.subTest(raw=raw):
                    result = await flow.async_step_init({"electricity_price": raw})
                    self.assertEqual(
                        result["errors"], {"electricity_price": "invalid_price"}
                    )
                    self.assert_serializable(result)
            flow.async_create_entry.assert_not_called()
            for cleared in ("", "   ", "0", None):
                with self.subTest(clear=cleared):
                    result = await flow.async_step_init({"electricity_price": cleared})
                    self.assertEqual(result["data"], {"unrelated": "preserved"})
            result = await flow.async_step_init({"electricity_price": " 0.31 "})
            self.assertEqual(
                result["data"], {"electricity_price": 0.31, "unrelated": "preserved"}
            )


if __name__ == "__main__":
    unittest.main()
