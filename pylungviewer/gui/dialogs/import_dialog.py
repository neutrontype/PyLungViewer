#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Диалог импорта DICOM файлов для приложения PyLungViewer.
"""

import os
import logging
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
    QFileDialog, QListWidget, QListWidgetItem, QLabel,
    QCheckBox, QRadioButton, QButtonGroup, QGroupBox,
    QComboBox, QProgressBar, QMessageBox
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
        
        self.selected_files = []
        self.recursive_search = True
        
        # Инициализация UI
        self._init_ui()
        
        logger.info("Диалог импорта DICOM инициализирован")
    
    def _init_ui(self):
        """Инициализация пользовательского интерфейса."""
        # Настройка диалога
        self.setWindowTitle("Импорт DICOM")
        self.setMinimumSize(600, 400)
        
        # Главный layout
        main_layout = QVBoxLayout(self)
        
        # Верхняя панель выбора источника
        source_group = QGroupBox("Источник данных")
        source_layout = QVBoxLayout(source_group)
        
        # Опции источника
        self.source_local = QRadioButton("Локальное устройство")
        self.source_local.setChecked(True)
        self.source_local.toggled.connect(self._on_source_changed)
        
        self.source_dicomdir = QRadioButton("DICOMDIR")
        self.source_dicomdir.toggled.connect(self._on_source_changed)
        
        self.source_pacs = QRadioButton("DICOM PACS")
        self.source_pacs.toggled.connect(self._on_source_changed)
        
        # Группа для радиокнопок
        self.source_group = QButtonGroup()
        self.source_group.addButton(self.source_local)
        self.source_group.addButton(self.source_dicomdir)
        self.source_group.addButton(self.source_pacs)
        
        source_layout.addWidget(self.source_local)
        source_layout.addWidget(self.source_dicomdir)
        source_layout.addWidget(self.source_pacs)
        
        main_layout.addWidget(source_group)
        
        # Панель выбора файлов
        file_selection_layout = QHBoxLayout()
        
        # Поле для отображения пути
        self.path_label = QLabel("Выберите файлы или директорию")
        file_selection_layout.addWidget(self.path_label, 1)
        
        # Кнопка выбора файлов
        self.browse_btn = QPushButton("Обзор...")
        self.browse_btn.clicked.connect(self._on_browse)
        file_selection_layout.addWidget(self.browse_btn)
        
        main_layout.addLayout(file_selection_layout)
        
        # Список файлов
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        main_layout.addWidget(self.file_list, 1)  # 1 - растягивается по вертикали
        
        # Опции импорта
        options_layout = QHBoxLayout()
        
        # Чекбокс для рекурсивного поиска
        self.recursive_check = QCheckBox("Рекурсивный поиск в поддиректориях")
        self.recursive_check.setChecked(True)
        self.recursive_check.stateChanged.connect(self._on_recursive_changed)
        options_layout.addWidget(self.recursive_check)
        
        # Режим открытия
        options_layout.addWidget(QLabel("Открыть:"))
        self.open_mode_combo = QComboBox()
        self.open_mode_combo.addItems(["Только первый пациент", "Все пациенты", "По одному"])
        options_layout.addWidget(self.open_mode_combo)
        
        main_layout.addLayout(options_layout)
        
        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        
        # Кнопки действий
        buttons_layout = QHBoxLayout()
        
        self.import_btn = QPushButton("Импорт")
        self.import_btn.clicked.connect(self.accept)
        
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.import_btn)
        buttons_layout.addWidget(self.cancel_btn)
        
        main_layout.addLayout(buttons_layout)
    
    def _on_source_changed(self, checked):
        """
        Обработчик изменения источника DICOM.
        
        Args:
            checked: Флаг состояния переключателя.
        """
        if not checked:
            return
        
        # Очищаем список файлов
        self.file_list.clear()
        self.selected_files = []
        
        # Обновляем интерфейс в зависимости от выбранного источника
        if self.source_local.isChecked():
            self.path_label.setText("Выберите файлы или директорию")
            self.recursive_check.setEnabled(True)
        elif self.source_dicomdir.isChecked():
            self.path_label.setText("Выберите файл DICOMDIR")
            self.recursive_check.setEnabled(False)
        elif self.source_pacs.isChecked():
            self.path_label.setText("Настройки PACS")
            self.recursive_check.setEnabled(False)
            # Здесь можно добавить дополнительные поля для настройки PACS
    
    def _on_recursive_changed(self, state):
        """
        Обработчик изменения флага рекурсивного поиска.
        
        Args:
            state: Новое состояние флага.
        """
        self.recursive_search = state == Qt.Checked
    
    def _on_browse(self):
        """Обработчик кнопки выбора файлов/директорий."""
        # Определяем режим выбора в зависимости от выбранного источника
        if self.source_local.isChecked():
            # Выбор файлов или директории
            file_paths, _ = QFileDialog.getOpenFileNames(
                self, "Выберите DICOM файлы", 
                os.path.expanduser("~"),
                "DICOM Files (*.dcm *.dicom *.dic);;All Files (*)"
            )
            
            if not file_paths:
                # Если файлы не выбраны, предлагаем выбрать директорию
                dir_path = QFileDialog.getExistingDirectory(
                    self, "Выберите директорию с DICOM файлами", 
                    os.path.expanduser("~"),
                    QFileDialog.ShowDirsOnly
                )
                
                if dir_path:
                    file_paths = [dir_path]
        
        elif self.source_dicomdir.isChecked():
            # Выбор файла DICOMDIR
            file_paths, _ = QFileDialog.getOpenFileNames(
                self, "Выберите файл DICOMDIR", 
                os.path.expanduser("~"),
                "DICOMDIR Files (DICOMDIR);;All Files (*)"
            )
            
            # Если ничего не выбрано, пробуем поискать DICOMDIR в директории
            if not file_paths:
                dir_path = QFileDialog.getExistingDirectory(
                    self, "Выберите директорию с DICOMDIR", 
                    os.path.expanduser("~"),
                    QFileDialog.ShowDirsOnly
                )
                
                if dir_path:
                    # Проверяем наличие файла DICOMDIR в выбранной директории
                    dicomdir_path = os.path.join(dir_path, "DICOMDIR")
                    if os.path.exists(dicomdir_path):
                        file_paths = [dicomdir_path]
        
        elif self.source_pacs.isChecked():
            # Здесь будет логика для работы с PACS
            # Пока просто показываем сообщение
            QMessageBox.information(
                self, "PACS", 
                "Функция импорта из PACS находится в разработке."
            )
            return
        
        if not file_paths:
            return
        
        # Обновляем выбранные файлы
        self.selected_files = file_paths
        
        # Обновляем метку пути и список файлов
        if len(file_paths) == 1:
            self.path_label.setText(file_paths[0])
        else:
            self.path_label.setText(f"Выбрано {len(file_paths)} файлов")
        
        # Очищаем и заполняем список файлов
        self.file_list.clear()
        for path in file_paths:
            item = QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            self.file_list.addItem(item)
    
    def get_selected_files(self):
        """
        Получение списка выбранных файлов.
        
        Returns:
            list: Список путей к выбранным файлам.
        """
        return self.selected_files
    
    def get_recursive_search(self):
        """
        Получение флага рекурсивного поиска.
        
        Returns:
            bool: True, если выбран рекурсивный поиск.
        """
        return self.recursive_search
    
    def get_open_mode(self):
        """
        Получение выбранного режима открытия.
        
        Returns:
            str: Выбранный режим открытия.
        """
        return self.open_mode_combo.currentText()