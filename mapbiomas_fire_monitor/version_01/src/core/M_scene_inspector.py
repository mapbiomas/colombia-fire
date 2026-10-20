"""Inspect unmasked GEE L2A scenes and download only explicitly selected acquisitions."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json

# The L2A CloudSEN12 model needs all 12 spectral bands; SCL is the comparison mask.
DOWNLOAD_BANDS = ("coastal", "blue", "green", "red", "rededge1", "rededge2", "rededge3",
                  "nir", "nir08", "nir09", "swir16", "swir22", "scl")


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
            asset_id = ee.String('COPERNICUS/S2_SR_HARMONIZED/').cat(ee.String(image.get('system:index')))
            return ee.Dictionary({'id': asset_id, 'time': image.get('system:time_start'),
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
            bands=DOWNLOAD_BANDS, download_images=True,
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
        self.download_checks = {}
        self.layer_status = {}
        self.source = widgets.Dropdown(options=['planetary-computer', 'earth-search'], description='Fuente:')
        self.inspect_button = widgets.Button(description='Listar imágenes', icon='search', button_style='info')
        self.download_button = widgets.Button(description='Descargar marcadas', icon='download',
                                               button_style='success', disabled=True)
        self.select_all_button = widgets.Button(description='Marcar todas', icon='check-square-o', disabled=True)
        self.select_none_button = widgets.Button(description='Desmarcar todas', icon='square-o', disabled=True)
        self.messages = widgets.Output()
        self.map_container = widgets.VBox(layout=widgets.Layout(width='100%'))
        self.table = widgets.VBox(layout=widgets.Layout(max_height='420px', overflow='auto', width='100%'))
        self.selection_summary = widgets.HTML()
        self.panel = widgets.VBox([
            widgets.HTML('<h3 style="color:#185c44;margin:0">Imágenes L2A por región</h3>'
                         '<p>Todas las imágenes quedan marcadas para descargar. Desmarque las que no necesita. '
                         'Las capas del mapa comienzan apagadas; use el control de capas para activarlas. '
                         'Vista SWIR1 / NIR / rojo, sin máscara adicional ni filtro de nubosidad.</p>'),
            self.inspect_button, self.map_container,
            widgets.HBox([self.select_all_button, self.select_none_button]),
            self.table, self.selection_summary, self.source, self.download_button, self.messages,
        ], layout=widgets.Layout(border='1px solid #cbded5', padding='18px', width='100%'))
        self.inspect_button.on_click(self._inspect)
        self.select_all_button.on_click(lambda _: self._select_all(True))
        self.select_none_button.on_click(lambda _: self._select_all(False))
        self.download_button.on_click(self._download)

    @property
    def selected_ids(self):
        return [scene_id for scene_id, check in self.download_checks.items() if check.value]

    def _update_download(self):
        selected = len(self.selected_ids)
        self.download_button.disabled = not bool(selected)
        self.selection_summary.value = f'<p><b>{selected}/{len(self.download_checks)}</b> imágenes marcadas para descargar.</p>'

    def _select_all(self, selected):
        for check in self.download_checks.values():
            check.value = selected
        self._update_download()

    def _build_table(self, scenes):
        import ipywidgets as widgets
        from html import escape
        widths = ('100px', '185px', '85px', '90px', '350px', '100px')
        def text_cell(text, width):
            return widgets.HTML(escape(str(text)), layout=widgets.Layout(width=width, flex='0 0 auto'))
        rows = [widgets.HBox([text_cell(label, width) for label, width in zip(
            ('Descargar', 'Adquisición (UTC)', 'Tesela', 'Nubes (%)', 'Imagen GEE', 'Capa'), widths)])]
        self.download_checks = {}
        self.layer_status = {}
        for scene in scenes:
            check = widgets.Checkbox(value=True, indent=False,
                                     layout=widgets.Layout(width=widths[0], flex='0 0 auto'))
            check.observe(lambda _: self._update_download(), names='value')
            self.download_checks[scene['id']] = check
            timestamp = datetime.fromtimestamp(scene['time'] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            status = text_cell('Preparando…', widths[-1])
            self.layer_status[scene['id']] = status
            values = (timestamp, scene['tile'], scene.get('cloud', '—'), scene['id'].split('/')[-1])
            cells = [check] + [text_cell(value, width) for value, width in zip(values, widths[1:-1])] + [status]
            rows.append(widgets.HBox(cells, layout=widgets.Layout(border_bottom='1px solid #ddd')))
        self.table.children = tuple(rows)
        self.select_all_button.disabled = self.select_none_button.disabled = not bool(scenes)
        self._update_download()

    def _add_scene_layers(self, scenes):
        failed = []
        for scene in scenes:
            try:
                self.map.addLayer(self.session.scene_image(scene['id']),
                                  {'bands': ['B11', 'B8', 'B4'], 'min': .03, 'max': .4},
                                  scene['id'].split('/')[-1], shown=False)
                self.layer_status[scene['id']].value = 'Lista (apagada)'
            except Exception as exc:
                self.layer_status[scene['id']].value = 'Error de vista'
                failed.append((scene['id'], str(exc)))
        return failed

    def _inspect(self, _=None):
        import geemap
        import ipywidgets as widgets
        self.inspect_button.disabled = True
        self.download_button.disabled = True
        self.download_checks = {}
        self.layer_status = {}
        self.select_all_button.disabled = self.select_none_button.disabled = True
        self.table.children = ()
        self.map_container.children = ()
        self.selection_summary.value = ''
        self.messages.clear_output()
        self.map = None
        try:
            with self.messages:
                scenes = self.session.inspect()
                print(f'{len(scenes)} imágenes disponibles. Preparando las capas…')
            self._build_table(scenes)
            if not scenes:
                return
            # Embed the ipyleaflet widget directly, avoiding nested output capture in Colab.
            self.map = geemap.Map(ee_initialize=False, layout=widgets.Layout(height='600px', width='100%'))
            self.map_container.children = (self.map,)
            self.map.add_basemap('Esri.WorldImagery')
            self.map.centerObject(self.session.geometry, 8)
            self.map.add_layer_control()
            failed = self._add_scene_layers(scenes)
            with self.messages:
                print(f'{len(scenes) - len(failed)}/{len(scenes)} capas listas, apagadas por defecto. '
                      'Actívelas desde el control de capas del mapa.')
                for scene_id, error in failed:
                    print(f'Error de vista en {scene_id}: {error}')
        except Exception as exc:
            with self.messages:
                print(f'No se pudo completar la inspección: {exc}')
        finally:
            self.inspect_button.disabled = False
            self._update_download()

    def _download(self, _=None):
        selected = self.selected_ids
        controls = (self.inspect_button, self.download_button, self.source,
                    self.select_all_button, self.select_none_button, *self.download_checks.values())
        for control in controls:
            control.disabled = True
        self.messages.clear_output()
        try:
            with self.messages:
                result = self.session.download(selected, self.source.value)
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


def filter_blocklisted_scenes(scenes, blocked_ids):
    """Exclude GEE IDs and reprocessed versions of the same acquisition/tile."""
    import re
    pattern = re.compile(r'^(\d{8}T\d{6})_\d{8}T\d{6}_T([A-Z0-9]{5})$')
    blocked = {str(value).split('/')[-1] for value in blocked_ids}
    acquisitions = {}
    for value in blocked:
        match = pattern.fullmatch(value)
        if match:
            acquisitions[(match[1], match[2])] = value
    allowed, excluded = [], []
    for scene in scenes:
        index = scene['id'].split('/')[-1]
        match = pattern.fullmatch(index)
        reason = index if index in blocked else acquisitions.get((match[1], match[2])) if match else None
        if reason:
            excluded.append({'id': scene['id'], 'blocklist_id': reason})
        else:
            allowed.append(scene)
    return allowed, excluded


def copy_debug_file(source, destination):
    """Atomically copy a debug artifact, reusing an unchanged complete copy."""
    import shutil
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and (source.stat().st_size, source.stat().st_mtime_ns) == (
            destination.stat().st_size, destination.stat().st_mtime_ns):
        return destination
    temporary = destination.with_name(destination.name + '.partial')
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def archive_download_result(result, drive_dir):
    """Preserve each scene's native bands and calibration metadata in Drive."""
    drive_dir = Path(drive_dir)
    archived = {}
    for scene_id, paths in result.paths.items():
        if Path(scene_id).name != scene_id or scene_id in ('', '.', '..'):
            raise ValueError('Identificador de escena inválido para el archivo debug.')
        print(f'Guardando escena en Drive: {scene_id}')
        archived[scene_id] = {asset: str(copy_debug_file(path, drive_dir/'scenes'/scene_id/f'{asset}.tif'))
                              for asset, path in paths.items()}
    manifest = [{key: value for key, value in record.items() if key != 'source_url'} for record in result.manifest]
    for record in manifest:
        record['path'] = f"scenes/{record['item_id']}/{record['asset']}.tif"
    drive_dir.mkdir(parents=True, exist_ok=True)
    (drive_dir/'download_manifest.json').write_text(json.dumps(manifest, indent=2))
    return archived


def download_month(workflow, parameters, *, source='planetary-computer', test_mode=False,
                   max_scenes=10, debug_drive_dir=None):
    """Download a month; debug limits the scene count and optionally archives native files."""
    import M_blocklist as blocklists
    from Sentinel2_pipeline_test import PipelineConfig, select_region, search_scenes, download_scenes

    if test_mode and (not isinstance(max_scenes, int) or isinstance(max_scenes, bool) or max_scenes < 1):
        raise ValueError('El número de escenas debug debe ser un entero mayor que cero.')
    print('Consultando blocklist Sentinel-2…')
    blocked_ids = blocklists.refresh_blocklists(['sentinel2'])['sentinel2']
    report = blocklists.blocklist_report(['sentinel2'])['sentinel2']
    sources = report.get('sources', [])
    for entry in sources:
        print(f"Blocklist {entry['asset']}: {entry['status']} ({entry.get('count', 0)} IDs)")
    if not sources or any(entry.get('status') != 'ok' for entry in sources):
        raise RuntimeError('No se pudo verificar toda la blocklist. No se iniciará la descarga. '
                           'Revise el acceso a las fuentes GEE de la lista.')

    # Query scene metadata only: no geemap layers, map tiles, or pixel masks.
    session = SceneInspectionSession(workflow, parameters)
    scenes = session.inspect()
    allowed, excluded = filter_blocklisted_scenes(scenes, blocked_ids)
    print(f'Escenas disponibles: {len(scenes)}. Excluidas por blocklist: {len(excluded)}.')
    for row in excluded:
        print(f"BLOCKLIST — {row['id']} (entrada: {row['blocklist_id']})")
    summary = {'source': source, 'test_mode': bool(test_mode), 'excluded': excluded,
               'unavailable': [], 'selected': [], 'result': None,
               'max_scenes': max_scenes if test_mode else None,
               'debug_drive_dir': str(debug_drive_dir) if test_mode and debug_drive_dir else None}
    if not allowed:
        print('No hay escenas permitidas para descargar.')
        return summary

    workflow.load_gee_vectors(parameters)
    config = PipelineConfig(
        shapefile=workflow.tiles_path, regions_zip=workflow.regions_path,
        region_id=parameters['region_id'], start_date=parameters['start_date'],
        end_date=(date.fromisoformat(parameters['end_date']) - timedelta(days=1)).isoformat(),
        bands=DOWNLOAD_BANDS, download_images=True,
        data_dir=workflow.results_dir / 'monthly_scenes' / source / f"{parameters['start_date']}_{parameters['end_date']}",
        source=source)
    region = select_region(config)
    available = search_scenes(config, region.region, filter_cloud_cover=False)
    chosen, seen = [], set()
    # Stable chronological order makes the limited debug download repeatable.
    for scene in sorted(allowed, key=lambda row: (row['time'], row['id'])):
        try:
            item, = match_selected_scenes([scene], available.items)
        except ValueError:
            summary['unavailable'].append(scene['id'])
            print(f"No disponible en {source}: {scene['id']}")
            continue
        if item.id in seen:
            continue
        seen.add(item.id)
        chosen.append(item)
        summary['selected'].append({'gee_id': scene['id'], 'stac_id': item.id})
        if test_mode and len(chosen) == max_scenes:
            break
    mode = f'ACTIVADO (máximo {max_scenes} escenas)' if test_mode else 'DESACTIVADO (todas las permitidas)'
    print(f"Debug: {mode}. "
          f'Escenas a descargar: {len(chosen)}.')
    config.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {key: value for key, value in summary.items() if key != 'result'}
    manifest.update(region_id=parameters['region_id'], start_date=parameters['start_date'],
                    end_date=parameters['end_date'], bands=list(config.bands))
    (config.output_dir / 'monthly_download_selection.json').write_text(json.dumps(manifest, indent=2))
    if chosen:
        summary['result'] = download_scenes(config, chosen)
        if summary['debug_drive_dir']:
            summary['drive_paths'] = archive_download_result(summary['result'], debug_drive_dir)
            copy_debug_file(config.output_dir/'monthly_download_selection.json',
                            Path(debug_drive_dir)/'monthly_download_selection.json')
            print(f'Escenas debug guardadas en Drive: {debug_drive_dir}')
        print(f'Descarga terminada. Directorio: {config.output_dir}')
    else:
        print('Ninguna escena permitida está disponible en la fuente elegida.')
    return summary


def region_menu_options(regions_asset):
    """Fetch region IDs/names only, without downloading GEE geometries."""
    import ee
    regions = ee.FeatureCollection(regions_asset)
    def describe(value):
        feature = ee.Feature(value)
        return ee.Dictionary({'id': feature.get('id_monitor'), 'name': feature.get('nombre'),
                              'fallback': feature.get('LEVEL_3')})
    rows = regions.toList(regions.size()).map(describe).getInfo()
    if not rows or any(row.get('id') is None for row in rows):
        raise ValueError('El asset de regiones debe tener un id_monitor por región.')
    if len({str(row['id']) for row in rows}) != len(rows):
        raise ValueError('El asset contiene id_monitor duplicados.')
    rows.sort(key=lambda row: (0, int(row['id'])) if str(row['id']).isdigit() else (1, str(row['id'])))
    return [(f"{row['id']} · {row.get('name') or row.get('fallback') or 'Región'}", row['id']) for row in rows]


class MonthlyDownloadUI:
    """One menu cell; only the explicit Download click starts scene transfers."""

    def __init__(self, workflow, regions_asset, tiles_asset):
        import ipywidgets as widgets
        self.workflow = workflow
        self.regions_asset, self.tiles_asset = regions_asset, tiles_asset
        self.result = None
        self.download_parameters = None
        self.download_source = None
        self.download_region_path = None
        options = region_menu_options(regions_asset)
        selected = next((value for _, value in options if str(value) == '7'), options[0][1])
        self.region = widgets.Dropdown(options=options, value=selected, description='Región:',
                                       layout=widgets.Layout(width='100%'))
        self.year = widgets.Dropdown(options=list(range(date.today().year, 2016, -1)),
                                     value=date.today().year, description='Año:')
        months = ('Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre')
        self.month = widgets.Dropdown(options=[(name, i) for i, name in enumerate(months, 1)],
                                      value=1, description='Mes:')
        self.source = widgets.Dropdown(options=[('Planetary Computer', 'planetary-computer'),
                                                ('Earth Search', 'earth-search')],
                                       value='planetary-computer', description='Fuente:')
        self.test_mode = widgets.Checkbox(value=True, description='Debug: limitar escenas y guardar en Drive', indent=False)
        self.scene_limit = widgets.IntText(value=10, description='N.º escenas:')
        self.download_button = widgets.Button(description='Descargar', icon='download', button_style='success')
        self.output = widgets.Output()
        self.status = widgets.HTML('Seleccione región, año, mes y fuente. Pulse Descargar para empezar.')
        self.panel = widgets.VBox([
            widgets.HTML('<h3 style="color:#185c44;margin:0">Descargar escenas del mes</h3>'),
            self.region, widgets.HBox([self.year, self.month, self.source],
                                      layout=widgets.Layout(flex_flow='row wrap')),
            widgets.HBox([self.test_mode, self.scene_limit], layout=widgets.Layout(flex_flow='row wrap')),
            self.download_button, self.status, self.output,
        ], layout=widgets.Layout(border='1px solid #cbded5', padding='18px', width='100%'))
        self.download_button.on_click(self._download)
        self.test_mode.observe(lambda _: self._debug_changed(), names='value')

    def _debug_changed(self):
        self.scene_limit.disabled = not self.test_mode.value

    def parameters(self):
        year, month = self.year.value, self.month.value
        return {'regions_asset': self.regions_asset, 'tiles_asset': self.tiles_asset,
                'region_id': self.region.value, 'start_date': date(year, month, 1).isoformat(),
                'end_date': date(year + (month == 12), 1 if month == 12 else month + 1, 1).isoformat()}

    def _download(self, _=None):
        controls = (self.region, self.year, self.month, self.source, self.test_mode,
                    self.scene_limit, self.download_button)
        parameters = self.parameters()
        source, test_mode = self.source.value, self.test_mode.value
        self.result = self.download_parameters = self.download_source = self.download_region_path = None
        self.output.clear_output()
        for control in controls:
            control.disabled = True
        self.status.value = 'Consultando blocklist y descargando escenas…'
        try:
            with self.output:
                drive_dir = None
                if test_mode:
                    if self.scene_limit.value < 1:
                        raise ValueError('El número de escenas debug debe ser mayor que cero.')
                    if getattr(self.workflow, 'archive_dir', None) is None:
                        self.workflow.use_drive('/content/drive/MyDrive/FuegoMonitor')
                    drive_dir = (Path(self.workflow.archive_dir)/'debug_scenes'/source/
                                 f"region_{parameters['region_id']}"/parameters['start_date'][:7])
                result = download_month(self.workflow, parameters, source=source, test_mode=test_mode,
                                        max_scenes=self.scene_limit.value, debug_drive_dir=drive_dir)
            self.result = result
            self.download_parameters = parameters
            self.download_source = source
            self.download_region_path = Path(self.workflow.regions_path)
            count = len(result['result'].paths) if result.get('result') is not None else 0
            self.status.value = f'Descarga terminada: {count} escenas. Puede ejecutar la siguiente celda de procesamiento.' if count else 'Sin escenas descargadas para esta selección.'
        except Exception as exc:
            self.status.value = 'No se pudo completar la descarga. Revise el mensaje de abajo.'
            with self.output:
                print(str(exc))
        finally:
            for control in controls:
                control.disabled = False
            self._debug_changed()

    def show(self):
        from IPython.display import display
        display(self.panel)
        return self


def run_monthly_download_ui(workflow, *, regions_asset, tiles_asset):
    return MonthlyDownloadUI(workflow, regions_asset, tiles_asset).show()
