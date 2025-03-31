#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Главное окно приложения PyLungViewer.
"""

import os
import logging
from PyQt5.QtWidgets import (
    QMainWindow, QDockWidget, QAction, QToolBar, 
    QSplitter, QFileDialog, QMessageBox, QLabel,
    QStatusBar, QVBoxLayout, QWidget, QProgressBar
)
from PyQt5.QtCore import Qt, QSettings, QSize
from PyQt5.QtGui import QIcon

# Импорт модулей приложения
from pylungviewer.gui.viewer_panel import ViewerPanel
from pylungviewer.gui.sidebar import SidebarPanel
from pylungviewer.gui.dialogs.import_dialog import DicomImportDialog
from pylungviewer.core.dicom_loader import DicomLoader

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Главное окно приложения PyLungViewer."""
    
    def __init__(self, settings: QSettings, parent=None):
        """
        Инициализация главного окна.
        
        Args:
            settings: Настройки приложения.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self.settings = settings
        self.dicom_loader = DicomLoader(settings)
        
        # Подключаем сигналы загрузчика DICOM
        self.dicom_loader.loading_complete.connect(self._on_loading_complete)
        self.dicom_loader.loading_error.connect(self._on_loading_error)
        self.dicom_loader.loading_progress.connect(self._on_loading_progress)
        
        # Инициализация UI
        self._init_ui()
        
        # Загрузка настроек окна
        self._load_window_settings()
        
        logger.info("Главное окно инициализировано")
    
    def _init_ui(self):
        """Инициализация пользовательского интерфейса."""
        # Настройка главного окна
        self.setWindowTitle("PyLungViewer - Анализатор КТ снимков лёгких")
        self.setMinimumSize(1024, 768)
        
        # Создание центрального виджета
        self.central_widget = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.central_widget)
        
        # Создание основных панелей
        self.viewer_panel = ViewerPanel(self)
        self.sidebar_panel = SidebarPanel(self)
        
        # Подключаем сигналы боковой панели
        self.sidebar_panel.series_selected.connect(self._on_series_selected)
        
        # Добавление панелей в splitter
        self.central_widget.addWidget(self.sidebar_panel)
        self.central_widget.addWidget(self.viewer_panel)
        self.central_widget.setStretchFactor(0, 1)  # Боковая панель
        self.central_widget.setStretchFactor(1, 4)  # Панель просмотра
        
        # Создание меню и панели инструментов
        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        
        # Создание строки состояния
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Готово")
        self.status_bar.addWidget(self.status_label, 1)
        
        # Добавляем прогресс-бар в статус-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
    
    def _create_actions(self):
        """Создание действий для меню и панели инструментов."""
        # Основные действия файлового меню
        self.import_action = QAction("Импорт DICOM", self)
        self.import_action.setIcon(QIcon("pylungviewer/resources/icons/import.png"))
        self.import_action.setStatusTip("Импортировать DICOM файлы")
        self.import_action.triggered.connect(self._on_import_dicom)
        
        self.export_action = QAction("Экспорт DICOM", self)
        self.export_action.setIcon(QIcon("pylungviewer/resources/icons/export.png"))
        self.export_action.setStatusTip("Экспортировать DICOM файлы")
        self.export_action.triggered.connect(self._on_export_dicom)
        
        self.exit_action = QAction("Выход", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.setStatusTip("Выйти из приложения")
        self.exit_action.triggered.connect(self.close)
        
        # Действия для работы с просмотром
        self.zoom_in_action = QAction("Увеличить", self)
        self.zoom_in_action.setShortcut("Ctrl++")
        self.zoom_in_action.setStatusTip("Увеличить изображение")
        self.zoom_in_action.triggered.connect(self._on_zoom_in)
        
        self.zoom_out_action = QAction("Уменьшить", self)
        self.zoom_out_action.setShortcut("Ctrl+-")
        self.zoom_out_action.setStatusTip("Уменьшить изображение")
        self.zoom_out_action.triggered.connect(self._on_zoom_out)
        
        self.reset_view_action = QAction("Сбросить вид", self)
        self.reset_view_action.setShortcut("Ctrl+0")
        self.reset_view_action.setStatusTip("Сбросить масштаб и положение")
        self.reset_view_action.triggered.connect(self._on_reset_view)
    
    def _create_menus(self):
        """Создание меню приложения."""
        # Создание основных меню
        self.file_menu = self.menuBar().addMenu("Файл")
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.export_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)
        
        self.view_menu = self.menuBar().addMenu("Вид")
        self.view_menu.addAction(self.zoom_in_action)
        self.view_menu.addAction(self.zoom_out_action)
        self.view_menu.addAction(self.reset_view_action)
        
        self.tools_menu = self.menuBar().addMenu("Инструменты")
        self.help_menu = self.menuBar().addMenu("Справка")
    
    def _create_toolbar(self):
        """Создание панели инструментов."""
        # Основная панель инструментов
        self.main_toolbar = QToolBar("Основная панель", self)
        self.main_toolbar.setMovable(False)
        self.main_toolbar.setIconSize(QSize(32, 32))
        self.addToolBar(Qt.TopToolBarArea, self.main_toolbar)
        
        # Добавление действий на панель инструментов
        self.main_toolbar.addAction(self.import_action)
        self.main_toolbar.addAction(self.export_action)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(self.zoom_in_action)
        self.main_toolbar.addAction(self.zoom_out_action)
        self.main_toolbar.addAction(self.reset_view_action)
    
    def _load_window_settings(self):
        """Загрузка сохраненных настроек окна."""
        geometry = self.settings.value("MainWindow/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        
        state = self.settings.value("MainWindow/state")
        if state:
            self.restoreState(state)
        
        splitter_state = self.settings.value("MainWindow/splitter")
        if splitter_state:
            self.central_widget.restoreState(splitter_state)
    
    def _save_window_settings(self):
        """Сохранение настроек окна."""
        self.settings.setValue("MainWindow/geometry", self.saveGeometry())
        self.settings.setValue("MainWindow/state", self.saveState())
        self.settings.setValue("MainWindow/splitter", self.central_widget.saveState())
    
    def _on_import_dicom(self):
        """Обработчик импорта DICOM файлов."""
        try:
            # Показываем диалог импорта DICOM
            import_dialog = DicomImportDialog(self)
            if import_dialog.exec_():
                # Получаем выбранные файлы
                selected_files = import_dialog.get_selected_files()
                recursive_search = import_dialog.get_recursive_search()
                
                if selected_files:
                    # Отображаем прогресс-бар
                    self.progress_bar.setVisible(True)
                    self.progress_bar.setValue(0)
                    
                    # Обновляем статус
                    self.status_label.setText("Загрузка DICOM файлов...")
                    
                    # Запускаем загрузку файлов
                    self.dicom_loader.load_files(selected_files, recursive_search)
                    
                    # Обновление интерфейса произойдет через сигналы загрузчика
        except Exception as e:
            logger.error(f"Ошибка при импорте DICOM: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка импорта", 
                               f"Произошла ошибка при импорте DICOM файлов: {str(e)}")
            self.status_label.setText("Ошибка при импорте DICOM")
            self.progress_bar.setVisible(False)
    
    def _on_loading_progress(self, current, total):
        """
        Обработчик прогресса загрузки DICOM файлов.
        
        Args:
            current: Текущий прогресс.
            total: Общее количество файлов.
        """
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self.status_label.setText(f"Загрузка DICOM файлов... ({current}/{total})")
    
    def _on_loading_complete(self, studies):
        """
        Обработчик завершения загрузки DICOM файлов.
        
        Args:
            studies: Список загруженных исследований.
        """
        # Скрываем прогресс-бар
        self.progress_bar.setVisible(False)
        
        # Обновляем статус
        self.status_label.setText(f"Загружено {len(studies)} исследований")
        
        # Обновляем боковую панель со списком исследований
        self.sidebar_panel.set_studies(studies)
        logger.info(f"Загружено {len(studies)} исследований")
    
    def _on_loading_error(self, error_message):
        """
        Обработчик ошибки загрузки DICOM файлов.
        
        Args:
            error_message: Сообщение об ошибке.
        """
        # Скрываем прогресс-бар
        self.progress_bar.setVisible(False)
        
        # Показываем сообщение об ошибке
        QMessageBox.critical(self, "Ошибка импорта", error_message)
        self.status_label.setText("Ошибка при импорте DICOM")
    
    def _on_series_selected(self, series_data):
        """
        Обработчик выбора серии DICOM.
        
        Args:
            series_data: Данные выбранной серии.
        """
        logger.info(f"Выбрана серия для отображения: {series_data.get('description', 'Неизвестно')}")
        
        # Загружаем серию в просмотрщик
        self.viewer_panel.load_series(series_data)
    
    def _on_export_dicom(self):
        """Обработчик экспорта DICOM файлов."""
        # Получение директории для экспорта
        export_dir = QFileDialog.getExistingDirectory(
            self, "Выберите директорию для экспорта", 
            os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly
        )
        
        if not export_dir:
            return
        
        # Здесь будет логика экспорта файлов
        self.status_label.setText(f"Файлы экспортированы в {export_dir}")
        logger.info(f"Файлы экспортированы в директорию: {export_dir}")
    
    def _on_zoom_in(self):
        """Обработчик увеличения масштаба."""
        # Реализация увеличения масштаба через viewer_panel
        logger.info("Увеличение масштаба")
        # TODO: Добавить соответствующий метод в ViewerPanel
    
    def _on_zoom_out(self):
        """Обработчик уменьшения масштаба."""
        # Реализация уменьшения масштаба через viewer_panel
        logger.info("Уменьшение масштаба")
        # TODO: Добавить соответствующий метод в ViewerPanel
    
    def _on_reset_view(self):
        """Обработчик сброса вида."""
        # Реализация сброса вида через viewer_panel
        logger.info("Сброс вида")
        # TODO: Добавить соответствующий метод в ViewerPanel
    
    def closeEvent(self, event):
        """Обработчик события закрытия окна."""
        # Сохранение настроек перед выходом
        self._save_window_settings()
        super().closeEvent(event)