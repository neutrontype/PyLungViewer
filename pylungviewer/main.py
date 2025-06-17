


"""
PyLungViewer - Просмотрщик и анализатор КТ снимков легких.
Основной модуль запуска приложения.
"""

import sys
import os
import logging

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pylungviewer.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("pylungviewer")

def setup_app_path():
    home_dir = os.path.expanduser("~")
    app_dir = os.path.join(home_dir, ".pylungviewer")
    subdirs = ["config", "cache", "temp", "models"]
    for subdir in subdirs:
        dir_path = os.path.join(app_dir, subdir)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            logger.info(f"Создана директория: {dir_path}")
    return app_dir

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PyLungViewer")
    app.setOrganizationName("PyLungDev")
    app.setOrganizationDomain("pylungviewer.org")
    app_dir = setup_app_path()
    models_dir = os.path.join(app_dir, "models")
    settings = QSettings(
        os.path.join(app_dir, "config", "settings.ini"),
        QSettings.IniFormat
    )

    from pylungviewer.gui.main_window import MainWindow
    main_window = MainWindow(settings, models_dir=models_dir)
    main_window.show()
    return app.exec_()

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logger.exception(f"Неожиданная ошибка: {e}")
        raise
