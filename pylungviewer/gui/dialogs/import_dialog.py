#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Диалог импорта DICOM файлов для приложения PyLungViewer.
(Версия с раздельными кнопками и возможностью добавления нескольких источников)
"""

import os
import logging
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QLabel,
    QCheckBox, QRadioButton, QButtonGroup, QGroupBox,
    QComboBox, QProgressBar, QMessageBox, QSizePolicy # Добавлено QSizePolicy
)
from PyQt5.QtCore import Qt, QSize, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QIcon

logger = logging.getLogger(__name__)


class DicomImportDialog(QDialog):
    """Диалог для импорта DICOM файлов."""

    def __init__(self, parent=None):
        """
        Инициализация диалога импорта.

        Args:
            parent: Родительский виджет.
        """
        super().__init__(parent)

        # --- Изменено: Храним список путей в очереди ---
        self.queued_paths = []
        # ---------------------------------------------
        self.recursive_search = True # Глобальная настройка для папок

        # Инициализация UI
        self._init_ui()

        logger.info("Диалог импорта DICOM инициализирован")

    def _init_ui(self):
        """Инициализация пользовательского интерфейса."""
        self.setWindowTitle("Импорт DICOM")
        self.setMinimumSize(600, 450) # Немного увеличим высоту
        main_layout = QVBoxLayout(self)

        # --- Панель кнопок выбора ---
        button_panel_layout = QHBoxLayout()
        self.browse_files_btn = QPushButton("Добавить файлы...")
        self.browse_files_btn.setToolTip("Выбрать отдельные DICOM файлы")
        self.browse_files_btn.clicked.connect(self._on_browse_files)
        button_panel_layout.addWidget(self.browse_files_btn)

        self.browse_folder_btn = QPushButton("Добавить папку...")
        self.browse_folder_btn.setToolTip("Выбрать папку с DICOM файлами")
        self.browse_folder_btn.clicked.connect(self._on_browse_folder)
        button_panel_layout.addWidget(self.browse_folder_btn)

        # --- Добавлена кнопка DICOMDIR ---
        self.browse_dicomdir_btn = QPushButton("Добавить DICOMDIR...")
        self.browse_dicomdir_btn.setToolTip("Выбрать файл DICOMDIR")
        self.browse_dicomdir_btn.clicked.connect(self._on_browse_dicomdir)
        button_panel_layout.addWidget(self.browse_dicomdir_btn)
        # ---------------------------------

        button_panel_layout.addStretch(1)
        main_layout.addLayout(button_panel_layout)
        # ------------------------------------

        # Метка для списка
        self.path_label = QLabel("Элементы для импорта:") # Изменен текст
        main_layout.addWidget(self.path_label)

        # Список файлов/папок в очереди
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection) # Позволяем выбирать несколько для удаления
        main_layout.addWidget(self.file_list, 1)

        # --- Панель удаления и опций ---
        options_layout = QHBoxLayout()

        # Кнопка удаления выбранного из списка
        self.remove_btn = QPushButton("Удалить выбранное")
        self.remove_btn.setToolTip("Удалить выделенные элементы из списка выше")
        self.remove_btn.clicked.connect(self._remove_selected_items)
        options_layout.addWidget(self.remove_btn)
        options_layout.addStretch(1) # Промежуток

        # Чекбокс рекурсии (теперь глобальный для всех добавляемых папок)
        self.recursive_check = QCheckBox("Рекурсивный поиск в папках")
        self.recursive_check.setChecked(True)
        self.recursive_check.setToolTip("Искать DICOM файлы во всех вложенных папках при добавлении папки")
        self.recursive_check.stateChanged.connect(self._on_recursive_changed)
        options_layout.addWidget(self.recursive_check)

        main_layout.addLayout(options_layout)
        # ------------------------------------

        # Прогресс-бар (оставляем)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Кнопки действий (оставляем)
        buttons_layout = QHBoxLayout()
        self.import_btn = QPushButton("Импорт")
        self.import_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.import_btn)
        buttons_layout.addWidget(self.cancel_btn)
        main_layout.addLayout(buttons_layout)

    def _on_recursive_changed(self, state):
        """ Обработчик изменения флага рекурсивного поиска. """
        self.recursive_search = state == Qt.Checked

    def _add_paths_to_queue(self, paths):
        """Добавляет пути в очередь и в список виджета, избегая дубликатов."""
        added_count = 0
        for path in paths:
            # Нормализуем путь для сравнения
            normalized_path = os.path.normpath(path)
            if normalized_path not in self.queued_paths:
                self.queued_paths.append(normalized_path)
                # Добавляем в QListWidget
                item = QListWidgetItem(normalized_path) # Показываем полный путь для ясности
                item.setToolTip(normalized_path)
                self.file_list.addItem(item)
                added_count += 1
            else:
                logger.debug(f"Путь уже в очереди: {normalized_path}")
        if added_count > 0:
             logger.info(f"Добавлено {added_count} новых элементов в очередь импорта.")


    def _on_browse_files(self):
        """ Обработчик кнопки выбора файлов. """
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите DICOM файлы",
            self.settings.value("Paths/last_import_dir", os.path.expanduser("~")),
            "DICOM Files (*.dcm *.dicom *.dic *.ima);;All Files (*)"
        )
        if file_paths:
            self._add_paths_to_queue(file_paths)
            # Сохраняем директорию первого файла
            self.settings.setValue("Paths/last_import_dir", os.path.dirname(file_paths[0]))

    def _on_browse_folder(self):
        """ Обработчик кнопки выбора папки. """
        dir_path = QFileDialog.getExistingDirectory(
            self, "Выберите директорию с DICOM файлами",
             self.settings.value("Paths/last_import_dir", os.path.expanduser("~")),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if dir_path:
            self._add_paths_to_queue([dir_path])
            # Сохраняем выбранную директорию
            self.settings.setValue("Paths/last_import_dir", dir_path)

    def _on_browse_dicomdir(self):
        """ Обработчик кнопки выбора файла DICOMDIR. """
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл DICOMDIR",
            self.settings.value("Paths/last_import_dir", os.path.expanduser("~")),
            "DICOMDIR Files (DICOMDIR);;All Files (*)"
        )
        if file_path and os.path.basename(file_path).upper() == "DICOMDIR":
            self._add_paths_to_queue([file_path])
            # Сохраняем директорию файла
            self.settings.setValue("Paths/last_import_dir", os.path.dirname(file_path))
        elif file_path:
             QMessageBox.warning(self, "Неверный файл", "Выбранный файл не является файлом DICOMDIR.")


    def _remove_selected_items(self):
        """ Удаляет выбранные элементы из очереди и списка. """
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            return

        removed_paths = []
        for item in selected_items:
            row = self.file_list.row(item)
            removed_item = self.file_list.takeItem(row) # Удаляем из виджета
            removed_path = removed_item.text() # Получаем путь из текста элемента
            removed_paths.append(removed_path)
            logger.debug(f"Удален элемент из списка: {removed_path}")

        # Удаляем соответствующие пути из self.queued_paths
        self.queued_paths = [p for p in self.queued_paths if p not in removed_paths]
        logger.info(f"Удалено {len(removed_paths)} элементов из очереди импорта.")


    def get_selected_files(self):
        """ Получение списка путей из очереди для импорта. """
        return self.queued_paths # Возвращаем весь список очереди

    def get_recursive_search(self):
        """ Получение глобального флага рекурсивного поиска для папок. """
        return self.recursive_check.isChecked()

    @property
    def settings(self):
        if self.parent() and hasattr(self.parent(), 'settings'):
            return self.parent().settings
        else:
            from PyQt5.QtCore import QSettings
            logger.warning("Не удалось получить QSettings от родителя в DicomImportDialog.")
            return QSettings()