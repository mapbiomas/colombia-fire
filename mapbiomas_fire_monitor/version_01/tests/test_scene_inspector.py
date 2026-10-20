"""Offline checks for unmasked inspection and explicit GEE-to-STAC selection."""
from datetime import datetime, timezone
import ast
import contextlib
import io
from datetime import date
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
sys.path.insert(0, str(CORE))
from M_scene_inspector import DOWNLOAD_BANDS, SceneInspectionSession, SceneInspectorUI, MonthlyDownloadUI, match_selected_scenes, filter_blocklisted_scenes, download_month, archive_download_result, copy_debug_file


class SceneInspectorTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = types.ModuleType('Sentinel2_pipeline_test')
        self.pipeline._item_tile = lambda item: item.properties['tile']
        self.pipeline._item_datetime = lambda item: item.properties['datetime']
        self.pipeline.PipelineConfig = MagicMock()
        self.pipeline.select_region = MagicMock()
        self.pipeline.search_scenes = MagicMock()
        self.pipeline.download_scenes = MagicMock()
        p = patch.dict(sys.modules, {'Sentinel2_pipeline_test': self.pipeline})
        p.start()
        self.addCleanup(p.stop)
        self.params = {'region_id': 7, 'regions_asset': 'regions', 'start_date': '2026-01-01',
                       'end_date': '2026-02-01'}
        self.workflow = types.SimpleNamespace(tiles_path=Path('tiles.zip'), regions_path=Path('regions.zip'),
                                             results_dir=Path('results'), archive_dir=Path('drive-results'),
                                             load_gee_vectors=MagicMock())
        self.session = SceneInspectionSession(self.workflow, self.params)
        self.time = int(datetime(2026, 1, 12, 15, 30, tzinfo=timezone.utc).timestamp() * 1000)
        self.row = {'id': 'COPERNICUS/S2_SR_HARMONIZED/scene1', 'tile': '18NZL',
                    'time': self.time, 'cloud': 90, 'product_id': 'PRODUCT_A.SAFE'}

    def item(self, name='item1', product='PRODUCT_A', tile='18NZL', date='2026-01-12T15:30:00Z'):
        return types.SimpleNamespace(id=name, properties={
            'tile': tile, 'datetime': date, 's2:product_uri': product})

    def test_product_match_requires_same_tile_and_normalizes_safe_suffix(self):
        correct = self.item()
        self.assertEqual(match_selected_scenes([self.row], [self.item('wrong', tile='18NZM'), correct]), [correct])

    def test_timestamp_fallback_requires_near_exact_acquisition(self):
        correct = self.item(product='', date='2026-01-12T15:30:01Z')
        self.assertEqual(match_selected_scenes([self.row], [correct]), [correct])
        with self.assertRaises(ValueError):
            match_selected_scenes([self.row], [self.item(product='', date='2026-01-12T16:00:00Z')])

    def test_missing_scene_never_substitutes_another_acquisition(self):
        with self.assertRaisesRegex(ValueError, 'no disponibles'):
            match_selected_scenes([self.row], [self.item(tile='18NZM')])

    def test_inspection_lists_all_scenes_without_cloud_filter_or_masks(self):
        ee = MagicMock()
        ee.Dictionary.return_value.getInfo.return_value = {'region_count': 1, 'scenes': [self.row]}
        with patch.dict(sys.modules, {'ee': ee}):
            self.assertEqual(self.session.inspect(), [self.row])
        collection = ee.ImageCollection.return_value.filterBounds.return_value.filterDate.return_value
        collection.filter.assert_not_called()
        collection.map.assert_not_called()
        ee.Filter.lte.assert_not_called()
        ee.ImageCollection.assert_called_once_with('COPERNICUS/S2_SR_HARMONIZED')
        ee.ImageCollection.return_value.filterBounds.return_value.filterDate.assert_called_once_with(
            '2026-01-01', '2026-02-01')

    def test_scene_preview_only_selects_scales_and_clips(self):
        self.session.scenes = [self.row]
        self.session.geometry = 'region'
        ee = MagicMock()
        with patch.dict(sys.modules, {'ee': ee}):
            image = self.session.scene_image(self.row['id'])
        ee.Image.return_value.select.assert_called_once_with(['B11', 'B8', 'B4'])
        ee.Image.return_value.select.return_value.multiply.assert_called_once_with(.0001)
        self.assertIs(image, ee.Image.return_value.select.return_value.multiply.return_value.clip.return_value)
        ee.Image.return_value.updateMask.assert_not_called()

    def test_download_only_selected_items_and_preserves_the_parameters(self):
        self.session.scenes = [self.row, dict(self.row, id='unselected', product_id='PRODUCT_B')]
        chosen, other = self.item(), self.item('item2', product='PRODUCT_B')
        self.pipeline.search_scenes.return_value.items = [chosen, other]
        with tempfile.TemporaryDirectory() as temp:
            config = types.SimpleNamespace(output_dir=Path(temp), bands=list(DOWNLOAD_BANDS))
            self.pipeline.PipelineConfig.return_value = config
            result = self.session.download([self.row['id']], 'earth-search')
            self.workflow.load_gee_vectors.assert_called_once_with(self.params)
            self.pipeline.download_scenes.assert_called_once_with(config, [chosen])
            self.assertIs(result, self.pipeline.download_scenes.return_value)
            self.assertEqual(self.pipeline.PipelineConfig.call_args.kwargs['bands'], DOWNLOAD_BANDS)
            self.assertNotIn('max_cloud_cover', self.pipeline.PipelineConfig.call_args.kwargs)
            self.assertFalse(self.pipeline.search_scenes.call_args.kwargs['filter_cloud_cover'])
            self.assertEqual(self.pipeline.PipelineConfig.call_args.kwargs['end_date'], '2026-01-31')
            self.assertTrue((Path(temp) / 'inspection_selection.json').is_file())

    def test_empty_unknown_or_unavailable_selection_never_downloads(self):
        self.session.scenes = [self.row]
        for selected in ([], ['unknown']):
            with self.assertRaises(ValueError):
                self.session.download(selected, 'planetary-computer')
        self.pipeline.search_scenes.return_value.items = []
        with self.assertRaises(ValueError):
            self.session.download([self.row['id']], 'planetary-computer')
        self.pipeline.download_scenes.assert_not_called()

    def test_stac_request_omits_the_cloud_filter_for_inspection(self):
        tree = ast.parse((CORE / 'Sentinel2_pipeline_test.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'search_scenes')
        client = MagicMock()
        client.open.return_value.search.side_effect = RuntimeError('request captured')
        namespace = dict(PipelineConfig=object, SearchResult=object, gpd=MagicMock(), date=date,
                         Client=client, StacApiIO=MagicMock(), _retry_policy=MagicMock())
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<search>', 'exec'), namespace)
        config = types.SimpleNamespace(start_date='2026-01-01', end_date='2026-01-31',
                                       max_cloud_cover=20, collection='sentinel-2-l2a',
                                       stac_url='catalog', region_id=7)
        tile = types.SimpleNamespace(geometry=types.SimpleNamespace(iloc=[
            types.SimpleNamespace(__geo_interface__={'type': 'Polygon', 'coordinates': []})]))
        for enabled, expected in [(False, {}), (True, {'eo:cloud_cover': {'lte': 20}})]:
            with self.assertRaisesRegex(RuntimeError, 'request captured'):
                namespace['search_scenes'](config, tile, filter_cloud_cover=enabled)
            self.assertEqual(client.open.return_value.search.call_args.kwargs['query'], expected)

    def make_ui(self):
        class Widget:
            def __init__(self, *args, **kwargs):
                self.value = kwargs.get('value', args[0] if args and isinstance(args[0], str) else '')
                self.children = tuple(args[0]) if args and isinstance(args[0], list) else ()
                self.disabled = kwargs.get('disabled', False)
            def observe(self, *args, **kwargs): pass
            def on_click(self, *args, **kwargs): pass
            def clear_output(self): pass
            def __enter__(self): return self
            def __exit__(self, *args): return False
        widgets = types.SimpleNamespace(**{name: Widget for name in (
            'Dropdown', 'Button', 'Output', 'VBox', 'HBox', 'HTML', 'Checkbox', 'IntText', 'Layout')})
        p = patch.dict(sys.modules, {'ipywidgets': widgets})
        p.start()
        self.addCleanup(p.stop)
        return SceneInspectorUI(self.workflow, self.params)

    def test_table_checks_every_scene_and_unchecking_excludes_download(self):
        ui = self.make_ui()
        rows = [self.row, dict(self.row, id='scene2')]
        ui._build_table(rows)
        self.assertEqual(ui.selected_ids, [self.row['id'], 'scene2'])
        self.assertEqual(len(ui.table.children), 3)  # header plus two rows
        ui.download_checks['scene2'].value = False
        ui._update_download()
        self.assertEqual(ui.selected_ids, [self.row['id']])
        ui.session.download = MagicMock(return_value=types.SimpleNamespace(paths={}, manifest=[]))
        ui._download()
        ui.session.download.assert_called_once_with([self.row['id']], ui.source.value)
        ui._select_all(False)
        self.assertEqual(ui.selected_ids, [])
        self.assertTrue(ui.download_button.disabled)

    def test_all_scene_layers_are_added_off_and_map_is_embedded_directly(self):
        ui = self.make_ui()
        scenes = [self.row, dict(self.row, id='scene2')]
        ui.session.inspect = MagicMock(return_value=scenes)
        ui.session.scene_image = MagicMock()
        ui.session.geometry = 'region'
        geemap = MagicMock()
        with patch.dict(sys.modules, {'geemap': geemap}):
            ui._inspect()
        canvas = geemap.Map.return_value
        self.assertEqual(ui.map_container.children, (canvas,))
        canvas.add_layer_control.assert_called_once()
        self.assertEqual(canvas.addLayer.call_count, 2)
        self.assertTrue(all(call.kwargs['shown'] is False for call in canvas.addLayer.call_args_list))
        self.assertEqual(ui.selected_ids, [self.row['id'], 'scene2'])

    def test_one_failed_map_layer_does_not_hide_the_other_scenes(self):
        ui = self.make_ui()
        scenes = [self.row, dict(self.row, id='scene2')]
        ui._build_table(scenes)
        ui.map = MagicMock()
        ui.map.addLayer.side_effect = [RuntimeError('tile failure'), None]
        ui.session.scene_image = MagicMock()
        failed = ui._add_scene_layers(scenes)
        self.assertEqual(len(failed), 1)
        self.assertEqual(ui.map.addLayer.call_count, 2)
        self.assertEqual(ui.layer_status['scene2'].value, 'Lista (apagada)')
        self.assertEqual(len(ui.selected_ids), 2)

    def monthly_fixture(self, root, count=15):
        scenes, items = [], []
        for i in range(count):
            instant = datetime.fromtimestamp(self.time / 1000 + i, timezone.utc)
            stamp = instant.strftime('%Y%m%dT%H%M%S')
            scenes.append(dict(self.row, id=f'COPERNICUS/S2_SR_HARMONIZED/{stamp}_20260112T170000_T18NZL',
                               time=self.time + i * 1000, product_id=f'PRODUCT_{i}'))
            items.append(self.item(f'item{i}', product=f'PRODUCT_{i}', date=instant.isoformat()))
        config = types.SimpleNamespace(output_dir=Path(root), bands=list(DOWNLOAD_BANDS))
        self.pipeline.PipelineConfig.return_value = config
        self.pipeline.search_scenes.return_value.items = items
        blocklists = types.ModuleType('M_blocklist')
        blocked = [scene['id'].split('/')[-1] for scene in scenes[:2]]
        blocklists.refresh_blocklists = MagicMock(return_value={'sentinel2': blocked})
        blocklists.blocklist_report = MagicMock(return_value={'sentinel2': {
            'sources': [{'asset': 'test-source', 'status': 'ok', 'count': len(blocked)}]}})
        return scenes, items, blocklists

    def test_monthly_test_mode_excludes_blocklist_before_ten_scene_limit(self):
        with tempfile.TemporaryDirectory() as root:
            scenes, items, blocklists = self.monthly_fixture(root)
            output = io.StringIO()
            with patch.dict(sys.modules, {'M_blocklist': blocklists}), \
                 patch.object(SceneInspectionSession, 'inspect', return_value=scenes), \
                 contextlib.redirect_stdout(output):
                report = download_month(self.workflow, self.params, test_mode=True)
            selected = self.pipeline.download_scenes.call_args.args[1]
            self.assertEqual(selected, items[2:12])
            self.assertEqual(self.pipeline.PipelineConfig.call_args.kwargs['bands'],
                             ('coastal', 'blue', 'green', 'red', 'rededge1', 'rededge2', 'rededge3',
                              'nir', 'nir08', 'nir09', 'swir16', 'swir22', 'scl'))
            self.assertEqual(len(report['selected']), 10)
            self.assertEqual(len(report['excluded']), 2)
            self.assertIn(scenes[0]['id'], output.getvalue())
            self.assertFalse(self.pipeline.search_scenes.call_args.kwargs['filter_cloud_cover'])
            self.assertTrue((Path(root) / 'monthly_download_selection.json').is_file())

    def test_monthly_normal_mode_downloads_all_permitted_scenes(self):
        with tempfile.TemporaryDirectory() as root:
            scenes, items, blocklists = self.monthly_fixture(root)
            with patch.dict(sys.modules, {'M_blocklist': blocklists}), \
                 patch.object(SceneInspectionSession, 'inspect', return_value=scenes), \
                 contextlib.redirect_stdout(io.StringIO()):
                report = download_month(self.workflow, self.params, test_mode=False)
            self.assertEqual(self.pipeline.download_scenes.call_args.args[1], items[2:])
            self.assertEqual(len(report['selected']), 13)

    def test_monthly_cannot_read_blocklist_does_not_start_download(self):
        with tempfile.TemporaryDirectory() as root:
            scenes, items, blocklists = self.monthly_fixture(root)
            blocklists.blocklist_report.return_value['sentinel2']['sources'][0]['status'] = 'error'
            with patch.dict(sys.modules, {'M_blocklist': blocklists}), \
                 patch.object(SceneInspectionSession, 'inspect') as inspect, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'blocklist'):
                    download_month(self.workflow, self.params)
                inspect.assert_not_called()
            self.pipeline.download_scenes.assert_not_called()

    def test_all_blocklisted_scenes_do_not_trigger_transfers(self):
        with tempfile.TemporaryDirectory() as root:
            scenes, items, blocklists = self.monthly_fixture(root, count=2)
            with patch.dict(sys.modules, {'M_blocklist': blocklists}), \
                 patch.object(SceneInspectionSession, 'inspect', return_value=scenes), \
                 contextlib.redirect_stdout(io.StringIO()):
                report = download_month(self.workflow, self.params, test_mode=True)
            self.assertIsNone(report['result'])
            self.pipeline.download_scenes.assert_not_called()
            self.workflow.load_gee_vectors.assert_not_called()

    def test_blocklist_matches_full_ids_and_reprocessed_acquisitions(self):
        blocked = '20260112T153000_20260112T170000_T18NZL'
        scenes = [dict(self.row, id='COPERNICUS/S2_SR_HARMONIZED/' + blocked),
                  dict(self.row, id='20260112T153000_20260113T170000_T18NZL'),
                  dict(self.row, id='20260113T153000_20260113T170000_T18NZL')]
        allowed, excluded = filter_blocklisted_scenes(scenes, [blocked])
        self.assertEqual(allowed, scenes[2:])
        self.assertEqual(len(excluded), 2)

    def make_monthly_ui(self):
        # Reuse the lightweight widget stand-ins installed by the existing UI tests.
        self.make_ui()
        with patch('M_scene_inspector.region_menu_options', return_value=[('7 · Región 7', 7), ('8 · Región 8', 8)]):
            return MonthlyDownloadUI(self.workflow, 'regions', 'tiles')

    def test_monthly_menu_starts_download_only_on_click(self):
        with patch('M_scene_inspector.download_month') as download:
            ui = self.make_monthly_ui()
            download.assert_not_called()
            ui.year.value = 2026
            ui.month.value = 12
            ui.region.value = 8
            ui.source.value = 'earth-search'
            ui.test_mode.value = True
            download.return_value = {'result': types.SimpleNamespace(paths={'scene': {}})}
            ui._download()
            download.assert_called_once_with(self.workflow, {
                'regions_asset': 'regions', 'tiles_asset': 'tiles', 'region_id': 8,
                'start_date': '2026-12-01', 'end_date': '2027-01-01',
            }, source='earth-search', test_mode=True, max_scenes=10,
               debug_drive_dir=Path('drive-results/debug_scenes/earth-search/region_8/2026-12'))
            self.assertIs(ui.result, download.return_value)
            self.assertFalse(ui.download_button.disabled)

    def test_debug_limit_is_applied_after_blocklist(self):
        with tempfile.TemporaryDirectory() as root:
            scenes, items, blocklists = self.monthly_fixture(root)
            with patch.dict(sys.modules, {'M_blocklist': blocklists}), \
                 patch.object(SceneInspectionSession, 'inspect', return_value=scenes), \
                 contextlib.redirect_stdout(io.StringIO()):
                report = download_month(self.workflow, self.params, test_mode=True, max_scenes=3)
            self.assertEqual(self.pipeline.download_scenes.call_args.args[1], items[2:5])
            self.assertEqual(report['max_scenes'], 3)

    def test_invalid_debug_limit_stops_before_remote_requests(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, blocklists = self.monthly_fixture(root)
            with patch.dict(sys.modules, {'M_blocklist': blocklists}):
                for limit in (0, -1, True, 1.5):
                    with self.assertRaises(ValueError):
                        download_month(self.workflow, self.params, test_mode=True, max_scenes=limit)
            blocklists.refresh_blocklists.assert_not_called()
            self.pipeline.download_scenes.assert_not_called()

    def test_normal_mode_ignores_debug_limit_and_drive_archive(self):
        with tempfile.TemporaryDirectory() as root:
            scenes, items, blocklists = self.monthly_fixture(root)
            with patch.dict(sys.modules, {'M_blocklist': blocklists}), \
                 patch.object(SceneInspectionSession, 'inspect', return_value=scenes), \
                 patch('M_scene_inspector.archive_download_result') as archive, \
                 contextlib.redirect_stdout(io.StringIO()):
                report = download_month(self.workflow, self.params, max_scenes=1, debug_drive_dir=Path(root)/'drive')
            self.assertEqual(self.pipeline.download_scenes.call_args.args[1], items[2:])
            self.assertIsNone(report['debug_drive_dir'])
            archive.assert_not_called()

    def test_menu_passes_custom_count_and_disables_limit_outside_debug(self):
        ui = self.make_monthly_ui()
        ui.scene_limit.value = 4
        with patch('M_scene_inspector.download_month', return_value={'result': None}) as download:
            ui._download()
        self.assertEqual(download.call_args.kwargs['max_scenes'], 4)
        ui.test_mode.value = False
        ui._debug_changed()
        self.assertTrue(ui.scene_limit.disabled)

    def test_archive_preserves_native_bands_and_calibration_without_signed_urls(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root/'source.tif'
            source.write_bytes(b'native raster bytes')
            result = types.SimpleNamespace(paths={'scene': {'nir': source}}, manifest=[{
                'item_id': 'scene', 'asset': 'nir', 'source_url': 'https://signed.example',
                'raster_bands': [{'scale': .0001, 'offset': -.1}]}])
            archived = archive_download_result(result, root/'drive')
            self.assertEqual(Path(archived['scene']['nir']).read_bytes(), source.read_bytes())
            manifest = json.loads((root/'drive/download_manifest.json').read_text())
            self.assertNotIn('source_url', manifest[0])
            self.assertEqual(manifest[0]['raster_bands'], result.manifest[0]['raster_bands'])
            self.assertEqual(manifest[0]['path'], 'scenes/scene/nir.tif')

    def test_interrupted_archive_preserves_previous_copy(self):
        with tempfile.TemporaryDirectory() as root:
            source, dest = Path(root)/'new', Path(root)/'saved'
            source.write_bytes(b'new raster')
            dest.write_bytes(b'previous raster')
            with patch('shutil.copy2', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    copy_debug_file(source, dest)
            self.assertEqual(dest.read_bytes(), b'previous raster')
            self.assertFalse(Path(str(dest)+'.partial').exists())

    def test_processing_snapshot_is_unchanged_when_menu_options_change(self):
        ui = self.make_monthly_ui()
        ui.year.value, ui.month.value = 2026, 1
        with patch('M_scene_inspector.download_month', return_value={'result': types.SimpleNamespace(paths={'scene': {}})}):
            ui._download()
        ui.month.value, ui.region.value, ui.source.value = 2, 8, 'earth-search'
        self.assertEqual(ui.download_parameters['start_date'], '2026-01-01')
        self.assertEqual(ui.download_parameters['region_id'], 7)
        self.assertEqual(ui.download_source, 'planetary-computer')
        self.assertEqual(ui.parameters()['start_date'], '2026-02-01')

    def test_failed_download_clears_previous_result_and_reenables_menu(self):
        ui = self.make_monthly_ui()
        ui.result = {'result': 'previous'}
        with patch('M_scene_inspector.download_month', side_effect=RuntimeError('No access')):
            ui._download()
        self.assertIsNone(ui.result)
        self.assertIsNone(ui.download_parameters)
        self.assertFalse(ui.download_button.disabled)
        self.assertFalse(ui.region.disabled)


if __name__ == '__main__':
    unittest.main()
