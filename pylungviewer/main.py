#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
PyLungViewer - Просмотрщик и анализатор КТ снимков легких.
Основной модуль запуска приложения.
"""

import sys
import os
import logging

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings

# Настройка логирования
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
    """Настраивает пути приложения и создает директории, если необходимо."""
    # Получение домашней директории пользователя
    home_dir = os.path.expanduser("~")
    app_dir = os.path.join(home_dir, ".pylungviewer")

    # Создание директорий, если они не существуют
    # Добавляем 'models' в список создаваемых директорий
    subdirs = ["config", "cache", "temp", "models"]
    for subdir in subdirs:
        dir_path = os.path.join(app_dir, subdir)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            logger.info(f"Создана директория: {dir_path}")

    return app_dir


def main():
    """Основная функция запуска приложения."""
    # Инициализация QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("PyLungViewer")
    app.setOrganizationName("PyLungDev")
    app.setOrganizationDomain("pylungviewer.org")

    # Настройка путей приложения
    app_dir = setup_app_path()
    # Определяем путь к папке моделей
    models_dir = os.path.join(app_dir, "models")
    # Здесь вы можете добавить логику для поиска конкретного файла модели,
    # например, искать первый файл с расширением .pth или .pt в этой папке.
    # Пока просто передаем путь к папке. Логика выбора файла будет в ViewerPanel.

    # Настройка параметров QSettings
    settings = QSettings(
        os.path.join(app_dir, "config", "settings.ini"),
        QSettings.IniFormat
    )

    # Импортируем здесь, чтобы избежать циклических импортов
    from pylungviewer.gui.main_window import MainWindow

    # Создание и отображение главного окна приложения
    # Передаем путь к папке моделей в главное окно
    main_window = MainWindow(settings, models_dir=models_dir)
    main_window.show()

    # Запуск цикла обработки событий
    return app.exec_()


if __name__ == "__main__":
    try:
        # Запуск приложения
        sys.exit(main())
    except Exception as e:
        logger.exception(f"Неожиданная ошибка: {e}")
        raise
