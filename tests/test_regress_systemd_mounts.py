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


    def test_environment_file_is_in_service_section(self):
        for name in self.UNITS:
            with self.subTest(unit=name):
                text = unit(name)
                before, service = text.split("[Service]", 1)
                self.assertNotIn("EnvironmentFile=", before)
                self.assertIn(
                    "EnvironmentFile=-/etc/webcam/webcam.env",
                    service,
                )

    def test_installer_renders_execstart_path_tokens(self):
        text = read(os.path.join(SYSTEMD, "install.sh"))
        self.assertIn("__WEBCAM_DIR__", text)
        self.assertIn("__WEBCAM_OLLAMA_BIN__", text)
        self.assertIn("${WEBCAM_OLLAMA_BIN}", text)

    def test_installer_renders_models_dir_token(self):
        text = read(os.path.join(SYSTEMD, "install.sh"))
        self.assertIn("__WEBCAM_MODELS_DIR__", text)
        self.assertIn("${WEBCAM_MODELS_DIR}", text)


class TestComposeHealthcheckSerialization(unittest.TestCase):
    def test_timer_delays_first_probe_from_activation(self):
        text = unit("webcam-healthcheck.timer")
        self.assertIn("OnActiveSec=30s", text)
        self.assertNotIn("OnBootSec=", text)

    def test_compose_mutations_share_a_lock(self):
        runner = read(os.path.join(ROOT, "tools", "webcam-compose-run.sh"))
        health = read(os.path.join(ROOT, "tools", "webcam-healthcheck.sh"))
        lock = "/run/lock/funes-gallery-compose.lock"
        self.assertIn(lock, runner)
        self.assertIn(lock, health)
        self.assertIn("flock -x 9", runner)
        self.assertIn("flock -x 9", health)

    def test_healthcheck_rechecks_after_lock(self):
        text = read(os.path.join(ROOT, "tools", "webcam-healthcheck.sh"))
        lock_pos = text.index("flock -x 9")
        recheck_pos = text.index(
            'docker ps --filter "name=${CONTAINER}"',
            lock_pos,
        )
        compose_pos = text.index(
            '"${COMPOSE[@]}" up -d "${CONTAINER}"',
            lock_pos,
        )
        self.assertLess(recheck_pos, compose_pos)


class TestHealthcheckSourceOfTruth(unittest.TestCase):
    def test_healthcheck_executes_repo_script(self):
        text = unit("webcam-healthcheck.service")
        self.assertIn("__WEBCAM_DIR__/tools/webcam-healthcheck.sh", text)
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
