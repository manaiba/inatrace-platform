import os
import unittest
from unittest import mock

from inatrace import config, stack


class ModeTest(unittest.TestCase):
    def test_default_and_override(self):
        self.assertEqual(stack.mode(config.Settings()), "fullstack-dev")
        settings = config.Settings(values={"INATRACE_MODE": "images"})
        self.assertEqual(stack.mode(settings), "images")
        self.assertEqual(stack.mode(settings, "front-dev"), "front-dev")

    def test_unknown(self):
        with self.assertRaises(SystemExit):
            stack.mode(config.Settings(values={"INATRACE_MODE": "prod"}))


class ComposeTest(unittest.TestCase):
    @mock.patch.dict(os.environ, {"INATRACE_BACKEND_VERSION": "9.9.9",
                                  "COMPOSE_PROFILES": "x", "HOME": "/home/dev"})
    def test_environment(self):
        env = stack.compose_env("back-dev")
        self.assertNotIn("INATRACE_BACKEND_VERSION", env)
        self.assertEqual(env["COMPOSE_PROFILES"], "back-dev")
        self.assertEqual(env["HOME"], "/home/dev")
        self.assertNotIn("COMPOSE_PROFILES", stack.compose_env(None))

    def test_command(self):
        cmd = stack.compose_cmd("ps")
        self.assertEqual(cmd[:2], ["docker", "compose"])
        self.assertEqual(cmd[-3:], ["-f", str(stack.paths.DEV_STACK_COMPOSE), "ps"][-3:])

    def test_modes_match_the_compose_profiles(self):
        text = stack.paths.DEV_STACK_COMPOSE.read_text()
        self.assertIn("profiles: [front-dev, images]", text)
        self.assertIn("profiles: [back-dev, images]", text)


if __name__ == "__main__":
    unittest.main()
