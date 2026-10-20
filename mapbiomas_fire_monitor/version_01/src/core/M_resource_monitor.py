"""Sample process RAM and CUDA resources by named processing stage."""
from contextlib import contextmanager
import csv
from html import escape
import json
from pathlib import Path
from threading import Event, RLock, Thread
import time

MIB = 1024**2


class ResourceMonitor:
    def __init__(self, output_dir, *, live=False, interval=.2):
        import psutil
        self.process = psutil.Process()
        self.output_dir = Path(output_dir)
        self.live, self.interval = live, interval
        self.cuda, self.device = None, None
        self.rows, self.notes = {}, []
        self.lock, self.stop = RLock(), Event()
        self.active, self.widget = None, None
        self.last_display = 0
        self.gpu_util_error = None
        self.status = 'running'

    def configure_gpu(self, cuda, device):
        self.cuda, self.device = cuda, device

    def __enter__(self):
        self.started = time.perf_counter()
        if self.live:
            try:
                import ipywidgets as widgets
                from IPython.display import display
                self.widget = widgets.HTML('<b>Monitor de recursos</b> · Preparando…')
                display(self.widget)
            except ImportError:
                pass
        self.thread = Thread(target=self._poll, daemon=True)
        self.thread.start()
        return self

    def _sample(self):
        rss = self.process.memory_info().rss / MIB
        with self.lock:
            current = self.active
            if current is None:
                return
            current['ram_peak_mb'] = max(current['ram_peak_mb'], rss)
            if current['gpu']:
                current['gpu_memory_peak_mb'] = max(current['gpu_memory_peak_mb'],
                                                    self.cuda.memory_allocated(self.device) / MIB)
                if self.gpu_util_error is None:
                    try:
                        utilization = float(self.cuda.utilization(self.device))
                        current['gpu_util_sum'] += utilization
                        current['gpu_util_samples'] += 1
                        current['gpu_util_peak_pct'] = max(current['gpu_util_peak_pct'], utilization)
                    except Exception as exc:
                        self.gpu_util_error = str(exc)
            if self.widget is not None and time.monotonic() - self.last_display >= 1:
                self.last_display = time.monotonic()
                gpu = (f' · VRAM PyTorch: {current["gpu_memory_peak_mb"]:.1f} MiB'
                       if current['gpu'] else ' · CPU')
                self.widget.value = (
                    f'<b>{escape(current["method"])} · {escape(current["scene_id"])}</b><br>'
                    f'RAM actual: {rss:.1f} MiB · pico de etapa: {current["ram_peak_mb"]:.1f} MiB{gpu}'
                    + (f' · GPU: {current["gpu_util_sum"]/current["gpu_util_samples"]:.1f}%'
                       if current['gpu_util_samples'] else ''))

    def _poll(self):
        while not self.stop.wait(self.interval):
            try:
                self._sample()
            except Exception:
                # Sampling must not interrupt an image operation; endpoints still sample synchronously.
                pass

    @contextmanager
    def phase(self, method, scene_id='', *, gpu=False):
        gpu = bool(gpu and self.cuda is not None)
        if gpu:
            self.cuda.synchronize(self.device)
            self.cuda.reset_peak_memory_stats(self.device)
        ram_start = self.process.memory_info().rss / MIB
        gpu_start = self.cuda.memory_allocated(self.device) / MIB if gpu else 0
        current = dict(method=method, scene_id=scene_id, gpu=gpu, ram_peak_mb=ram_start,
                       gpu_memory_peak_mb=gpu_start, gpu_util_sum=0., gpu_util_samples=0,
                       gpu_util_peak_pct=0.)
        with self.lock:
            if self.active is not None:
                raise RuntimeError('Las etapas del monitor no pueden anidarse.')
            self.active = current
        started = time.perf_counter()
        try:
            self._sample()
            started = time.perf_counter()
            yield
        finally:
            try:
                if gpu:
                    self.cuda.synchronize(self.device)
                elapsed = time.perf_counter() - started
                self._sample()
                if gpu:
                    current['gpu_memory_peak_mb'] = max(current['gpu_memory_peak_mb'],
                                                        self.cuda.max_memory_allocated(self.device) / MIB)
                    reserved = self.cuda.max_memory_reserved(self.device) / MIB
                with self.lock:
                    row = self.rows.setdefault((scene_id, method), dict(
                        scene_id=scene_id, method=method, device=str(self.device) if gpu else 'cpu',
                        duration_s=0., calls=0, ram_start_mb=ram_start, ram_peak_mb=ram_start,
                        ram_extra_peak_mb=0., gpu_memory_peak_mb=None, gpu_memory_extra_peak_mb=None,
                        gpu_reserved_peak_mb=None, gpu_util_sum=0., gpu_util_samples=0,
                        gpu_util_peak_pct=None))
                    row['duration_s'] += elapsed
                    row['calls'] += 1
                    row['ram_peak_mb'] = max(row['ram_peak_mb'], current['ram_peak_mb'])
                    row['ram_extra_peak_mb'] = max(row['ram_extra_peak_mb'], current['ram_peak_mb']-ram_start)
                    if gpu:
                        for key, value in (
                            ('gpu_memory_peak_mb', current['gpu_memory_peak_mb']),
                            ('gpu_memory_extra_peak_mb', max(0, current['gpu_memory_peak_mb']-gpu_start)),
                            ('gpu_reserved_peak_mb', reserved),
                        ):
                            row[key] = max(row[key] or 0, value)
                        row['gpu_util_sum'] += current['gpu_util_sum']
                        row['gpu_util_samples'] += current['gpu_util_samples']
                        if current['gpu_util_samples']:
                            row['gpu_util_peak_pct'] = max(row['gpu_util_peak_pct'] or 0, current['gpu_util_peak_pct'])
            finally:
                with self.lock:
                    self.active = None

    @staticmethod
    def _public_row(row):
        public = {key: value for key, value in row.items() if key not in ('gpu_util_sum', 'gpu_util_samples')}
        public['gpu_util_mean_pct'] = row['gpu_util_sum']/row['gpu_util_samples'] if row['gpu_util_samples'] else None
        return public

    def summary(self):
        grouped = {}
        for raw in self.rows.values():
            key = raw['method'], raw['device']
            row = grouped.setdefault(key, dict(raw, scene_id='TOTAL', duration_s=0., calls=0,
                                               gpu_util_sum=0., gpu_util_samples=0))
            row['duration_s'] += raw['duration_s']
            row['calls'] += raw['calls']
            row['gpu_util_sum'] += raw['gpu_util_sum']
            row['gpu_util_samples'] += raw['gpu_util_samples']
            for column in ('ram_peak_mb', 'ram_extra_peak_mb', 'gpu_memory_peak_mb',
                           'gpu_memory_extra_peak_mb', 'gpu_reserved_peak_mb', 'gpu_util_peak_pct'):
                if raw[column] is not None:
                    row[column] = max(row[column] or 0, raw[column])
        return [self._public_row(row) for row in grouped.values()]

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        self.thread.join(timeout=2)
        self.status = 'failed' if exc_type else 'complete'
        self.elapsed = time.perf_counter() - self.started
        self.output_dir.mkdir(parents=True, exist_ok=True)
        rows, summary = [self._public_row(row) for row in self.rows.values()], self.summary()
        report = {
            'status': self.status, 'wall_time_s': self.elapsed, 'sample_interval_s': self.interval,
            'ram_scope': 'RSS del proceso completo; delta pico por invocación de etapa. Incluye entorno y modelo residentes.',
            'gpu_memory_scope': 'Memoria de tensores PyTorch; reserva del allocator informada por separado.',
            'gpu_util_scope': 'Porcentaje del dispositivo completo, muestreado mediante NVML; no exclusivo de este proceso.',
            'gpu_util_error': self.gpu_util_error, 'notes': self.notes,
            'stages': rows, 'summary': summary,
        }
        if exc_type:
            report['error'] = f'{exc_type.__name__}: {exc}'
        (self.output_dir/'resource_usage.json').write_text(json.dumps(report, indent=2))
        for filename, data in (('resource_usage.csv', rows), ('resource_comparison.csv', summary)):
            with (self.output_dir/filename).open('w', newline='') as stream:
                fields = list(data[0]) if data else ['scene_id', 'method', 'device', 'duration_s']
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(data)
        def number(value):
            return '—' if value is None else f'{value:.2f}'
        headings = ['Etapa', 'Dispositivo', 'Tiempo (s)', 'RAM pico (MiB)', 'Δ RAM pico (MiB)',
                    'VRAM pico (MiB)', 'Δ VRAM pico (MiB)', 'GPU media (%)', 'GPU pico (%)']
        columns = ['method', 'device', 'duration_s', 'ram_peak_mb', 'ram_extra_peak_mb',
                   'gpu_memory_peak_mb', 'gpu_memory_extra_peak_mb', 'gpu_util_mean_pct', 'gpu_util_peak_pct']
        table = '<tr>' + ''.join(f'<th>{title}</th>' for title in headings) + '</tr>'
        for row in summary:
            values = [escape(str(row[column])) if i < 2 else number(row[column]) for i, column in enumerate(columns)]
            table += '<tr>' + ''.join(f'<td style="padding:6px">{value}</td>' for value in values) + '</tr>'
            print(f"{row['method']} ({row['device']}): {row['duration_s']:.2f} s · RAM pico {row['ram_peak_mb']:.1f} MiB · Δ RAM {row['ram_extra_peak_mb']:.1f} MiB"
                  + (f" · VRAM pico {row['gpu_memory_peak_mb']:.1f} MiB" if row['gpu_memory_peak_mb'] is not None else ''))
        if self.widget is not None:
            self.widget.value = ('<b>Recursos por etapa</b><div style="overflow:auto"><table>' + table + '</table></div>'
                                 '<p>RAM: proceso completo; compare también el incremento. Preparación compartida y carga del modelo separadas. '
                                 'GPU %: muestreo del dispositivo completo. —: no aplica o no disponible.</p>'
                                 + ''.join(f'<p>{escape(note)}</p>' for note in self.notes))
        print(f'Recursos guardados en {self.output_dir / "resource_comparison.csv"}')
