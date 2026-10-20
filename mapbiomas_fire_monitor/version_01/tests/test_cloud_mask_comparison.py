"""Synthetic GeoTIFF integration checks; no model weights or satellite downloads."""
from pathlib import Path
import sys
import tempfile
import json
import types
import unittest
from unittest.mock import MagicMock, patch

CORE = Path(__file__).resolve().parents[1] / 'src/core'
sys.path.insert(0, str(CORE))
try:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    import M_cloud_mask_comparison as comparison
    HAS_GIS = True
except ImportError:
    HAS_GIS = False


@unittest.skipUnless(HAS_GIS, 'Requires numpy and rasterio')
class CloudMaskComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths, manifest = {}, []
        for index, (band, asset) in enumerate(comparison.L2A_ASSETS.items()):
            factor = 6 if asset in ('coastal', 'nir09') else 2 if asset in (
                'rededge1', 'rededge2', 'rededge3', 'nir08', 'swir16', 'swir22') else 1
            size = 96 // factor
            data = np.full((size, size), 2000+index*50, dtype='uint16')
            if asset == 'blue':
                data[:size//2, size//2:] = 8000
            if asset == 'red':
                data[size//2:, :size//2] = 8000
            data[0, 0] = 0
            self.paths[asset] = self.write(asset, data, 10*factor)
            manifest.append({'item_id': 'scene', 'asset': asset,
                             'raster_bands': [{'scale': .0001, 'offset': -.01}]})
        scl = np.full((48, 48), 4, dtype='uint8')
        scl[:24, 24:] = 9
        scl[24:, :24] = 3
        scl[0, 0] = 0
        self.paths['scl'] = self.write('scl', scl, 20)
        self.download = {'result': types.SimpleNamespace(paths={'scene': self.paths}, manifest=manifest)}
        self.model = self.make_model()

    def write(self, name, data, resolution):
        path = self.root / (name + '.tif')
        with rasterio.open(path, 'w', driver='GTiff', height=data.shape[0], width=data.shape[1],
                           count=1, dtype=data.dtype, nodata=0, crs='EPSG:32618',
                           transform=from_origin(500000, 1000000, resolution, resolution)) as dest:
            dest.write(data, 1)
        return path

    @staticmethod
    def make_model():
        class Model:
            bands = list(comparison.L2A_ASSETS)
            calls = 0
            max_value = 0
            def predict(self, values):
                self.calls += 1
                self.max_value = max(self.max_value, float(values.max()))
                result = np.zeros(values.shape[1:], dtype='uint8')
                result[values[self.bands.index('B02')] > .5] = 1
                result[values[self.bands.index('B04')] > .5] = 3
                return result
        return Model()

    def process(self, **kwargs):
        return comparison.process_downloaded_scenes(self.download, self.root/'out', model=self.model,
                                                    block_size=32, halo=16, **kwargs)

    def test_scl_classes_separate_clouds_cirrus_shadows_and_nodata(self):
        values = np.arange(12, dtype='uint8')[None]
        expected = np.array([[255, 255, 0, 3, 0, 0, 0, 0, 1, 1, 2, 0]], dtype='uint8')
        np.testing.assert_array_equal(comparison.scl_cloud_classes(values), expected)

    def test_native_blockwise_prediction_calibration_alignment_and_nodata(self):
        with patch.object(comparison, 'plot_comparison') as plot:
            output = self.process(debug=False)
        plot.assert_not_called()
        record = output['scenes'][0]
        stages = {row['method']: row for row in output['resources']}
        self.assertTrue({'Preparación compartida', 'SCL', 'CloudSEN12'} <= stages.keys())
        self.assertEqual(stages['SCL']['device'], 'cpu')
        self.assertIsNone(stages['CloudSEN12']['gpu_memory_peak_mb'])
        self.assertGreater(stages['SCL']['ram_peak_mb'], 0)
        report = json.loads(Path(output['resources_path']).read_text())
        self.assertEqual(report['status'], 'complete')
        self.assertTrue(Path(output['comparison_path']).is_file())
        self.assertEqual(record['shape'], [96, 96])
        self.assertGreater(self.model.calls, 1)
        self.assertLess(self.model.max_value, 1)
        with rasterio.open(record['model_path']) as src:
            values = src.read(1)
            self.assertEqual(src.res, (10, 10))
            self.assertEqual(src.nodata, 255)
            self.assertEqual(values[20, 70], 1)
            self.assertEqual(values[70, 20], 3)
            self.assertEqual(values[70, 70], 0)
            self.assertEqual(values[0, 0], 255)
        with rasterio.open(record['scl_path']) as src:
            self.assertEqual(src.read(1)[20, 70], 1)
        self.assertTrue((self.root/'out/scene/cloud_masks.json').is_file())

    def test_repeat_uses_complete_cached_masks(self):
        self.process()
        calls = self.model.calls
        output = self.process()
        self.assertEqual(self.model.calls, calls)
        self.assertEqual(output['cached_scenes'], 1)
        self.assertEqual(output['processed_scenes'], 0)
        self.assertEqual(output['resources'], [])
        report = json.loads(Path(output['resources_path']).read_text())
        self.assertTrue(report['notes'])

    def test_missing_bands_fail_before_loading_weights(self):
        del self.download['result'].paths['scene']['coastal']
        with patch.object(comparison, '_load_model') as load:
            with self.assertRaisesRegex(ValueError, 'faltan'):
                comparison.process_downloaded_scenes(self.download, self.root/'out')
            load.assert_not_called()

    def test_invalid_model_output_does_not_publish_partial_masks(self):
        self.model.predict = lambda values: np.full(values.shape[1:], 9)
        with self.assertRaisesRegex(ValueError, 'clases'):
            self.process()
        self.assertFalse((self.root/'out/scene/cloud_masks.json').exists())
        self.assertEqual(list((self.root/'out/scene').glob('*.partial.tif')), [])
        report = json.loads((self.root/'out/resource_usage.json').read_text())
        self.assertEqual(report['status'], 'failed')

    def test_rejects_l1c_model(self):
        self.model.bands = [*comparison.L2A_ASSETS, 'B10']
        with self.assertRaisesRegex(ValueError, 'L1C'):
            self.process()

    def test_debug_grid_has_three_rows_ten_columns_and_saves_png(self):
        import matplotlib.pyplot as plt
        output = self.process()
        path = self.root/'comparison.png'
        with patch.object(plt, 'show'):
            figure = comparison.plot_comparison(output['scenes'], path)
        self.addCleanup(plt.close, figure)
        self.assertEqual(len(figure.axes), 30)
        self.assertTrue(path.is_file())
        self.assertEqual(len(figure.axes[0].images), 1)
        self.assertEqual(len(figure.axes[10].images), 1)
        self.assertEqual(len(figure.axes[20].images), 1)
        self.assertEqual(len(figure.axes[1].images), 0)  # no duplicate scenes in empty columns

    def test_debug_flag_calls_the_grid_only_when_enabled(self):
        with patch.object(comparison, 'plot_comparison') as plot:
            output = self.process(debug=True)
        plot.assert_called_once()
        self.assertTrue(output['figure_path'].endswith('cloud_mask_comparison_3x10.png'))

    def test_processing_uses_the_selected_region_geometry(self):
        from rasterio.warp import transform_geom
        triangle = {'type': 'Polygon', 'coordinates': [[
            [500200, 999800], [500800, 999800], [500200, 999200], [500200, 999800]]]
        }
        region = self.root/'region.geojson'
        region.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {}, 'geometry': transform_geom('EPSG:32618', 'EPSG:4326', triangle)}]}))
        output = self.process(region_path=region)
        with rasterio.open(output['scenes'][0]['model_path']) as src:
            self.assertLess(src.width, 96)
            self.assertLess(src.height, 96)
            self.assertTrue(np.any(src.read(1) == 255))
            self.assertTrue(np.any(src.read(1) != 255))

    def test_gpu_can_be_disabled_even_when_cuda_is_available(self):
        torch = types.ModuleType('torch')
        torch.cuda = MagicMock()
        torch.cuda.is_available.return_value = True
        torch.device = lambda name: name
        package = types.ModuleType('cloudsen12_models')
        package.cloudsen12 = types.SimpleNamespace(load_model_by_name=MagicMock())
        with patch.dict(sys.modules, {'torch': torch, 'cloudsen12_models': package}):
            comparison._load_model(self.root/'weights', use_gpu=False)
            self.assertEqual(package.cloudsen12.load_model_by_name.call_args.kwargs['device'], 'cpu')
            torch.cuda.is_available.assert_not_called()
            comparison._load_model(self.root/'weights', use_gpu=True)
            self.assertEqual(package.cloudsen12.load_model_by_name.call_args.kwargs['device'], 'cuda')


if __name__ == '__main__':
    unittest.main()
