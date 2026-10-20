"""Offline checks: preview/export identity, dates, duplicate tasks, partial failure."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
sys.path.insert(0, str(CORE))
from M1_mosaic_preview_ui import BANDS, MonthlyMosaicSession, MonthlyMosaicUI, month_dates


class MonthlyMosaicTests(unittest.TestCase):
    def setUp(self):
        self.config = types.ModuleType('M0_auth_config')
        self.config.CONFIG = {'mosaic_methods': ['minnbr', 'minnbr_buffer'], 'country': 'colombia'}
        self.config.GLOBAL_OPTS = {'PERSONAL_TASK_FLAG': 'MONITOR'}
        self.config.get_country_geometry = MagicMock()
        self.config.mosaic_name = lambda y, m, p, band, mosaic, sensor: f'{mosaic}_{band}_{y}_{m:02d}'
        self.logic = types.ModuleType('M1_export_logic')
        self.image = MagicMock()
        self.image.bandNames.return_value.getInfo.return_value = [
            'blue', 'green', 'red', 'nir', 'swir1', 'swir2', 'dayOfYear']
        self.logic.get_quality_mosaic = MagicMock(return_value=self.image)
        self.logic.export_to_asset = MagicMock(side_effect=self.new_task)
        self.logic.export_to_gcs = MagicMock(side_effect=lambda *a, **kw: [self.new_task()])
        self.modules = patch.dict(sys.modules, {'M0_auth_config': self.config, 'M1_export_logic': self.logic})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.session = MonthlyMosaicSession()

    @staticmethod
    def new_task(*args, **kwargs):
        task = MagicMock()
        task.status.return_value = {'state': 'READY'}
        return task

    def test_month_boundaries(self):
        self.assertEqual(month_dates(2024, 2), ('2024-02-01', '2024-03-01'))
        self.assertEqual(month_dates(2025, 12), ('2025-12-01', '2026-01-01'))
        with self.assertRaises(ValueError):
            month_dates(2025, 13)

    def test_exports_the_preview_image_to_both_destinations(self):
        self.session.prepare(2025, 1, 'minnbr')
        errors = self.session.export(['red', 'dayOfYear'], ['ASSET', 'GCS'])
        self.assertEqual(errors, [])
        self.assertEqual(len(self.session.tasks), 4)
        self.logic.get_quality_mosaic.assert_called_once_with(
            'sentinel2', 2025, '2025-01-01', '2025-02-01',
            self.config.get_country_geometry.return_value, month=1, method='minnbr')
        self.image.bandNames.assert_not_called()
        for fn in (self.logic.export_to_asset, self.logic.export_to_gcs):
            for call in fn.call_args_list:
                self.assertIs(call.args[0], self.image)
        self.assertFalse(self.logic.export_to_asset.call_args.kwargs['overwrite'])

    def test_interface_exports_all_bands_without_a_band_selector(self):
        ui = MonthlyMosaicUI.__new__(MonthlyMosaicUI)
        ui.session = MagicMock()
        ui.session.export.return_value = []
        ui.asset = types.SimpleNamespace(value=True)
        ui.gcs = types.SimpleNamespace(value=True)
        ui.messages = MagicMock()
        ui._busy = MagicMock()
        ui._refresh = MagicMock()
        ui._export()
        ui.session.export.assert_called_once_with(BANDS, ['ASSET', 'GCS'])

    def test_cannot_export_before_preview_or_after_config_change(self):
        with self.assertRaises(ValueError):
            self.session.export(['red'], ['ASSET'])
        self.session.prepare(2025, 1, 'minnbr')
        self.config.CONFIG['country'] = 'changed'
        with self.assertRaises(ValueError):
            self.session.export(['red'], ['GCS'])
        self.logic.export_to_gcs.assert_not_called()

    def test_duplicate_click_does_not_queue_another_task_and_failed_tasks_retry(self):
        self.session.prepare(2025, 1, 'minnbr')
        self.session.export(['red'], ['GCS'])
        self.session.export(['red'], ['GCS'])
        self.assertEqual(len(self.session.tasks), 1)
        self.session.tasks[0]['task'].status.return_value = {'state': 'FAILED'}
        self.session.export(['red'], ['GCS'])
        self.assertEqual(len(self.session.tasks), 2)

    def test_partial_failure_keeps_successful_task_receipts(self):
        self.session.prepare(2025, 1, 'minnbr')
        self.logic.export_to_asset.side_effect = [self.new_task(), RuntimeError('Denied')]
        errors = self.session.export(['red', 'nir'], ['ASSET', 'GCS'])
        self.assertEqual(len(self.session.tasks), 3)
        self.assertEqual(errors, ['ASSET / nir: Denied'])

    def test_failed_rebuild_invalidates_previous_preview(self):
        self.session.prepare(2025, 1, 'minnbr')
        self.logic.get_quality_mosaic.side_effect = RuntimeError('No imagery')
        with self.assertRaises(RuntimeError):
            self.session.prepare(2025, 2, 'minnbr')
        self.assertIsNone(self.session.prepared)

    def test_rejects_incomplete_month_and_invalid_export_selection(self):
        with self.assertRaises(ValueError):
            self.session.prepare(9998, 1, 'minnbr')
        self.logic.get_quality_mosaic.assert_not_called()
        self.session.prepare(2025, 1, 'minnbr')
        for bands, destinations in [([], ['GCS']), (['red'], []), (['B4'], ['GCS'])]:
            with self.assertRaises(ValueError):
                self.session.export(bands, destinations)

    def test_shared_export_helpers_return_tasks_and_valid_asset_policy(self):
        ee = MagicMock()
        self.config.get_sensor_scale = lambda sensor: 10
        self.config._single = lambda value: value[0] if isinstance(value, list) else value
        self.config.get_asset_mosaic_collection = lambda **kw: 'projects/test/assets/mosaics/red'
        self.config.monthly_chunk_path = lambda *a, **kw: 'monthly/CHUNKS'
        self.config.yearly_chunk_path = lambda *a, **kw: 'yearly/CHUNKS'
        self.config.CONFIG['bucket'] = 'test-bucket'
        spec = importlib.util.spec_from_file_location('export_logic_under_test', CORE / 'M1_export_logic.py')
        logic = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'ee': ee}):
            spec.loader.exec_module(logic)
            tasks = logic.export_to_gcs(self.image, 'name', 2025, 1,
                                        bands=['red'], sensor='sentinel2')
            self.assertEqual(tasks, [ee.batch.Export.image.toCloudStorage.return_value])
            tasks[0].start.assert_called_once()
            with patch.object(logic, 'ensure_asset_path'):
                task = logic.export_to_asset(self.image, 'name', 2025, 1,
                                              band='red', sensor='sentinel2', overwrite=False)
            task.start.assert_called_once()
            opts = ee.batch.Export.image.toAsset.call_args.kwargs
            self.assertEqual(opts['pyramidingPolicy'], {'.default': 'mean'})
            self.assertFalse(opts['overwrite'])


if __name__ == '__main__':
    unittest.main()
