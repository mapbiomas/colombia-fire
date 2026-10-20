"""Resource accounting checks with deterministic RSS and a fake CUDA backend."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
sys.path.insert(0, str(CORE))
from M_resource_monitor import MIB, ResourceMonitor


class ResourceMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.process = MagicMock()
        self.process.memory_info.return_value = types.SimpleNamespace(rss=100*MIB)
        psutil = types.ModuleType('psutil')
        psutil.Process = MagicMock(return_value=self.process)
        modules = patch.dict(sys.modules, {'psutil': psutil})
        modules.start()
        self.addCleanup(modules.stop)

    def cuda(self):
        cuda = MagicMock()
        cuda.memory_allocated.return_value = 100*MIB
        cuda.max_memory_allocated.return_value = 200*MIB
        cuda.max_memory_reserved.return_value = 300*MIB
        cuda.utilization.return_value = 65
        return cuda

    def test_ram_peak_delta_cpu_and_files(self):
        self.process.memory_info.side_effect = [types.SimpleNamespace(rss=n*MIB) for n in (100, 110, 140)]
        with ResourceMonitor(self.root, interval=100) as monitor:
            with monitor.phase('SCL', 'scene'):
                pass
        row = monitor.summary()[0]
        self.assertEqual(row['ram_peak_mb'], 140)
        self.assertEqual(row['ram_extra_peak_mb'], 40)
        self.assertIsNone(row['gpu_memory_peak_mb'])
        self.assertIsNone(row['gpu_util_mean_pct'])
        self.assertFalse(monitor.thread.is_alive())
        report = json.loads((self.root/'resource_usage.json').read_text())
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['stages'][0]['scene_id'], 'scene')
        with (self.root/'resource_comparison.csv').open() as stream:
            self.assertEqual(next(csv.DictReader(stream))['method'], 'SCL')

    def test_cuda_sync_reset_memory_and_utilization(self):
        cuda = self.cuda()
        with ResourceMonitor(self.root, interval=100) as monitor:
            monitor.configure_gpu(cuda, 'cuda:0')
            with monitor.phase('CloudSEN12', 'scene', gpu=True):
                pass
        row = monitor.summary()[0]
        self.assertEqual(cuda.synchronize.call_count, 2)
        cuda.reset_peak_memory_stats.assert_called_once_with('cuda:0')
        self.assertEqual(row['gpu_memory_peak_mb'], 200)
        self.assertEqual(row['gpu_memory_extra_peak_mb'], 100)
        self.assertEqual(row['gpu_reserved_peak_mb'], 300)
        self.assertEqual(row['gpu_util_mean_pct'], 65)
        self.assertEqual(row['gpu_util_peak_pct'], 65)

    def test_scl_does_not_attribute_resident_gpu_memory_to_cpu_work(self):
        cuda = self.cuda()
        with ResourceMonitor(self.root, interval=100) as monitor:
            monitor.configure_gpu(cuda, 'cuda:0')
            with monitor.phase('SCL', 'scene'):
                pass
        cuda.synchronize.assert_not_called()
        cuda.memory_allocated.assert_not_called()
        self.assertEqual(monitor.summary()[0]['device'], 'cpu')
        self.assertIsNone(monitor.summary()[0]['gpu_memory_peak_mb'])

    def test_missing_nvml_is_unavailable_not_zero_and_memory_still_works(self):
        cuda = self.cuda()
        cuda.utilization.side_effect = RuntimeError('NVML unavailable')
        with ResourceMonitor(self.root, interval=100) as monitor:
            monitor.configure_gpu(cuda, 'cuda:0')
            with monitor.phase('CloudSEN12', gpu=True):
                pass
        self.assertIsNone(monitor.summary()[0]['gpu_util_mean_pct'])
        self.assertEqual(monitor.summary()[0]['gpu_memory_peak_mb'], 200)
        self.assertIn('NVML', json.loads((self.root/'resource_usage.json').read_text())['gpu_util_error'])

    def test_summary_adds_time_and_uses_maximum_not_sum_for_memory(self):
        with ResourceMonitor(self.root, interval=100) as monitor:
            for scene in ('scene1', 'scene1', 'scene2'):
                with monitor.phase('SCL', scene):
                    pass
        summary = monitor.summary()[0]
        self.assertEqual(len(monitor.rows), 2)
        self.assertEqual(summary['calls'], 3)
        self.assertEqual(summary['ram_peak_mb'], 100)
        self.assertAlmostEqual(summary['duration_s'], sum(row['duration_s'] for row in monitor.rows.values()))

    def test_failure_keeps_measurements_and_stops_sampler(self):
        with self.assertRaisesRegex(ValueError, 'inference failure'):
            with ResourceMonitor(self.root, interval=100) as monitor:
                with monitor.phase('CloudSEN12', 'scene'):
                    raise ValueError('inference failure')
        self.assertFalse(monitor.thread.is_alive())
        report = json.loads((self.root/'resource_usage.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(report['stages']), 1)

    def test_live_widget_shows_stage_ram_and_final_table(self):
        widget = types.SimpleNamespace(value='')
        widgets = types.ModuleType('ipywidgets')
        widgets.HTML = lambda value: widget
        display = types.ModuleType('IPython.display')
        display.display = MagicMock()
        parent = types.ModuleType('IPython')
        parent.display = display
        with patch.dict(sys.modules, {'ipywidgets': widgets, 'IPython': parent, 'IPython.display': display}):
            with ResourceMonitor(self.root, live=True, interval=100) as monitor:
                with monitor.phase('SCL', 'scene'):
                    self.assertIn('RAM actual', widget.value)
                    self.assertIn('SCL', widget.value)
        self.assertIn('Recursos por etapa', widget.value)
        self.assertIn('RAM pico', widget.value)


if __name__ == '__main__':
    unittest.main()
