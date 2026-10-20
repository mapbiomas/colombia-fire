"""Offline M1 -> M2 integration: completed exports, shard checks and COG paths."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
sys.path.insert(0, str(CORE))
from M1_mosaic_preview_ui import BANDS


class M2IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = types.ModuleType('M0_auth_config')
        self.config.CONFIG = {'bucket': 'test', 'bands_all': list(BANDS)}
        self.config.GLOBAL_OPTS = {'PERSONAL_TASK_FLAG': 'MONITOR', 'SENSOR': ['sentinel2']}
        self.config._single = lambda value: value[0] if isinstance(value, list) else value
        self.config.mosaic_name = lambda y, m=None, period='monthly', band=None, sensor=None, mosaic=None: (
            f'image_colombia_fire_{sensor}_{mosaic}' + (f'_{band}' if band else '') + f'_{y}_{m:02d}')
        for name in ('_get_fs', 'monthly_chunk_path', 'monthly_mosaic_path', 'monthly_cog_path',
                     'yearly_chunk_path', 'yearly_mosaic_path', 'yearly_cog_path', 'get_temp_dir',
                     'check_command_exists', 'ensure_gdal_path'):
            setattr(self.config, name, MagicMock())
        self.config.monthly_chunk_path.return_value = 'SENTINEL2/MONTHLY/MINNBR/2025_01/CHUNKS'
        self.config.monthly_cog_path.return_value = 'SENTINEL2/MONTHLY/MINNBR/2025_01/COG'
        self.gcs = types.ModuleType('M_gcs')
        self.gcs.upload = MagicMock()
        self.gcs.download = MagicMock(side_effect=lambda remote, local: Path(local).write_bytes(b'shard'))
        modules = patch.dict(sys.modules, {'M0_auth_config': self.config, 'M_gcs': self.gcs})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location('m2_under_test', CORE / 'M2_mosaic_logic.py')
        self.logic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.logic)
        from M1_mosaic_preview_ui import _config_signature
        self.prepared = {'year': 2025, 'month': 1, 'method': 'minnbr', 'signature': _config_signature()}
        self.rows = []
        for band in BANDS:
            task = MagicMock()
            task.status.return_value = {'state': 'COMPLETED'}
            name = self.config.mosaic_name(2025, 1, band=band, sensor='sentinel2', mosaic='minnbr')
            self.rows.append({'destination': 'GCS', 'description': 'MONITOR_GCS_' + name, 'task': task})
        self.exporter = types.SimpleNamespace(session=types.SimpleNamespace(prepared=self.prepared, tasks=self.rows))
        self.files = ['gs://test/CHUNKS/' + self.config.mosaic_name(2025, 1, band=b,
                      sensor='sentinel2', mosaic='minnbr') + '_000.tif' for b in BANDS]

    def test_selection_uses_the_preceding_month_and_method(self):
        selected = self.logic.get_exported_month(self.exporter)
        self.assertEqual((selected['year'], selected['month'], selected['mosaic_method']), (2025, 1, 'minnbr'))
        self.assertEqual(selected['bands'], list(BANDS))

    def test_blocks_pending_failed_or_missing_gcs_tasks(self):
        for state in ('READY', 'RUNNING', 'FAILED', 'CANCELLED'):
            self.rows[0]['task'].status.return_value = {'state': state}
            with self.assertRaisesRegex(ValueError, state):
                self.logic.get_exported_month(self.exporter)
        self.exporter.session.tasks = []
        with self.assertRaisesRegex(ValueError, 'SIN EXPORTAR'):
            self.logic.get_exported_month(self.exporter)

    def test_ignores_asset_tasks_and_uses_the_latest_gcs_retry(self):
        self.exporter.session.tasks = self.rows + [dict(self.rows[0], destination='ASSET', task=MagicMock())]
        self.logic.get_exported_month(self.exporter)
        retry = dict(self.rows[0], task=MagicMock())
        retry['task'].status.return_value = {'state': 'RUNNING'}
        self.exporter.session.tasks.append(retry)
        with self.assertRaisesRegex(ValueError, 'RUNNING'):
            self.logic.get_exported_month(self.exporter)

    def test_blocks_changed_configuration_or_missing_preview(self):
        self.config.CONFIG['bucket'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'configuración'):
            self.logic.get_exported_month(self.exporter)
        self.exporter.session.prepared = None
        with self.assertRaises(ValueError):
            self.logic.get_exported_month(self.exporter)

    def test_blocks_missing_shards_before_assembly(self):
        with patch.object(self.logic, 'list_gcs_files', return_value=self.files[:-1]), \
             patch.object(self.logic, 'assemble_country_mosaic') as assemble:
            with self.assertRaisesRegex(ValueError, 'dayOfYear'):
                self.logic.assemble_from_export(self.exporter)
            assemble.assert_not_called()

    def test_real_m2_engine_assembles_seven_bands_into_the_cog_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.config.get_temp_dir.return_value = tmp
            with patch.object(self.logic, 'check_m2_dependencies', return_value=[]), \
                 patch.object(self.logic, 'list_gcs_files', return_value=self.files), \
                 patch.object(self.logic, 'run_cmd') as command:
                results = self.logic.assemble_from_export(self.exporter)
            self.assertEqual(len(results), 7)
            self.assertTrue(all('/2025_01/COG/' in p and p.endswith('_cog.tif') for p in results))
            self.assertEqual(self.gcs.download.call_count, 7)
            self.assertEqual(self.gcs.upload.call_count, 7)
            self.assertEqual(command.call_count, 14)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_gcs_listing_handles_urls_with_or_without_scheme(self):
        self.config._get_fs.return_value.find.return_value = ['test/a.tif', 'gs://test/b.tif', 'test/x.txt']
        self.assertEqual(self.logic.list_gcs_files('prefix'), ['gs://test/a.tif', 'gs://test/b.tif'])

    def test_missing_transfer_dependency_stops_processing(self):
        with patch.object(self.logic, 'check_m2_dependencies', return_value=['gsutil']):
            with self.assertRaisesRegex(RuntimeError, 'gsutil'):
                self.logic.assemble_country_mosaic(2025, 1, sensor='sentinel2')


if __name__ == '__main__':
    unittest.main()
