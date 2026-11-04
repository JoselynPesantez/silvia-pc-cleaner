"""
SILVIA PC Cleaner 1.0
Windows desktop utility. Scans first; deletes only user-selected items.
Run: python main.py
"""
from __future__ import annotations

import os
import hashlib
import threading
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

try:
    import psutil
except ImportError:
    psutil = None

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

APP_NAME = "SILVIA PC Cleaner"
LARGE_FILE_MIN = 100 * 1024 * 1024
SCAN_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Pictures",
    Path.home() / "Videos",
]

@dataclass
class Finding:
    category: str
    path: Path
    size: int
    note: str = ""

def human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"

def safe_is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False

def iter_files(folder: Path):
    """Walk without following directory symlinks; skip inaccessible paths."""
    if not folder.exists() or not folder.is_dir():
        return
    for current, dirs, files in os.walk(folder, topdown=True, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
        for name in files:
            item = Path(current) / name
            try:
                if item.is_file() and not item.is_symlink():
                    yield item
            except OSError:
                continue

def get_cache_folders() -> list[tuple[str, Path]]:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    roaming = Path(os.environ.get("APPDATA", str(Path.home())))
    temp = Path(os.environ.get("TEMP", str(Path.home() / "AppData/Local/Temp")))
    return [
        ("Temporales de Windows", temp),
        ("Temporales del usuario", local / "Temp"),
        ("Caché Edge", local / "Microsoft/Edge/User Data/Default/Cache/Cache_Data"),
        ("Caché Chrome", local / "Google/Chrome/User Data/Default/Cache/Cache_Data"),
        ("Caché Firefox", roaming / "Mozilla/Firefox/Profiles"),
        ("Miniaturas Explorer", local / "Microsoft/Windows/Explorer"),
    ]

def scan_folder(category: str, folder: Path, results: list[Finding],
                progress: Callable[[str], None], mode: str = "cleanup"):
    if not folder.exists():
        return
    progress(f"Analizando: {folder}")
    for item in iter_files(folder):
        try:
            # Browser profile root: only include known cache subfolders.
            if category == "Caché Firefox":
                parts = {p.lower() for p in item.parts}
                if "cache2" not in parts:
                    continue
            elif category == "Miniaturas Explorer":
                if not (item.name.lower().startswith("thumbcache") and item.suffix.lower() == ".db"):
                    continue
            size = item.stat().st_size
            if mode == "cleanup":
                results.append(Finding(category, item, size))
            elif mode == "large" and size >= LARGE_FILE_MIN:
                results.append(Finding("Archivo grande", item, size, "Revisar antes de mover o borrar"))
        except (OSError, PermissionError):
            continue

def scan_large_files(results: list[Finding], progress):
    for root in SCAN_ROOTS:
        if root.exists():
            scan_folder("Archivos grandes", root, results, progress, "large")

def find_duplicates(results: list[Finding], progress):
    # Restrict duplicate search to common personal folders; nothing is deleted.
    size_groups: dict[int, list[Path]] = {}
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        progress(f"Buscando duplicados en: {root}")
        for item in iter_files(root):
            try:
                size = item.stat().st_size
                if size >= 1024:
                    size_groups.setdefault(size, []).append(item)
            except OSError:
                continue

    def digest(path: Path) -> str | None:
        h = hashlib.sha256()
        try:
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    for size, paths in size_groups.items():
        if len(paths) < 2:
            continue
        hashes: dict[str, list[Path]] = {}
        for path in paths:
            value = digest(path)
            if value:
                hashes.setdefault(value, []).append(path)
        for same in hashes.values():
            if len(same) > 1:
                for path in same:
                    results.append(Finding("Posible duplicado", path, size,
                                           f"{len(same)} archivos con contenido idéntico"))

class CleanerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1060x690")
        self.root.minsize(850, 560)
        self.root.configure(bg="#F7F5FA")
        self.findings: list[Finding] = []
        self.row_map: dict[str, Finding] = {}
        self.busy = False
        self._style()
        self._build()
        self.refresh_system_info()

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=27, font=("Segoe UI", 9),
                        background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=8)
        style.configure("Accent.TButton", background="#7543A5", foreground="white")
        style.map("Accent.TButton", background=[("active", "#62358E")])
        style.configure("TProgressbar", troughcolor="#E8E1EF", background="#7543A5")

    def _build(self):
        header = tk.Frame(self.root, bg="#7543A5", padx=22, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="SILVIA PC Cleaner", bg="#7543A5", fg="white",
                 font=("Segoe UI", 21, "bold")).pack(anchor="w")
        tk.Label(header, text="Diagnóstico inteligente · Limpieza asistida · Windows",
                 bg="#7543A5", fg="#F0E8F8", font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))

        info = tk.Frame(self.root, bg="#F7F5FA", padx=20, pady=12)
        info.pack(fill="x")
        self.disk_label = tk.Label(info, text="Disco: calculando…", bg="#F7F5FA",
                                   fg="#352842", font=("Segoe UI", 10, "bold"))
        self.disk_label.pack(side="left", padx=(0, 24))
        self.ram_label = tk.Label(info, text="RAM: calculando…", bg="#F7F5FA",
                                  fg="#352842", font=("Segoe UI", 10, "bold"))
        self.ram_label.pack(side="left")

        controls = tk.Frame(self.root, bg="#F7F5FA", padx=20)
        controls.pack(fill="x", pady=(0, 10))
        self.scan_btn = ttk.Button(controls, text="Analizar equipo", style="Accent.TButton",
                                   command=self.start_scan)
        self.scan_btn.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Enviar seleccionados a Papelera",
                   command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(controls, text="Vaciar Papelera…",
                   command=self.empty_recycle_bin).pack(side="left", padx=4)
        ttk.Button(controls, text="Actualizar estado", command=self.refresh_system_info).pack(side="right")

        self.progress_text = tk.StringVar(value="Listo. El análisis no modifica archivos.")
        tk.Label(self.root, textvariable=self.progress_text, bg="#F7F5FA",
                 fg="#665B70", anchor="w", padx=22).pack(fill="x")
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=20, pady=(5, 10))

        table_frame = tk.Frame(self.root, bg="#F7F5FA", padx=20, pady=5)
        table_frame.pack(fill="both", expand=True)
        cols = ("category", "path", "size", "note")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        for col, title, width in [
            ("category", "Categoría", 155), ("path", "Ubicación", 550),
            ("size", "Tamaño", 95), ("note", "Detalle", 190)
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        footer = tk.Frame(self.root, bg="#F7F5FA", padx=20, pady=10)
        footer.pack(fill="x")

        self.summary = tk.Label(
            footer, text="Todavía no hay resultados.", bg="#F7F5FA",
            fg="#352842", font=("Segoe UI", 10, "bold")
        )
        self.summary.pack(anchor="w")

        footer_bottom = tk.Frame(footer, bg="#F7F5FA")
        footer_bottom.pack(fill="x", pady=(8, 0))

        brand_text = (
            "Creado por Joselyn Pesantez Polanco  |  SILVIA PRO®  |  "
            "Gratuita  |  Código abierto · MIT  |  © 2026 SILVIA PRO. "
            "Todos los derechos reservados."
        )
        tk.Label(
            footer_bottom, text=brand_text, bg="#F7F5FA",
            fg="#8A7D94", font=("Segoe UI", 8), anchor="w"
        ).pack(side="left", fill="x", expand=True)

        links = tk.Frame(footer_bottom, bg="#F7F5FA")
        links.pack(side="right")

        privacy_link = tk.Label(
            links, text="Política de privacidad", bg="#F7F5FA",
            fg="#7543A5", font=("Segoe UI", 8, "underline"), cursor="hand2"
        )
        privacy_link.pack(side="left", padx=(8, 12))
        privacy_link.bind("<Button-1>", lambda _event: self.show_privacy_policy())

        website_link = tk.Label(
            links, text="silviapro.com", bg="#F7F5FA",
            fg="#7543A5", font=("Segoe UI", 8, "underline"), cursor="hand2"
        )
        website_link.pack(side="left")
        website_link.bind(
            "<Button-1>",
            lambda _event: webbrowser.open("https://www.silviapro.com")
        )

    def show_privacy_policy(self):
        policy = tk.Toplevel(self.root)
        policy.title("Política de privacidad · SILVIA PC Cleaner")
        policy.geometry("700x540")
        policy.minsize(520, 400)
        policy.configure(bg="#F7F5FA")
        policy.transient(self.root)

        heading = tk.Label(
            policy, text="Política de privacidad",
            bg="#F7F5FA", fg="#352842",
            font=("Segoe UI", 16, "bold")
        )
        heading.pack(anchor="w", padx=18, pady=(16, 8))

        content = (
            "SILVIA PC Cleaner 1.0\n"
            "Responsable del proyecto: Joselyn Pesantez Polanco · SILVIA PRO\n\n"
            "1. Tratamiento local\n"
            "La aplicación está diseñada para analizar el equipo localmente. "
            "Esta versión no incorpora cuentas de usuario, publicidad, "
            "analítica, telemetría ni mecanismos propios para enviar datos "
            "a servidores de SILVIA PRO.\n\n"
            "2. Información a la que puede acceder\n"
            "Para realizar sus funciones, la aplicación puede consultar "
            "nombres y rutas de archivos, tamaños y metadatos del sistema. "
            "La búsqueda de posibles duplicados calcula hashes SHA-256 "
            "leyendo el contenido de los archivos seleccionados para comparar "
            "su identidad; no crea una copia de esos archivos.\n\n"
            "3. Limpieza y Papelera de reciclaje\n"
            "La aplicación solo intenta mover a la Papelera los archivos "
            "seleccionados por el usuario que pertenecen a las ubicaciones "
            "temporales o de caché admitidas. Los posibles duplicados y los "
            "archivos grandes son informativos en esta versión y no se "
            "eliminan desde sus resultados. Windows puede conservar los "
            "elementos movidos en su Papelera. La función para vaciar la "
            "Papelera puede ser irreversible y requiere confirmación.\n\n"
            "4. Conservación\n"
            "Esta versión no implementa una cuenta ni un servicio propio "
            "de almacenamiento de informes. Los resultados del análisis se "
            "mantienen en la sesión de la aplicación; el código no implementa "
            "un historial persistente de análisis.\n\n"
            "5. Conexión a Internet\n"
            "El análisis y la limpieza no requieren conexión a Internet. "
            "El enlace a SILVIA PRO abre el sitio web en el navegador cuando "
            "el usuario lo selecciona. El tratamiento que realice ese sitio "
            "se rige por sus propias condiciones y política de privacidad.\n\n"
            "6. Cambios\n"
            "Esta política corresponde a SILVIA PC Cleaner 1.0. Si una "
            "versión futura incorpora sincronización, servicios en la nube, "
            "telemetría u otras funciones que cambien el tratamiento de "
            "datos, esta política deberá actualizarse antes de activarlas.\n\n"
            "Contacto: www.silviapro.com"
        )

        text_frame = tk.Frame(policy, bg="#F7F5FA")
        text_frame.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical")
        policy_text = tk.Text(
            text_frame, wrap="word", font=("Segoe UI", 10),
            bg="white", fg="#352842", padx=12, pady=10,
            yscrollcommand=scrollbar.set, relief="flat"
        )
        scrollbar.config(command=policy_text.yview)
        scrollbar.pack(side="right", fill="y")
        policy_text.pack(side="left", fill="both", expand=True)
        policy_text.insert("1.0", content)
        policy_text.config(state="disabled")

        ttk.Button(policy, text="Cerrar", command=policy.destroy).pack(pady=(0, 14))

    def refresh_system_info(self):
        try:
            usage = __import__("shutil").disk_usage(Path.home().anchor)
            self.disk_label.config(text=f"Disco: {human_size(usage.free)} libres de {human_size(usage.total)}")
        except Exception:
            self.disk_label.config(text="Disco: no disponible")
        if psutil:
            mem = psutil.virtual_memory()
            self.ram_label.config(text=f"RAM: {mem.percent}% usada ({human_size(mem.available)} disponible)")
        else:
            self.ram_label.config(text="RAM: instala psutil para ver métricas")

    def set_busy(self, value: bool):
        self.busy = value
        self.scan_btn.config(state="disabled" if value else "normal")
        if value:
            self.progress.start(10)
        else:
            self.progress.stop()

    def start_scan(self):
        if self.busy:
            return
        self.findings.clear()
        self.row_map.clear()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.set_busy(True)
        self.progress_text.set("Iniciando análisis…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        results: list[Finding] = []
        def progress(message):
            self.root.after(0, lambda m=message: self.progress_text.set(m))
        for category, folder in get_cache_folders():
            scan_folder(category, folder, results, progress)
        scan_large_files(results, progress)
        find_duplicates(results, progress)
        self.root.after(0, lambda: self._scan_done(results))

    def _scan_done(self, results):
        self.findings = results
        for index, finding in enumerate(results):
            iid = str(index)
            self.row_map[iid] = finding
            self.tree.insert("", "end", iid=iid, values=(
                finding.category, str(finding.path), human_size(finding.size), finding.note
            ))
        total = sum(x.size for x in results if x.category not in ("Posible duplicado", "Archivo grande"))
        self.summary.config(text=f"{len(results)} hallazgos · {human_size(total)} en categorías de limpieza")
        self.progress_text.set("Análisis terminado. Selecciona únicamente lo que reconoces y deseas eliminar.")
        self.set_busy(False)
        self.refresh_system_info()

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Selecciona uno o más elementos de la lista.")
            return
        chosen = [self.row_map[iid] for iid in selected if iid in self.row_map]
        # Duplicates and large files are informational only in v1.
        deletable = [x for x in chosen if x.category not in ("Posible duplicado", "Archivo grande")]
        if not deletable:
            messagebox.showinfo(APP_NAME, "Los duplicados y archivos grandes son informativos en esta versión. No se eliminan desde aquí.")
            return
        if not send2trash:
            messagebox.showerror(APP_NAME, "Falta la dependencia send2trash. Ejecuta: pip install -r requirements.txt")
            return
        total = sum(x.size for x in deletable)
        if not messagebox.askyesno(APP_NAME,
            f"Se enviarán {len(deletable)} archivos a la Papelera de reciclaje ({human_size(total)}).\n\n"
            "No se borrarán permanentemente. ¿Continuar?"):
            return
        errors = 0
        moved = 0
        for item in deletable:
            try:
                # Revalidate: only files inside approved temporary/cache locations.
                approved = [p for _, p in get_cache_folders()]
                if not any(safe_is_inside(item.path, root) for root in approved):
                    errors += 1
                    continue
                if item.path.exists() and item.path.is_file():
                    send2trash(str(item.path))
                    moved += 1
            except Exception:
                errors += 1
        messagebox.showinfo(APP_NAME, f"Enviados a Papelera: {moved}\nNo procesados: {errors}")
        self.start_scan()

    def empty_recycle_bin(self):
        if os.name != "nt":
            messagebox.showerror(APP_NAME, "Esta función solo está disponible en Windows.")
            return
        if not messagebox.askyesno(APP_NAME,
            "Esta acción vacía la Papelera de reciclaje y puede ser irreversible.\n"
            "Comprueba su contenido antes de continuar.\n\n¿Vaciarla ahora?"):
            return
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Clear-RecycleBin -Force -ErrorAction Stop"],
                           check=True, capture_output=True, text=True)
            messagebox.showinfo(APP_NAME, "Papelera vaciada.")
            self.refresh_system_info()
        except subprocess.CalledProcessError as exc:
            messagebox.showerror(APP_NAME, f"No se pudo vaciar la Papelera.\n{exc.stderr[-1000:]}")

def main():
    if os.name != "nt":
        print("SILVIA PC Cleaner está diseñado para Windows.")
        return
    root = tk.Tk()
    CleanerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
