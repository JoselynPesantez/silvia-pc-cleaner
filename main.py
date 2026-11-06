"""
SILVIA PC Cleaner 1.1
Panel de diagnóstico y gestión asistida del almacenamiento para Windows.
Los archivos personales, grandes y duplicados son informativos: no se eliminan desde la app.
Ejecutar: python main.py
"""
from __future__ import annotations

import os
import sys
import hashlib
import threading
import queue
import time
import shutil
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, date
from collections import defaultdict

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

APP_NAME = "SILVIA PC Cleaner"
VERSION = "1.1.4"
LARGE_FILE_MIN = 100 * 1024 * 1024
SCAN_ROOTS = [
    Path.home() / "Desktop", Path.home() / "Documents",
    Path.home() / "Downloads", Path.home() / "Pictures",
    Path.home() / "Videos", Path.home() / "Music",
]
CATEGORY_EXTENSIONS = {
    "Documentos": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".md"},
    "Imágenes": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".heic"},
    "Vídeos": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".mpeg", ".mpg"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "Comprimidos": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Instaladores": {".exe", ".msi", ".msix", ".appx"},
}
COLORS = {
    "bg": "#F5F3F8", "panel": "#FFFFFF", "ink": "#30263B",
    "muted": "#766D80", "purple": "#7543A5", "purple_dark": "#5F328C",
    "line": "#E7E0ED", "green": "#27845B", "orange": "#C27A22",
    "red": "#BD4A4A", "blue": "#3976A8",
}

@dataclass
class Finding:
    category: str
    path: Path
    size: int
    modified: float
    created: float
    extension: str
    note: str = ""
    priority: str = "Normal"

def human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"

def stamp(value: float) -> str:
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "No disponible"

def file_category(path: Path) -> str:
    ext = path.suffix.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "Otros"

def iter_files(folder: Path):
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

def cache_folders():
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

def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False

def inspect_file(path: Path, category_override: str | None = None) -> Finding | None:
    try:
        st = path.stat()
        cat = category_override or file_category(path)
        return Finding(cat, path, st.st_size, st.st_mtime, st.st_ctime, path.suffix.lower())
    except OSError:
        return None

def discover_programs():
    """Lee metadatos publicados por desinstaladores; no estima última actividad."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    rows, seen = [], set()
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as parent:
                count = winreg.QueryInfoKey(parent)[0]
                for i in range(count):
                    try:
                        sub = winreg.EnumKey(parent, i)
                        with winreg.OpenKey(parent, sub) as key:
                            def val(name):
                                try:
                                    return str(winreg.QueryValueEx(key, name)[0]).strip()
                                except OSError:
                                    return ""
                            name = val("DisplayName")
                            if not name or name.lower() in seen:
                                continue
                            seen.add(name.lower())
                            rows.append({
                                "name": name,
                                "publisher": val("Publisher") or "No indicado",
                                "version": val("DisplayVersion") or "No indicada",
                                "installed": val("InstallDate") or "No indicada",
                                "activity": "Desconocida (Windows no proporciona este dato)",
                            })
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(rows, key=lambda x: x["name"].lower())

class CleanerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} {VERSION}")
        self.root.geometry("1280x820")
        self.root.minsize(980, 650)
        self.root.configure(bg=COLORS["bg"])

        # Contenedor desplazable: permite llegar al footer incluso en pantallas
        # pequeñas o cuando la ventana no está maximizada.
        self.shell = tk.Frame(self.root, bg=COLORS["bg"])
        self.shell.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.shell, bg=COLORS["bg"], highlightthickness=0)
        self.vertical_scroll = ttk.Scrollbar(self.shell, orient="vertical",
                                             command=self.canvas.yview, style="Silvia.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.vertical_scroll.set)
        self.vertical_scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=COLORS["bg"])
        self.content_window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.started = 0.0
        self.findings: list[Finding] = []
        self.programs = []
        self.row_map = {}
        self.current_filter = "Todos"
        self.view_mode = "list"
        self.sort_mode = "modified_desc"
        self.thumbnail_cache = {}
        self.gallery_images = []
        self.gallery_items = {}
        self.column_filters = {}
        self.column_sort = None
        self.checked_items = set()
        self._style()
        self._build()
        self.refresh_system()

    def _update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event):
        self.canvas.itemconfigure(self.content_window, width=event.width)

    def _on_mousewheel(self, event):
        # Windows: delta suele ser múltiplo de 120.
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 9),
                        background="white", fieldbackground="white", foreground=COLORS["ink"])
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                        background="#EEE8F4", foreground=COLORS["ink"])
        style.map("Treeview", background=[("selected", "#E8DDF2")],
                  foreground=[("selected", COLORS["ink"])])
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=(10, 7))
        style.configure("Accent.TButton", background=COLORS["purple"], foreground="white")
        style.map("Accent.TButton", background=[("active", COLORS["purple_dark"])])
        style.configure("TProgressbar", troughcolor="#E9E3EF", background=COLORS["purple"])
        # Barra principal de desplazamiento: visible y alineada con la marca SILVIA.
        style.configure("Silvia.Vertical.TScrollbar",
                        background="#A84CC4", troughcolor="#F3DCEC",
                        bordercolor="#E2C5EA", arrowcolor="#FFFFFF",
                        darkcolor="#81349B", lightcolor="#C77ADB",
                        width=17, arrowsize=15)
        style.map("Silvia.Vertical.TScrollbar",
                  background=[("active", "#C45AD8"), ("pressed", "#81349B")])

    def _card(self, parent, title, variable, accent=None):
        frame = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["line"],
                         highlightthickness=1, padx=15, pady=12)
        tk.Label(frame, text=title.upper(), bg="white", fg=COLORS["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(frame, textvariable=variable, bg="white",
                 fg=accent or COLORS["ink"], font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(5, 0))
        return frame

    def _build(self):
        header = tk.Frame(self.content, bg=COLORS["purple"], padx=24, pady=17)
        header.pack(fill="x")
        tk.Label(header, text="SILVIA PC Cleaner", bg=COLORS["purple"], fg="white",
                 font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(header, text="Diagnóstico inteligente · Almacenamiento · Limpieza asistida",
                 bg=COLORS["purple"], fg="#F0E8F8", font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))

        body = tk.Frame(self.content, bg=COLORS["bg"], padx=20, pady=15)
        body.pack(fill="both", expand=True)

        self.disk_var = tk.StringVar(value="Calculando…")
        self.used_var = tk.StringVar(value="—")
        self.findings_var = tk.StringVar(value="0")
        self.ram_var = tk.StringVar(value="—")
        cards = tk.Frame(body, bg=COLORS["bg"])
        cards.pack(fill="x", pady=(0, 12))
        for i, (title, var, color) in enumerate([
            ("Espacio libre", self.disk_var, COLORS["green"]),
            ("Uso del disco", self.used_var, COLORS["purple"]),
            ("Hallazgos", self.findings_var, COLORS["orange"]),
            ("Memoria RAM", self.ram_var, COLORS["blue"]),
        ]):
            card = self._card(cards, title, var, color)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            cards.columnconfigure(i, weight=1)

        toolbar = tk.Frame(body, bg=COLORS["bg"])
        toolbar.pack(fill="x", pady=(0, 10))
        self.scan_btn = ttk.Button(toolbar, text="Analizar equipo", style="Accent.TButton", command=self.start_scan)
        self.scan_btn.pack(side="left")
        self.cancel_btn = ttk.Button(toolbar, text="Cancelar", command=self.cancel_scan, state="disabled")
        self.cancel_btn.pack(side="left", padx=7)
        ttk.Button(toolbar, text="Seleccionar categoría", command=self.select_category).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Limpiar selección", command=self.clear_selection).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Enviar selección permitida a Papelera", command=self.delete_selected).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Actualizar", command=self.refresh_system).pack(side="right")

        self.status = tk.StringVar(value="Listo. El análisis no modifica archivos.")
        tk.Label(body, textvariable=self.status, bg=COLORS["bg"], fg=COLORS["muted"],
                 anchor="w", font=("Segoe UI", 9)).pack(fill="x")
        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(5, 3))
        self.detail = tk.StringVar(value="Tiempo: 00:00")
        tk.Label(body, textvariable=self.detail, bg=COLORS["bg"], fg=COLORS["muted"],
                 anchor="w", font=("Segoe UI", 8)).pack(fill="x", pady=(0, 10))

        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill="both", expand=True)
        self.files_tab = tk.Frame(self.tabs, bg="white")
        self.programs_tab = tk.Frame(self.tabs, bg="white")
        self.tabs.add(self.files_tab, text="Archivos y almacenamiento")
        self.tabs.add(self.programs_tab, text="Programas instalados")
        self._build_files_tab()
        self._build_programs_tab()

        # Footer anclado a la ventana principal para que no desaparezca al ampliar la tabla.
        footer = tk.Frame(self.content, bg="#EEE8F4", padx=18, pady=8,
                          highlightbackground=COLORS["line"], highlightthickness=1)
        footer.pack(side="bottom", fill="x")

        footer_top = tk.Frame(footer, bg="#EEE8F4")
        footer_top.pack(fill="x")
        tk.Label(footer_top, text="SILVIA PC Cleaner  ·  SMART PC MAINTENANCE",
                 bg="#EEE8F4", fg=COLORS["purple_dark"],
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(footer_top, text="100% GRATUITA  ·  CÓDIGO ABIERTO · MIT",
                 bg="#EEE8F4", fg=COLORS["green"],
                 font=("Segoe UI", 8, "bold")).pack(side="right")

        footer_bottom = tk.Frame(footer, bg="#EEE8F4")
        footer_bottom.pack(fill="x", pady=(4, 0))
        tk.Label(footer_bottom,
                 text="Creado por Joselyn Pesantez Polanco · SILVIA PRO® · © 2026 SILVIA PRO. Todos los derechos reservados.",
                 bg="#EEE8F4", fg=COLORS["muted"],
                 font=("Segoe UI", 8)).pack(side="left")
        links = tk.Frame(footer_bottom, bg="#EEE8F4")
        links.pack(side="right")
        privacy_link = tk.Label(links, text="Política de privacidad",
                                bg="#EEE8F4", fg=COLORS["purple"],
                                cursor="hand2", font=("Segoe UI", 8, "underline"))
        privacy_link.pack(side="left", padx=(0, 14))
        privacy_link.bind("<Button-1>", lambda _event: self.show_privacy())
        website_link = tk.Label(links, text="www.silviapro.com",
                                bg="#EEE8F4", fg=COLORS["purple"],
                                cursor="hand2", font=("Segoe UI", 8, "underline"))
        website_link.pack(side="left")
        website_link.bind("<Button-1>", lambda _event: webbrowser.open("https://www.silviapro.com"))

    def _build_files_tab(self):
        bar = tk.Frame(self.files_tab, bg="white", padx=10, pady=9)
        bar.pack(fill="x")

        self.filter_var = tk.StringVar(value="Todos")
        choices = ["Todos", "Documentos", "Imágenes", "Vídeos", "Audio", "Comprimidos",
                   "Instaladores", "Otros", "Archivo grande", "Posible duplicado", "Temporales"]
        ttk.Label(bar, text="Categoría:").pack(side="left")
        combo = ttk.Combobox(bar, textvariable=self.filter_var, values=choices,
                             state="readonly", width=16)
        combo.pack(side="left", padx=5)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.render_findings())

        ttk.Label(bar, text="Ordenar:").pack(side="left", padx=(8, 2))
        self.sort_var = tk.StringVar(value="Modificación: recientes primero")
        sort_values = [
            "Tamaño: menor a mayor", "Tamaño: mayor a menor",
            "Modificación: recientes primero", "Modificación: antiguos primero",
            "Creación: recientes primero", "Creación: antiguos primero",
            "Nombre: A–Z", "Nombre: Z–A"
        ]
        sort_combo = ttk.Combobox(bar, textvariable=self.sort_var, values=sort_values,
                                  state="readonly", width=27)
        sort_combo.pack(side="left", padx=3)
        sort_combo.bind("<<ComboboxSelected>>", self._sort_changed)

        self.category_summary = tk.StringVar(value="Aún no hay análisis.")
        tk.Label(bar, textvariable=self.category_summary, bg="white",
                 fg=COLORS["muted"]).pack(side="right")
        filters = tk.Frame(self.files_tab, bg="white", padx=10, pady=0)
        filters.pack(fill="x", pady=(0, 7))
        ttk.Label(filters, text="Usa las flechas de los encabezados para filtrar y ordenar, como en Excel.").pack(side="left")
        ttk.Button(filters, text="Quitar filtros", command=self.clear_column_filters).pack(side="right", padx=(6, 0))
        ttk.Button(filters, text="Galería", command=lambda: self.set_view("gallery")).pack(side="right", padx=(4, 0))
        ttk.Button(filters, text="Lista", command=lambda: self.set_view("list")).pack(side="right")

        frame = tk.Frame(self.files_tab, bg="white", padx=10, pady=4)
        frame.pack(fill="both", expand=True)
        cols = ("check", "category", "name", "size", "priority", "modified", "created", "path")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        headers = [("check", "✓", 42), ("category", "Categoría  ▾", 125),
                   ("name", "Nombre  ▾", 210), ("size", "Tamaño  ▾", 105),
                   ("priority", "Revisión  ▾", 100), ("modified", "Modificado  ▾", 150),
                   ("created", "Creado  ▾", 150), ("path", "Ubicación  ▾", 420)]
        for col, label, width in headers:
            self.tree.heading(col, text=label, command=lambda c=col: self._open_column_filter(c) if c != "check" else self._toggle_all_visible())
            self.tree.column(col, width=width, anchor="center" if col == "check" else "w", stretch=(col == "path"))
        self.tree.bind("<Button-1>", self._tree_checkbox_click, add="+")
        self.tree.tag_configure("high", foreground=COLORS["red"])
        self.tree.tag_configure("medium", foreground=COLORS["orange"])
        self.tree.tag_configure("low", foreground=COLORS["green"])
        self.tree.bind("<Double-1>", self._tree_double_click)
        self.tree_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.tree_scroll.set, xscrollcommand=x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree_scroll.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")

        self.gallery_canvas = tk.Canvas(frame, bg="white", highlightthickness=0)
        self.gallery_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.gallery_canvas.yview)
        self.gallery_canvas.configure(yscrollcommand=self.gallery_scroll.set)
        self.gallery_inner = tk.Frame(self.gallery_canvas, bg="white")
        self.gallery_window = self.gallery_canvas.create_window((0, 0), window=self.gallery_inner, anchor="nw")
        self.gallery_inner.bind("<Configure>", lambda _e: self.gallery_canvas.configure(
            scrollregion=self.gallery_canvas.bbox("all")))
        self.gallery_canvas.bind("<Configure>", lambda e: self.gallery_canvas.itemconfigure(
            self.gallery_window, width=e.width))
        self.gallery_canvas.grid(row=0, column=0, sticky="nsew")
        self.gallery_scroll.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.set_view("list")

    def _sort_changed(self, _event=None):
        labels = {
            "Tamaño: menor a mayor": "size_asc",
            "Tamaño: mayor a menor": "size_desc",
            "Modificación: recientes primero": "modified_desc",
            "Modificación: antiguos primero": "modified_asc",
            "Creación: recientes primero": "created_desc",
            "Creación: antiguos primero": "created_asc",
            "Nombre: A–Z": "name_asc",
            "Nombre: Z–A": "name_desc",
        }
        self.sort_mode = labels.get(self.sort_var.get(), "modified_desc")
        self.render_findings()

    def _tree_checkbox_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) == "#1":
            iid = self.tree.identify_row(event.y)
            if iid:
                if iid in self.checked_items: self.checked_items.remove(iid)
                else: self.checked_items.add(iid)
                self._update_checkbox(iid)
            return "break"

    def _update_checkbox(self, iid):
        if self.tree.exists(iid):
            values = list(self.tree.item(iid, "values"))
            if values:
                values[0] = "☑" if iid in self.checked_items else "☐"
                self.tree.item(iid, values=values)

    def _toggle_all_visible(self):
        ids = list(self.tree.get_children())
        select_all = not ids or not all(i in self.checked_items for i in ids)
        for iid in ids:
            (self.checked_items.add if select_all else self.checked_items.discard)(iid)
            self._update_checkbox(iid)

    def _open_column_filter(self, column):
        labels = {"category":"Categoría", "name":"Nombre", "size":"Tamaño", "priority":"Revisión", "modified":"Modificado", "created":"Creado", "path":"Ubicación"}
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Ordenar ascendente", command=lambda c=column: self._sort_column(c, False))
        menu.add_command(label="Ordenar descendente", command=lambda c=column: self._sort_column(c, True))
        menu.add_separator()
        if column == "name":
            menu.add_command(label="Buscar texto…", command=self._prompt_name_filter)
        elif column == "size":
            menu.add_command(label="Filtrar rango de MB…", command=self._prompt_size_filter)
        elif column in ("modified", "created"):
            menu.add_command(label="Últimos 7 días", command=lambda c=column: self._date_quick_filter(c, 7))
            menu.add_command(label="Últimos 30 días", command=lambda c=column: self._date_quick_filter(c, 30))
            menu.add_command(label="Últimos 12 meses", command=lambda c=column: self._date_quick_filter(c, 365))
        values = self._column_values(column)
        if values:
            submenu = tk.Menu(menu, tearoff=False)
            for value in values[:60]:
                submenu.add_command(label=str(value)[:65], command=lambda c=column, v=value: self._set_column_filter(c, v))
            menu.add_cascade(label="Filtrar por valor", menu=submenu)
        menu.add_separator()
        menu.add_command(label="Quitar filtro de esta columna", command=lambda c=column: self._clear_one_filter(c))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        menu.grab_release()

    def _column_values(self, column):
        vals = set()
        for f in self.findings:
            value = {"category":f.category, "priority":f.priority, "modified":stamp(f.modified),
                     "created":stamp(f.created), "path":str(f.path.parent), "name":f.path.name,
                     "size":human_size(f.size)}.get(column)
            if value is not None: vals.add(value)
        return sorted(vals, key=lambda x: str(x).casefold())

    def _sort_column(self, column, descending):
        self.column_sort = (column, descending)
        self.render_findings()

    def _set_column_filter(self, column, value):
        self.column_filters[column] = value; self.render_findings()

    def _clear_one_filter(self, column):
        self.column_filters.pop(column, None); self.render_findings()

    def clear_column_filters(self):
        self.column_filters.clear(); self.render_findings()

    def _prompt_name_filter(self):
        value = simpledialog.askstring(APP_NAME, "Mostrar nombres que contengan:", parent=self.root)
        if value is not None:
            self.column_filters["name_contains"] = value.strip().casefold(); self.render_findings()

    def _prompt_size_filter(self):
        low = simpledialog.askstring(APP_NAME, "Tamaño mínimo en MB (vacío = sin mínimo):", parent=self.root)
        if low is None: return
        high = simpledialog.askstring(APP_NAME, "Tamaño máximo en MB (vacío = sin máximo):", parent=self.root)
        if high is None: return
        try:
            lo = float(low) if low.strip() else None; hi = float(high) if high.strip() else None
            if lo is not None and hi is not None and lo > hi: raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "Introduce un rango válido en MB."); return
        self.column_filters["size_min"] = lo; self.column_filters["size_max"] = hi; self.render_findings()

    def _date_quick_filter(self, column, days):
        self.column_filters[f"{column}_days"] = days; self.render_findings()

    def set_view(self, mode):
        self.view_mode = mode
        if mode == "gallery":
            self.tree.grid_remove()
            self.tree_scroll.grid_remove()
            self.gallery_canvas.grid()
            self.gallery_scroll.grid()
            self._render_gallery()
        else:
            self.gallery_canvas.grid_remove()
            self.gallery_scroll.grid_remove()
            self.tree.grid()
            self.tree_scroll.grid()
        self._update_scroll_region()

    def _visible_image_items(self):
        return [(iid, item) for iid, item in self.row_map.items()
                if item.category == "Imágenes"]

    def _thumbnail(self, item, size=(132, 100)):
        if Image is None or ImageTk is None:
            return None
        key = (str(item.path), item.modified, item.size, size)
        if key in self.thumbnail_cache:
            return self.thumbnail_cache[key]
        try:
            with Image.open(item.path) as source:
                source.thumbnail(size)
                image = Image.new("RGB", size, "white")
                if source.mode in ("RGBA", "LA"):
                    rgba = source.convert("RGBA")
                    image.paste(rgba, ((size[0]-rgba.width)//2, (size[1]-rgba.height)//2), rgba)
                else:
                    source = source.convert("RGB")
                    image.paste(source, ((size[0]-source.width)//2, (size[1]-source.height)//2))
            photo = ImageTk.PhotoImage(image)
            self.thumbnail_cache[key] = photo
            return photo
        except Exception:
            return None

    def _render_gallery(self):
        for child in self.gallery_inner.winfo_children():
            child.destroy()
        self.gallery_images.clear()
        self.gallery_items.clear()
        images = self._visible_image_items()
        if not images:
            message = "Selecciona la categoría Imágenes para ver las fotografías."
            tk.Label(self.gallery_inner, text=message, bg="white", fg=COLORS["muted"],
                     font=("Segoe UI", 10)).pack(anchor="w", padx=15, pady=15)
            return
        if Image is None:
            tk.Label(self.gallery_inner,
                     text="Para mostrar miniaturas instala Pillow: pip install Pillow",
                     bg="white", fg=COLORS["orange"], font=("Segoe UI", 10)).pack(anchor="w", padx=15, pady=15)
            return
        width = max(self.gallery_canvas.winfo_width(), 500)
        columns = max(2, min(6, width // 190))
        for index, (iid, item) in enumerate(images):
            card = tk.Frame(self.gallery_inner, bg="#FAF8FC", highlightbackground=COLORS["line"],
                            highlightthickness=1, padx=8, pady=8)
            card.grid(row=index // columns, column=index % columns, padx=7, pady=7, sticky="n")
            photo = self._thumbnail(item)
            if photo:
                self.gallery_images.append(photo)
                picture = tk.Label(card, image=photo, bg="white", cursor="hand2")
            else:
                picture = tk.Label(card, text="Vista previa no disponible", width=18, height=6,
                                   bg="#EEE8F4", fg=COLORS["muted"], cursor="hand2")
            picture.pack()
            name = tk.Label(card, text=item.path.name[:24], bg="#FAF8FC",
                            fg=COLORS["ink"], wraplength=150)
            name.pack(anchor="w", pady=(5, 0))
            details = tk.Label(card, text=f"{human_size(item.size)} · {stamp(item.modified)}",
                               bg="#FAF8FC", fg=COLORS["muted"], font=("Segoe UI", 8))
            details.pack(anchor="w")
            for widget in (card, picture, name, details):
                widget.bind("<Button-1>", lambda _e, row=iid: self._toggle_gallery_selection(row))
                widget.bind("<Double-Button-1>", lambda _e, row=iid: self._open_preview(row))
            self.gallery_items[iid] = card

    def _toggle_gallery_selection(self, iid):
        selected = set(self.tree.selection())
        if iid in selected:
            selected.remove(iid); self.checked_items.discard(iid)
        else:
            selected.add(iid); self.checked_items.add(iid)
        self.tree.selection_set(list(selected)); self._update_checkbox(iid)
        card = self.gallery_items.get(iid)
        if card:
            card.configure(highlightbackground=COLORS["purple"] if iid in selected else COLORS["line"],
                           highlightthickness=2 if iid in selected else 1)

    def _tree_double_click(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self._open_preview(selected[0])

    def _open_preview(self, iid):
        item = self.row_map.get(iid)
        if not item or item.category != "Imágenes":
            return
        if Image is None or ImageTk is None:
            messagebox.showinfo(APP_NAME, "La vista previa requiere Pillow. Instálalo con: pip install Pillow")
            return
        try:
            with Image.open(item.path) as source:
                source.load()
                source.thumbnail((1000, 700))
                preview = ImageTk.PhotoImage(source.copy())
        except Exception as exc:
            messagebox.showwarning(APP_NAME, f"No se pudo abrir la imagen.\\n{exc}")
            return
        window = tk.Toplevel(self.root)
        window.title(f"Vista previa · {item.path.name}")
        window.geometry("900x760")
        window.configure(bg=COLORS["bg"])
        image_label = tk.Label(window, image=preview, bg=COLORS["bg"])
        image_label.image = preview
        image_label.pack(fill="both", expand=True, padx=12, pady=12)
        info = tk.Label(window,
                        text=f"{item.path.name}  ·  {human_size(item.size)}  ·  Modificado: {stamp(item.modified)}\\n{item.path}",
                        bg=COLORS["bg"], fg=COLORS["ink"], justify="left", wraplength=850)
        info.pack(fill="x", padx=14, pady=(0, 12))

    def _build_programs_tab(self):
        top = tk.Frame(self.programs_tab, bg="white", padx=12, pady=10)
        top.pack(fill="x")
        ttk.Button(top, text="Consultar programas instalados", command=self.load_programs).pack(side="left")
        tk.Label(top, text="La última actividad solo se muestra si existe una fuente verificable.",
                 bg="white", fg=COLORS["muted"], font=("Segoe UI", 8)).pack(side="left", padx=12)
        frame = tk.Frame(self.programs_tab, bg="white", padx=10, pady=5)
        frame.pack(fill="both", expand=True)
        cols = ("name", "publisher", "version", "installed", "activity")
        self.program_tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, label, width in [
            ("name", "Programa", 280), ("publisher", "Fabricante", 210),
            ("version", "Versión", 120), ("installed", "Instalación", 120),
            ("activity", "Última actividad", 330),
        ]:
            self.program_tree.heading(col, text=label)
            self.program_tree.column(col, width=width, anchor="w")
        sy = ttk.Scrollbar(frame, orient="vertical", command=self.program_tree.yview)
        self.program_tree.configure(yscrollcommand=sy.set)
        self.program_tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")

    def refresh_system(self):
        try:
            usage = shutil.disk_usage(Path.home().anchor)
            self.disk_var.set(f"{human_size(usage.free)} libres")
            self.used_var.set(f"{usage.used / usage.total * 100:.1f}% utilizado")
        except Exception:
            self.disk_var.set("No disponible")
            self.used_var.set("No disponible")
        if psutil:
            mem = psutil.virtual_memory()
            self.ram_var.set(f"{mem.percent}% usada")
        else:
            self.ram_var.set("psutil no instalado")

    def start_scan(self):
        if self.busy:
            return
        self.findings = []
        self.row_map.clear()
        self.checked_items.clear()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.busy = True
        self.started = time.monotonic()
        self.progress.configure(value=0, mode="indeterminate")
        self.progress.start(12)
        self.status.set("Preparando diagnóstico del almacenamiento…")
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._poll)

    def cancel_scan(self):
        if self.busy:
            self.cancel.set()
            self.cancel_btn.configure(state="disabled")
            self.status.set("Cancelando de forma segura…")

    def _worker(self):
        found = []
        count = 0
        try:
            for label, folder in cache_folders():
                if self.cancel.is_set():
                    break
                self.events.put(("status", f"Revisando {label}…", count))
                for path in iter_files(folder):
                    if self.cancel.is_set():
                        break
                    if label == "Caché Firefox" and "cache2" not in {p.lower() for p in path.parts}:
                        continue
                    if label == "Miniaturas Explorer" and not (path.name.lower().startswith("thumbcache") and path.suffix.lower() == ".db"):
                        continue
                    item = inspect_file(path, "Temporales")
                    if item:
                        item.note = label
                        found.append(item)
                    count += 1
                    if count % 100 == 0:
                        self.events.put(("status", f"Revisando temporales y cachés…", count))
            for root in SCAN_ROOTS:
                if self.cancel.is_set():
                    break
                self.events.put(("status", f"Clasificando archivos en {root.name}…", count))
                for path in iter_files(root):
                    if self.cancel.is_set():
                        break
                    item = inspect_file(path)
                    if item:
                        found.append(item)
                        if item.size >= LARGE_FILE_MIN:
                            large = inspect_file(path, "Archivo grande")
                            if large:
                                large.note = "Informativo: revisar antes de tomar decisiones"
                                found.append(large)
                    count += 1
                    if count % 100 == 0:
                        self.events.put(("status", "Clasificando archivos…", count))
            # Hash only same-size candidates; no file is modified.
            groups = defaultdict(list)
            for item in found:
                if item.category != "Temporales" and item.size >= 1024:
                    groups[item.size].append(item.path)
            candidates = [p for group in groups.values() if len(group) > 1 for p in group]
            hashes = defaultdict(list)
            total = len(candidates)
            for idx, path in enumerate(candidates, 1):
                if self.cancel.is_set():
                    break
                try:
                    h = hashlib.sha256()
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            if self.cancel.is_set():
                                break
                            h.update(chunk)
                    if not self.cancel.is_set():
                        hashes[(path.stat().st_size, h.hexdigest())].append(path)
                except OSError:
                    pass
                if idx % 3 == 0 or idx == total:
                    self.events.put(("hash", idx, total, count))
            for same in hashes.values():
                if len(same) > 1:
                    for path in same:
                        item = inspect_file(path, "Posible duplicado")
                        if item:
                            item.note = f"Contenido idéntico: {len(same)} copias"
                            found.append(item)
            self.events.put(("done", found, self.cancel.is_set(), count))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll(self):
        if not self.busy:
            return
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    self.status.set(event[1])
                    self.detail.set(f"Archivos examinados (contador parcial): {event[2]:,} · Tiempo: {self.elapsed()}")
                elif event[0] == "hash":
                    _, current, total, count = event
                    pct = int(current * 100 / total) if total else 0
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=pct)
                    self.status.set("Comparando candidatos a duplicados mediante SHA-256…")
                    self.detail.set(f"Comparación: {current:,}/{total:,} ({pct}%) · Archivos examinados: {count:,}")
                elif event[0] == "done":
                    self.finish(event[1], event[2], event[3])
                    return
                elif event[0] == "error":
                    self.finish([], True, 0)
                    messagebox.showerror(APP_NAME, event[1])
                    return
        except queue.Empty:
            pass
        self.detail.set(f"Tiempo transcurrido: {self.elapsed()}")
        self.root.after(100, self._poll)

    def elapsed(self):
        seconds = int(time.monotonic() - self.started) if self.started else 0
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    def finish(self, results, cancelled, count):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0 if cancelled else 100)
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.findings = results
        self.assign_priorities()
        self.render_findings()
        self.findings_var.set(str(len(results)))
        self.status.set("Análisis cancelado; se conservan los hallazgos parciales." if cancelled else
                        "Análisis terminado. Revisa los hallazgos antes de tomar decisiones.")
        self.detail.set(f"Archivos examinados (contador parcial): {count:,} · Tiempo total: {self.elapsed()}")
        self.refresh_system()

    def assign_priorities(self):
        by_category = defaultdict(list)
        for f in self.findings:
            if f.category not in ("Temporales", "Posible duplicado", "Archivo grande"):
                by_category[f.category].append(f.size)
        thresholds = {}
        for cat, sizes in by_category.items():
            ordered = sorted(sizes)
            thresholds[cat] = (ordered[int((len(ordered)-1)*.75)], ordered[int((len(ordered)-1)*.95)])
        for f in self.findings:
            if f.category in ("Temporales", "Posible duplicado", "Archivo grande"):
                f.priority = "Revisar"
                continue
            p75, p95 = thresholds.get(f.category, (0, 0))
            if f.size >= max(p95, 1):
                f.priority = "Alta"
            elif f.size >= max(p75, 1):
                f.priority = "Media"
            else:
                f.priority = "Baja"

    def render_findings(self):
        for row in self.tree.get_children(): self.tree.delete(row)
        self.row_map.clear()
        selected = self.filter_var.get(); counts = defaultdict(int); now = datetime.now(); visible = []
        for idx, item in enumerate(self.findings):
            counts[item.category] += 1
            f = self.column_filters
            if selected != "Todos" and item.category != selected: continue
            if "category" in f and item.category != f["category"]: continue
            if "priority" in f and item.priority != f["priority"]: continue
            if "path" in f and str(item.path.parent) != f["path"]: continue
            if "name" in f and item.path.name != f["name"]: continue
            if f.get("name_contains") and f["name_contains"] not in item.path.name.casefold(): continue
            if "size" in f and human_size(item.size) != f["size"]: continue
            if f.get("size_min") is not None and item.size < f["size_min"] * 1048576: continue
            if f.get("size_max") is not None and item.size > f["size_max"] * 1048576: continue
            if "modified" in f and stamp(item.modified) != f["modified"]: continue
            if "created" in f and stamp(item.created) != f["created"]: continue
            if "modified_days" in f and (now - datetime.fromtimestamp(item.modified)).days > f["modified_days"]: continue
            if "created_days" in f and (now - datetime.fromtimestamp(item.created)).days > f["created_days"]: continue
            visible.append((idx, item))
        sorters = {"size_asc":lambda x:x[1].size, "size_desc":lambda x:-x[1].size,
                   "modified_asc":lambda x:x[1].modified, "modified_desc":lambda x:-x[1].modified,
                   "created_asc":lambda x:x[1].created, "created_desc":lambda x:-x[1].created,
                   "name_asc":lambda x:x[1].path.name.casefold(), "name_desc":lambda x:x[1].path.name.casefold()}
        if self.column_sort:
            col, desc = self.column_sort
            key_map = {"category": lambda x: x[1].category.casefold(),
                       "name": lambda x: x[1].path.name.casefold(),
                       "size": lambda x: x[1].size,
                       "priority": lambda x: x[1].priority.casefold(),
                       "modified": lambda x: x[1].modified,
                       "created": lambda x: x[1].created,
                       "path": lambda x: str(x[1].path).casefold()}
            visible.sort(key=key_map.get(col, lambda x: x[1].modified), reverse=desc)
        else:
            visible.sort(key=sorters.get(self.sort_mode, sorters["modified_desc"]), reverse=self.sort_mode == "name_desc")
        for idx, item in visible:
            iid=str(idx); self.row_map[iid]=item
            checked="☑" if iid in self.checked_items else "☐"
            tag={"Alta":"high", "Media":"medium", "Baja":"low"}.get(item.priority, "")
            self.tree.insert("", "end", iid=iid, values=(checked,item.category,item.path.name,human_size(item.size),item.priority,stamp(item.modified),stamp(item.created),str(item.path)),tags=(tag,))
        self.category_summary.set("  ·  ".join(f"{k}: {v}" for k,v in sorted(counts.items())) or "Sin resultados")
        if self.view_mode == "gallery": self._render_gallery()

    def select_category(self):
        category = self.filter_var.get()
        if category == "Todos":
            messagebox.showinfo(APP_NAME, "Elige primero una categoría en el filtro.")
            return
        ids = [iid for iid, item in self.row_map.items() if item.category == category]
        self.checked_items.update(ids)
        for iid in ids: self._update_checkbox(iid)
        self.tree.selection_set(ids)

    def clear_selection(self):
        self.checked_items.clear(); self.tree.selection_remove(self.tree.selection())
        for iid in self.tree.get_children(): self._update_checkbox(iid)
        for card in self.gallery_items.values(): card.configure(highlightbackground=COLORS["line"], highlightthickness=1)

    def delete_selected(self):
        selected_ids = set(self.checked_items) | set(self.tree.selection())
        chosen = [self.row_map[i] for i in selected_ids if i in self.row_map]
        if not chosen:
            messagebox.showinfo(APP_NAME, "Selecciona elementos de la lista o de la galería.")
            return
        approved = [p for _, p in cache_folders()]
        deletable = [f for f in chosen if
                     (f.category == "Temporales" and any(inside(f.path, p) for p in approved))
                     or (f.category == "Imágenes" and f.path.suffix.lower() in CATEGORY_EXTENSIONS["Imágenes"])]
        blocked = len(chosen) - len(deletable)
        if not deletable:
            messagebox.showinfo(APP_NAME, "Esta selección es informativa. Solo los temporales/cachés aprobados y las imágenes seleccionadas pueden enviarse a Papelera.")
            return
        if not send2trash:
            messagebox.showerror(APP_NAME, "Falta send2trash. Instálalo con: pip install Send2Trash")
            return
        total = sum(f.size for f in deletable)
        image_count = sum(1 for f in deletable if f.category == "Imágenes")
        temp_count = len(deletable) - image_count
        detail = []
        if image_count:
            detail.append(f"{image_count} imágenes")
        if temp_count:
            detail.append(f"{temp_count} temporales/cachés")
        warning = (f"Se enviarán {len(deletable)} elementos ({', '.join(detail)}; {human_size(total)}) "
                   "a la Papelera de Windows. Podrás restaurarlos desde la Papelera mientras no la vacíes.")
        if blocked:
            warning += f"\\n{blocked} elementos informativos no se modificarán."
        if not messagebox.askyesno(APP_NAME, warning + "\\n\\n¿Continuar?"):
            return
        moved, errors = 0, 0
        for f in deletable:
            try:
                allowed = (f.category == "Imágenes" and f.path.suffix.lower() in CATEGORY_EXTENSIONS["Imágenes"])
                allowed = allowed or (f.category == "Temporales" and any(inside(f.path, p) for p in approved))
                if allowed and f.path.exists() and f.path.is_file():
                    send2trash(str(f.path))
                    moved += 1
            except Exception:
                errors += 1
        messagebox.showinfo(APP_NAME, f"Enviados a Papelera: {moved}\\nNo procesados: {errors}")
        self.start_scan()

    def show_privacy(self):
        """Muestra la política de privacidad dentro de la aplicación."""
        window = tk.Toplevel(self.root)
        window.title("Política de privacidad · SILVIA PC Cleaner")
        window.geometry("680x520")
        window.minsize(560, 420)
        window.configure(bg=COLORS["bg"])
        window.transient(self.root)

        heading = tk.Frame(window, bg=COLORS["purple"], padx=20, pady=14)
        heading.pack(fill="x")
        tk.Label(heading, text="Política de privacidad",
                 bg=COLORS["purple"], fg="white",
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(heading, text="SILVIA PC Cleaner 1.1.4 · Última actualización: noviembre de 2026",
                 bg=COLORS["purple"], fg="#F0E8F8",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

        content = tk.Text(window, wrap="word", padx=18, pady=16,
                          bg="white", fg=COLORS["ink"], relief="flat",
                          font=("Segoe UI", 10), spacing1=3, spacing3=5)
        content.pack(fill="both", expand=True, padx=14, pady=14)
        policy = """SILVIA PC Cleaner es una herramienta gratuita de diagnóstico y limpieza asistida para Windows.

1. Información y funcionamiento
El análisis se realiza localmente en el equipo. La aplicación consulta metadatos del sistema y de los archivos seleccionados para mostrar categorías, tamaños, ubicaciones y fechas disponibles. También puede consultar información de programas instalados en el registro de Windows.

2. Recopilación y transmisión
SILVIA PC Cleaner no recopila ni transmite información personal a SILVIA PRO. El código de esta versión no implementa cuentas de usuario, publicidad, telemetría, servidores propios ni envío de archivos o informes. Los resultados del análisis se mantienen en memoria durante la sesión de la aplicación.

3. Acceso a archivos y cambios
La aplicación examina ubicaciones locales para generar el diagnóstico. Los archivos personales, archivos grandes y posibles duplicados se muestran con fines informativos. Las imágenes pueden seleccionarse y enviarse a la Papelera de reciclaje tras una confirmación explícita. La función de limpieza también permite elementos temporales y cachés de ubicaciones aprobadas. Los archivos enviados a la Papelera pueden restaurarse mientras esta no se vacíe.

4. Conexión a Internet
El análisis y las funciones principales no requieren conexión a Internet. El enlace al sitio web de SILVIA PRO solo se abre si el usuario hace clic en él.

5. Control del usuario
El usuario decide cuándo ejecutar un análisis y si desea enviar los elementos temporales permitidos a la Papelera. Los resultados pueden variar según los permisos y la configuración de Windows.

6. Licencia y contacto
SILVIA PC Cleaner se distribuye gratuitamente como software de código abierto bajo la licencia MIT. Para más información, visita www.silviapro.com.

Desarrollado por Joselyn Pesantez Polanco · SILVIA PRO® · © 2026 SILVIA PRO.

Nota: esta política describe el comportamiento implementado en SILVIA PC Cleaner 1.1.2; no sustituye la política de privacidad de Windows ni la de otros programas instalados en el equipo."""
        content.insert("1.0", policy)
        content.configure(state="disabled")
        ttk.Button(window, text="Cerrar", command=window.destroy).pack(pady=(0, 12))

    def load_programs(self):
        for row in self.program_tree.get_children():
            self.program_tree.delete(row)
        self.programs = discover_programs()
        for item in self.programs:
            self.program_tree.insert("", "end", values=(
                item["name"], item["publisher"], item["version"],
                item["installed"], item["activity"]))
        if not self.programs:
            messagebox.showinfo(APP_NAME, "No se pudieron consultar programas instalados en el registro de Windows.")

def main():
    if os.name != "nt":
        print("SILVIA PC Cleaner está diseñado para Windows.")
        return
    root = tk.Tk()
    CleanerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
