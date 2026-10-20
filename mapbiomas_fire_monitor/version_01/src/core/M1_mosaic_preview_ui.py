"""Monthly Sentinel-2 preview and exports using the shared M1 mosaic engine."""

from datetime import date
from html import escape
import json

BANDS = ('blue', 'green', 'red', 'nir', 'swir1', 'swir2', 'dayOfYear')
MONTHS = ('Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
          'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre')


def month_dates(year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start.isoformat(), end.isoformat()


def _config_signature():
    import M0_auth_config as config
    return json.dumps([config.CONFIG, config.GLOBAL_OPTS], sort_keys=True)


class MonthlyMosaicSession:
    """Keep the preview image and task receipts together; exports never rebuild it."""

    def __init__(self):
        self.prepared = None
        self.tasks = []

    def prepare(self, year, month, method):
        import M0_auth_config as config
        from M1_export_logic import get_quality_mosaic
        self.prepared = None
        if method not in config.CONFIG['mosaic_methods']:
            raise ValueError('Elija un método configurado en la primera celda.')
        start, end = month_dates(year, month)
        if date.fromisoformat(end) > date.today():
            raise ValueError('Seleccione un mes completo anterior al mes actual.')
        geometry = config.get_country_geometry()
        image = get_quality_mosaic('sentinel2', year, start, end, geometry,
                                   month=month, method=method)
        # Evaluate before enabling exports, including Cloud Score / buffer errors.
        names = image.bandNames().getInfo()
        if not set(BANDS).issubset(names):
            raise ValueError('El mosaico no contiene las siete bandas esperadas.')
        if not image.select('red').getMapId({'min': 0, 'max': 40}):
            raise ValueError('No se pudo preparar la vista del mosaico.')
        self.prepared = dict(year=year, month=month, method=method, image=image,
                             geometry=geometry, signature=_config_signature())
        return self.prepared

    def export(self, bands, destinations):
        import M0_auth_config as config
        import M1_export_logic as logic
        if self.prepared is None:
            raise ValueError('Prepare y visualice el mosaico antes de exportar.')
        if self.prepared['signature'] != _config_signature():
            raise ValueError('La configuración cambió. Vuelva a preparar el mosaico.')
        bands, destinations = tuple(dict.fromkeys(bands)), tuple(dict.fromkeys(destinations))
        if not bands or not set(bands).issubset(BANDS):
            raise ValueError('Seleccione al menos una banda válida.')
        if not destinations or not set(destinations).issubset(('ASSET', 'GCS')):
            raise ValueError('Seleccione GEE Assets, GCS o ambos.')
        p = self.prepared
        errors = []
        for destination in destinations:
            for band in bands:
                name = config.mosaic_name(p['year'], p['month'], 'monthly', band=band,
                                          mosaic=p['method'], sensor='sentinel2')
                description = f"{config.GLOBAL_OPTS['PERSONAL_TASK_FLAG']}_{destination}_{name}"
                # Avoid resubmitting tasks already queued/running/completed in this session.
                if any(row['description'] == description and
                       row['task'].status().get('state') not in ('FAILED', 'CANCELLED')
                       for row in self.tasks):
                    continue
                try:
                    if destination == 'ASSET':
                        task = logic.export_to_asset(
                            p['image'], name, p['year'], p['month'], band=band,
                            mosaic=p['method'], sensor='sentinel2', overwrite=False)
                    else:
                        task, = logic.export_to_gcs(
                            p['image'], name, p['year'], p['month'], bands=[band],
                            mosaic=p['method'], sensor='sentinel2')
                    self.tasks.append(dict(task=task, description=description,
                                           destination=destination, band=band))
                except Exception as exc:
                    errors.append(f'{destination} / {band}: {exc}')
        return errors


class MonthlyMosaicUI:
    def __init__(self, years=None):
        import ipywidgets as widgets
        import M0_auth_config as config
        self.session = MonthlyMosaicSession()
        selected_years = config.resolve_years(years, sensor='sentinel2')
        methods = config.CONFIG['mosaic_methods']
        self.year = widgets.Dropdown(options=selected_years, description='Año:')
        self.month = widgets.Dropdown(options=[(m, i) for i, m in enumerate(MONTHS, 1)],
                                      description='Mes:')
        self.method = widgets.Dropdown(options=[(m.upper(), m) for m in methods], description='Mosaico:')
        self.bands = widgets.SelectMultiple(options=BANDS, value=BANDS, description='Bandas:', rows=7)
        self.asset = widgets.Checkbox(value=True, description='GEE Assets', indent=False)
        self.gcs = widgets.Checkbox(value=True, description='Google Cloud Storage', indent=False)
        self.preview_button = widgets.Button(description='Preparar y visualizar', icon='map', button_style='info')
        self.export_button = widgets.Button(description='Exportar mosaico', icon='cloud-upload',
                                             button_style='success', disabled=True)
        self.refresh_button = widgets.Button(description='Actualizar tareas', icon='refresh')
        self.summary = widgets.HTML()
        self.map_output = widgets.Output()
        self.messages = widgets.Output()
        self.task_table = widgets.HTML()
        self.panel = widgets.VBox([
            widgets.HTML('<h3 style="color:#185c44;margin:0">Mosaico mensual · Sentinel-2</h3>'
                         '<p>Seleccione un mes completo, visualice el mosaico y exporte las bandas elegidas.</p>'),
            widgets.HBox([self.year, self.month, self.method], layout=widgets.Layout(flex_flow='row wrap')),
            self.summary,
            widgets.HBox([self.bands, widgets.VBox([self.asset, self.gcs])]),
            widgets.HBox([self.preview_button, self.export_button, self.refresh_button],
                         layout=widgets.Layout(flex_flow='row wrap')),
            self.messages, self.map_output, self.task_table,
        ], layout=widgets.Layout(border='1px solid #cbded5', padding='18px', width='100%'))
        for control in (self.year, self.month, self.method):
            control.observe(self._selection_changed, names='value')
        self.preview_button.on_click(self._preview)
        self.export_button.on_click(self._export)
        self.refresh_button.on_click(self._refresh)
        self._selection_changed()

    def _selection_changed(self, change=None):
        import M0_auth_config as config
        self.session.prepared = None
        self.export_button.disabled = True
        self.map_output.clear_output()
        start, end = month_dates(self.year.value, self.month.value)
        asset_path = config.get_asset_mosaic_collection(
            sensor='sentinel2', period='monthly', mosaic=self.method.value)
        gcs_path = config.monthly_chunk_path(self.year.value, self.month.value,
                                            mosaic=self.method.value, sensor='sentinel2')
        self.summary.value = (
            f'<p><b>Colombia · {start} → {end} (fin exclusivo) · 10 m</b><br>'
            'Cloud Score Plus ≥ 0.40 · Reflectancia almacenada en byte (0–100) · '
            'Una tarea por banda y destino.<br>'
            f'GEE: <code>{escape(asset_path)}/&lt;banda&gt;</code><br>'
            f'GCS: <code>gs://{escape(config.CONFIG["bucket"])}/{escape(gcs_path)}</code></p>')

    def _busy(self, busy):
        for control in (self.year, self.month, self.method, self.bands, self.asset,
                        self.gcs, self.preview_button, self.refresh_button):
            control.disabled = busy
        self.export_button.disabled = busy or self.session.prepared is None

    def _preview(self, _=None):
        from IPython.display import display
        import geemap
        self._busy(True)
        self.messages.clear_output()
        self.map_output.clear_output()
        try:
            with self.messages:
                print('Preparando el mosaico con la lógica M1…')
            prepared = self.session.prepare(self.year.value, self.month.value, self.method.value)
            canvas = geemap.Map()
            canvas.addLayer(prepared['image'], {'bands': ['swir1', 'nir', 'red'], 'min': 0, 'max': 40},
                            f"{self.method.value.upper()} {self.year.value}-{self.month.value:02d}")
            canvas.addLayer(prepared['image'].select('dayOfYear'),
                            {'min': 1, 'max': 366, 'palette': ['blue', 'yellow', 'red']}, 'Día del año', False)
            canvas.centerObject(prepared['geometry'], 6)
            with self.map_output:
                display(canvas)
            self.messages.clear_output()
            with self.messages:
                print('Mosaico listo. La exportación utilizará exactamente esta imagen.')
        except Exception as exc:
            self.session.prepared = None
            with self.messages:
                print(f'No se pudo preparar el mosaico: {exc}')
        finally:
            self._busy(False)

    def _export(self, _=None):
        self._busy(True)
        self.messages.clear_output()
        try:
            destinations = [name for name, selected in (('ASSET', self.asset.value), ('GCS', self.gcs.value)) if selected]
            errors = self.session.export(self.bands.value, destinations)
            with self.messages:
                print('Envío terminado. Consulte el estado de cada tarea abajo.')
                for error in errors:
                    print(f'Error: {error}')
            self._refresh()
        except Exception as exc:
            with self.messages:
                print(f'No se pudo exportar: {exc}')
        finally:
            self._busy(False)

    def _refresh(self, _=None):
        rows = []
        for row in self.session.tasks:
            task = row['task']
            try:
                status = task.status()
                state = status.get('state', 'UNKNOWN')
                error = status.get('error_message', '')
            except Exception as exc:
                state, error = 'Sin conexión', str(exc)
            values = [row['description'], row['destination'], state, getattr(task, 'id', '') or '', error]
            rows.append('<tr>' + ''.join(f'<td style="padding:6px;border-bottom:1px solid #ddd">{escape(str(v))}</td>' for v in values) + '</tr>')
        self.task_table.value = (
            '<h4>Tareas de exportación</h4><div style="overflow:auto"><table>'
            '<tr><th>Tarea</th><th>Destino</th><th>Estado</th><th>ID</th><th>Error</th></tr>'
            + ''.join(rows) + '</table></div>'
        )

    def show(self):
        from IPython.display import display
        display(self.panel)
        return self


def run_ui(years=None):
    return MonthlyMosaicUI(years=years).show()


def start_export(ui_obj):
    """Optional notebook trigger equivalent to clicking Exportar mosaico."""
    ui_obj._export()
