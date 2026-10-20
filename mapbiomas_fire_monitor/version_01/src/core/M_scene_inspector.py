"""Inspect unmasked GEE L2A scenes and download only explicitly selected acquisitions."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json


def _tile(value):
    return str(value).upper().removeprefix('T')


def _product(value):
    return str(value or '').removesuffix('.SAFE')


def match_selected_scenes(scenes, items):
    """Match GEE to STAC by tile/product, falling back to acquisition time (2 s)."""
    from Sentinel2_pipeline_test import _item_tile, _item_datetime
    matched, missing = [], []
    for scene in scenes:
        candidates = [item for item in items if _tile(_item_tile(item)) == _tile(scene['tile'])]
        candidates.sort(key=lambda item: (
            str(item.properties.get('s2:generation_time') or item.properties.get('updated') or ''), item.id
        ), reverse=True)
        product = _product(scene.get('product_id'))
        exact = [item for item in candidates if product and product in {
            _product(item.properties.get('s2:product_uri')), _product(item.properties.get('s2:product_id'))}]
        if not exact:
            target = datetime.fromtimestamp(scene['time'] / 1000, timezone.utc)
            exact = [item for item in candidates if abs((datetime.fromisoformat(
                _item_datetime(item).replace('Z', '+00:00')) - target).total_seconds()) <= 2]
        if not exact:
            missing.append(scene['id'])
        else:
            # Prefer the most recent processing when a provider has duplicate acquisitions.
            matched.append(exact[0])
    if missing:
        raise ValueError('Escenas seleccionadas no disponibles en esta fuente; pruebe la otra: ' + ', '.join(missing))
    return list({item.id: item for item in matched}.values())


class SceneInspectionSession:
    def __init__(self, workflow, parameters):
        self.workflow = workflow
        self.parameters = json.loads(json.dumps(parameters))
        self.scenes = []
        self.geometry = None
        self.collection = None
        self.result = None

    def inspect(self):
        import ee
        p = self.parameters
        self.scenes = []
        self.result = None
        if date.fromisoformat(p['start_date']) >= date.fromisoformat(p['end_date']):
            raise ValueError('La fecha final exclusiva debe ser posterior a la inicial.')
        regions = ee.FeatureCollection(p['regions_asset']).filter(ee.Filter.eq('id_monitor', p['region_id']))
        self.geometry = regions.geometry()
        self.collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                           .filterBounds(self.geometry).filterDate(p['start_date'], p['end_date'])
                           .sort('system:index'))
        def describe(value):
            image = ee.Image(value)
            return ee.Dictionary({'id': image.id(), 'time': image.get('system:time_start'),
                                  'tile': image.get('MGRS_TILE'), 'cloud': image.get('CLOUDY_PIXEL_PERCENTAGE'),
                                  'product_id': image.get('PRODUCT_ID')})
        info = ee.Dictionary({'region_count': regions.size(), 'scenes': self.collection.toList(
            self.collection.size()).map(describe)}).getInfo()
        if info['region_count'] != 1:
            raise ValueError('El asset debe contener exactamente una región con ese id_monitor.')
        self.scenes = info['scenes']
        return self.scenes

    def scene_image(self, scene_id):
        import ee
        if scene_id not in {row['id'] for row in self.scenes}:
            raise ValueError('Seleccione una escena de la búsqueda actual.')
        # No SCL, Cloud Score, positive-value mask, or temporal composition.
        return ee.Image(scene_id).select(['B11', 'B8', 'B4']).multiply(0.0001).clip(self.geometry)

    def download(self, selected_ids, source):
        from Sentinel2_pipeline_test import (PipelineConfig, select_region,
                                            search_scenes, download_scenes)
        self.result = None
        ids = list(dict.fromkeys(selected_ids))
        if not ids:
            raise ValueError('Seleccione al menos una imagen para descargar.')
        by_id = {row['id']: row for row in self.scenes}
        if any(scene_id not in by_id for scene_id in ids):
            raise ValueError('La selección no pertenece a la búsqueda actual.')
        self.workflow.load_gee_vectors(self.parameters)
        params = self.parameters
        config = PipelineConfig(
            shapefile=self.workflow.tiles_path, regions_zip=self.workflow.regions_path,
            region_id=params['region_id'], start_date=params['start_date'],
            end_date=(date.fromisoformat(params['end_date']) - timedelta(days=1)).isoformat(),
            bands=('red', 'nir', 'swir16', 'swir22'), download_images=True,
            data_dir=self.workflow.results_dir / 'selected_scenes' / source / f"{params['start_date']}_{params['end_date']}",
            source=source)
        region = select_region(config)
        available = search_scenes(config, region.region, filter_cloud_cover=False)
        selected = match_selected_scenes([by_id[scene_id] for scene_id in ids], available.items)
        self.result = download_scenes(config, selected)
        # Save acquisition/provider correspondence without signed asset URLs.
        manifest = {'source': source, 'gee_scene_ids': ids, 'stac_item_ids': [item.id for item in selected],
                    'region_id': params['region_id'], 'start_date': params['start_date'],
                    'end_date': params['end_date'], 'bands': list(config.bands)}
        (config.output_dir / 'inspection_selection.json').write_text(json.dumps(manifest, indent=2))
        return self.result


class SceneInspectorUI:
    def __init__(self, workflow, parameters):
        import ipywidgets as widgets
        self.session = SceneInspectionSession(workflow, parameters)
        self.map = None
        self.scene_layer = None
        self.view = widgets.Dropdown(options=[], description='Visualizar:', layout=widgets.Layout(width='100%'))
        self.selection = widgets.SelectMultiple(options=[], description='Descargar:', rows=9,
                                                layout=widgets.Layout(width='100%'))
        self.source = widgets.Dropdown(options=['planetary-computer', 'earth-search'], description='Fuente:')
        self.inspect_button = widgets.Button(description='Listar imágenes', icon='search', button_style='info')
        self.download_button = widgets.Button(description='Descargar seleccionadas', icon='download',
                                               button_style='success', disabled=True)
        self.messages = widgets.Output()
        self.map_output = widgets.Output()
        self.panel = widgets.VBox([
            widgets.HTML('<h3 style="color:#185c44;margin:0">Imágenes L2A por región</h3>'
                         '<p>Vista SWIR1 / NIR / rojo, sin máscara adicional ni filtro de nubosidad. '
                         'Cambie la imagen visible y seleccione las escenas que desea descargar.</p>'),
            self.inspect_button, self.view, self.map_output, self.selection, self.source,
            self.download_button, self.messages,
        ], layout=widgets.Layout(border='1px solid #cbded5', padding='18px', width='100%'))
        self.inspect_button.on_click(self._inspect)
        self.view.observe(self._view, names='value')
        self.selection.observe(lambda _: self._update_download(), names='value')
        self.download_button.on_click(self._download)

    def _update_download(self):
        self.download_button.disabled = not bool(self.selection.value)

    def _inspect(self, _=None):
        import geemap
        from IPython.display import display
        self.inspect_button.disabled = True
        self.download_button.disabled = True
        self.selection.options = []
        self.view.options = []
        self.messages.clear_output()
        self.map_output.clear_output()
        self.map = None
        self.scene_layer = None
        try:
            with self.messages:
                scenes = self.session.inspect()
                print(f'{len(scenes)} imágenes disponibles. Elija cuáles descargar; la vista no descarga archivos.')
            if not scenes:
                return
            self.map = geemap.Map(ee_initialize=False)
            self.map.add_basemap('Esri.WorldImagery')
            self.map.centerObject(self.session.geometry, 8)
            options = []
            for row in scenes:
                time = datetime.fromtimestamp(row['time'] / 1000, timezone.utc).isoformat()
                cloud = row.get('cloud')
                label = f"{time} · {row['tile']} · nubes {cloud}% · {row['id'].split('/')[-1]}"
                options.append((label, row['id']))
            self.selection.options = options
            self.selection.value = ()
            self.view.options = options
            with self.map_output:
                display(self.map)
        except Exception as exc:
            with self.messages:
                print(f'No se pudo listar las imágenes: {exc}')
        finally:
            self.inspect_button.disabled = False
            self._update_download()

    def _view(self, change):
        if self.map is None or not change['new']:
            return
        try:
            if self.scene_layer is not None:
                self.map.remove_layer(self.scene_layer)
                self.scene_layer = None
            self.map.addLayer(self.session.scene_image(change['new']),
                              {'bands': ['B11', 'B8', 'B4'], 'min': .03, 'max': .4},
                              change['new'].split('/')[-1])
            self.scene_layer = self.map.layers[-1]
        except Exception as exc:
            with self.messages:
                print(f'No se pudo visualizar la escena: {exc}')

    def _download(self, _=None):
        controls = (self.inspect_button, self.download_button, self.view, self.selection, self.source)
        for control in controls:
            control.disabled = True
        self.messages.clear_output()
        try:
            with self.messages:
                result = self.session.download(self.selection.value, self.source.value)
                print(f'Escenas descargadas: {len(result.paths)}. Archivos: {len(result.manifest)}.')
                for scene_id, paths in result.paths.items():
                    print(scene_id, '→', str(Path(next(iter(paths.values()))).parent))
        except Exception as exc:
            with self.messages:
                print(f'No se pudo completar la descarga: {exc}')
        finally:
            for control in controls:
                control.disabled = False
            self._update_download()

    def show(self):
        from IPython.display import display
        display(self.panel)
        return self


def run_ui(workflow, parameters):
    return SceneInspectorUI(workflow, parameters).show()
