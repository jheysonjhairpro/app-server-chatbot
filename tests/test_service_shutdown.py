"""El lanzador debe cerrar sus procesos sin dejar servidores hijos activos."""

from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch


launcher = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/servicios_web.py"))
stop_process = launcher["stop_process"]


class ShutdownTests(unittest.TestCase):
    def test_windows_stops_only_owned_process_tree(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch.object(launcher["os"], "name", "nt"), patch.object(
            launcher["subprocess"], "run"
        ) as run, patch.object(launcher["subprocess"], "CREATE_NO_WINDOW", 0x08000000, create=True):
            stop_process(process)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "12345", "/T", "/F"])
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        process.terminate.assert_not_called()

    def test_finished_process_is_not_signaled(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(launcher["subprocess"], "run") as run:
            stop_process(process)
        run.assert_not_called()
        process.terminate.assert_not_called()

    def test_posix_keeps_normal_termination(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(launcher["os"], "name", "posix"), patch.object(launcher["subprocess"], "run") as run:
            stop_process(process)
        process.terminate.assert_called_once_with()
        run.assert_not_called()
