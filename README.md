SILVIA PC Cleaner 1.0

A free and open-source Windows PC cleaning utility.

Creado por Joselyn Pesantez Polanco | SILVIA PRO®

SILVIA PC Cleaner is a Windows desktop application designed to help users analyze temporary files, browser caches, large files, duplicate files, and system resource usage before taking action.

Analyze before you act.

Español

Descripción

SILVIA PC Cleaner es una herramienta gratuita para Windows que permite analizar archivos temporales, caché de navegadores, archivos grandes, archivos duplicados y recursos del sistema.

Su filosofía es sencilla: analizar primero, revisar los resultados y permitir que el usuario decida qué acciones realizar.

Características

Análisis de archivos temporales de Windows y del usuario.

Búsqueda de caché conocida de Microsoft Edge, Google Chrome y Mozilla Firefox.

Identificación de archivos de miniaturas del Explorador de Windows.

Detección informativa de archivos grandes (100 MB o más).

Detección de duplicados mediante SHA-256.

Visualización del uso de memoria RAM y espacio en disco.

Envío de elementos seleccionados a la Papelera de reciclaje.

Vaciado de la Papelera mediante confirmación explícita.

Requisitos

Windows 10 o Windows 11.

Python 3.10 o superior para ejecutar desde el código fuente.

Conexión a Internet para instalar las dependencias.

Instalación desde el código fuente

Instala Python desde https://www.python.org/downloads/.

Abre PowerShell en la carpeta del proyecto.

Ejecuta:

py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py main.py

Seguridad y limitaciones

No requiere permisos de administrador.

No elimina archivos automáticamente.

Los archivos grandes y duplicados son informativos; no se eliminan desde estas funciones.

Los archivos de caché que estén en uso podrían no poder moverse.

No elimina carpetas completas de los navegadores.

La búsqueda de duplicados puede tardar y leer grandes cantidades de datos.

No modifica el Registro de Windows, servicios, controladores ni Windows Update.

Vaciar la Papelera puede ser irreversible. Revisa su contenido antes de confirmar.

English

Description

SILVIA PC Cleaner is a free Windows utility that analyzes temporary files, known browser cache locations, large files, duplicate files, and system resource usage.

Its approach is simple: analyze first, review the results, and let the user decide what actions to take.

Features

Windows and user temporary file analysis.

Known cache detection for Microsoft Edge, Google Chrome, and Mozilla Firefox.

Windows Explorer thumbnail file identification.

Informational detection of large files (100 MB or more).

Duplicate file detection using SHA-256.

RAM and disk usage information.

Sending selected items to the Recycle Bin.

Recycle Bin emptying with explicit confirmation.

Requirements

Windows 10 or Windows 11.

Python 3.10 or later to run from source.

Internet connection to install dependencies.

Installation

Follow the installation instructions in the Spanish section above, using the same commands.

Security and limitations

Administrator privileges are not required.

Files are not deleted automatically.

Large and duplicate files are informational only.

Files currently in use may not be movable.

Browser cache folders are not deleted entirely.

Duplicate scanning may take time and read substantial amounts of data.

The application does not modify the Windows Registry, services, drivers, or Windows Update.

Emptying the Recycle Bin may be irreversible. Review its contents before confirming.

Technology

Python

psutil

Send2Trash

PyInstaller

License

This project is distributed under the MIT License. See LICENSE for details.

Privacy

See PRIVACY.md for information about the application's privacy practices.

Project information

Project: SILVIA PC Cleaner
Version: 1.0
Developer: Joselyn Pesantez Polanco
Brand: SILVIA PRO®
Website: https://www.silviapro.com

© 2026 SILVIA PRO. All rights reserved.