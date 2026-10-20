"""Offline checks for unmasked inspection and explicit GEE-to-STAC selection."""
from datetime import datetime, timezone
import ast
from datetime import date
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
sys.path.insert(0, str(CORE))
from M_scene_inspector import SceneInspectionSession, match_selected_scenes


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
                                             results_dir=Path('results'), load_gee_vectors=MagicMock())
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
            config = types.SimpleNamespace(output_dir=Path(temp), bands=['red', 'nir', 'swir16', 'swir22', 'scl'])
            self.pipeline.PipelineConfig.return_value = config
            result = self.session.download([self.row['id']], 'earth-search')
            self.workflow.load_gee_vectors.assert_called_once_with(self.params)
            self.pipeline.download_scenes.assert_called_once_with(config, [chosen])
            self.assertIs(result, self.pipeline.download_scenes.return_value)
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


if __name__ == '__main__':
    unittest.main()
