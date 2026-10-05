"""Portable deployment invariants for systemd-managed funes-vision services."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEMD = os.path.join(ROOT, "systemd")
MODELS_TOKEN = "__WEBCAM_MODELS_DIR__"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def unit(name):
    return read(os.path.join(SYSTEMD, name))


class TestMountOrdering(unittest.TestCase):
    UNITS = (
        "webcam-api.service",
        "webcam-pipeline@.service",
        "ollama.service",
        "webcam-compose.service",
        "webcam-healthcheck.service",
    )

    def test_data_consumers_require_rendered_models_mount(self):
        for name in self.UNITS:
            with self.subTest(unit=name):
                self.assertIn(
                    f"RequiresMountsFor={MODELS_TOKEN}",
                    unit(name),
                )

    def test_local_data_consumers_do_not_depend_only_on_remote_fs(self):
        for name in self.UNITS:
            with self.subTest(unit=name):
                text = unit(name)
                self.assertNotIn("After=network.target remote-fs.target", text)

    def test_installer_renders_models_dir_token(self):
        text = read(os.path.join(SYSTEMD, "install.sh"))
        self.assertIn("__WEBCAM_MODELS_DIR__", text)
        self.assertIn("${WEBCAM_MODELS_DIR}", text)


class TestHealthcheckSourceOfTruth(unittest.TestCase):
    def test_healthcheck_executes_repo_script(self):
        text = unit("webcam-healthcheck.service")
        self.assertIn("${WEBCAM_DIR}/tools/webcam-healthcheck.sh", text)
        self.assertNotIn("/usr/local/bin/webcam-healthcheck.sh", text)

    def test_installer_does_not_copy_stale_healthcheck(self):
        text = read(os.path.join(SYSTEMD, "install.sh"))
        self.assertNotRegex(
            text,
            re.compile(
                r"install\s+-m\s+755\s+.*webcam-healthcheck\.sh\s+"
                r"/usr/local/bin/webcam-healthcheck\.sh"
            ),
        )


if __name__ == "__main__":
    unittest.main()
