"""Offline GEE geometry loading checks, without raster or GIS dependencies."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
tree = ast.parse((CORE / 'Sentinel2_pipeline_test.py').read_text())
workflow_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'NotebookWorkflow')
method = next(n for n in workflow_class.body if isinstance(n, ast.FunctionDef) and n.name == 'load_gee_vectors')
namespace = {'json': json, 'hashlib': hashlib}
exec(compile(ast.Module(body=[method], type_ignores=[]), '<gee-vectors>', 'exec'), namespace)
load_gee_vectors = namespace['load_gee_vectors']


class GEEVectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workflow = types.SimpleNamespace(vectors_dir=root, regions_path=root/'old-regions.zip',
                                               tiles_path=root/'old-tiles.zip')
        self.params = {'regions_asset': 'regions-v1', 'tiles_asset': 'tiles', 'region_id': 7}
        geometry = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        self.region = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': geometry, 'properties': {'id_monitor': 7}}]}
        self.tiles = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': geometry, 'properties': {'MGRS_TILE': '18NZL'}}]}
        self.ee = MagicMock()
        self.regions_fc, self.tiles_fc = MagicMock(), MagicMock()
        self.ee.FeatureCollection.side_effect = lambda asset: self.regions_fc if asset == 'regions-v1' else self.tiles_fc
        self.regions_fc.filter.return_value.getInfo.side_effect = lambda: self.region
        self.tiles_fc.filterBounds.return_value.getInfo.side_effect = lambda: self.tiles
        p = patch.dict(sys.modules, {'ee': self.ee})
        p.start()
        self.addCleanup(p.stop)

    def test_assets_supply_geojson_region_id_and_tile_names(self):
        load_gee_vectors(self.workflow, self.params)
        region = json.loads(self.workflow.regions_path.read_text())
        tiles = json.loads(self.workflow.tiles_path.read_text())
        self.assertEqual(region['features'][0]['properties']['id_monitor'], 7)
        self.assertEqual(region['features'][0]['properties']['nombre'], 'Región 7')
        self.assertEqual(tiles['features'][0]['properties']['Name'], '18NZL')
        self.assertEqual(self.workflow.regions_path.suffix, '.geojson')
        self.assertEqual(self.workflow.tiles_path.suffix, '.geojson')
        self.tiles_fc.filterBounds.assert_called_once_with(self.regions_fc.filter.return_value.geometry.return_value)

    def test_same_region_reuses_files_but_changed_region_refreshes(self):
        load_gee_vectors(self.workflow, self.params)
        load_gee_vectors(self.workflow, self.params)
        self.assertEqual(self.ee.FeatureCollection.call_count, 2)
        previous = self.workflow.regions_path
        load_gee_vectors(self.workflow, dict(self.params, region_id=8))
        self.assertEqual(self.ee.FeatureCollection.call_count, 4)
        self.assertNotEqual(previous, self.workflow.regions_path)

    def test_missing_region_or_tiles_does_not_replace_workflow_paths(self):
        self.region['features'] = []
        with self.assertRaises(ValueError):
            load_gee_vectors(self.workflow, self.params)
        self.assertEqual(self.workflow.regions_path.name, 'old-regions.zip')
        self.assertEqual(list(self.workflow.vectors_dir.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
