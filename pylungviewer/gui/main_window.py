#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Главное окно приложения PyLungViewer.
(Версия с интеграцией сегментации - Исправлены ошибки)
"""

import os
import logging
from PyQt5.QtWidgets import (
    QMainWindow, QDockWidget, QAction, QToolBar,
    QSplitter, QFileDialog, QMessageBox, QLabel,
    QStatusBar, QVBoxLayout, QWidget, QProgressBar,
    QApplication # <--- Добавлен импорт QApplication
)
from PyQt5.QtCore import Qt, QSettings, QSize
from PyQt5.QtGui import QIcon

# Импорт модулей приложения
from pylungviewer.gui.viewer_panel import ViewerPanel, SEGMENTATION_AVAILABLE # Импортируем флаг доступности
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
        # --- Файловые действия ---
        self.import_action = QAction("Импорт DICOM", self)
        # self.import_action.setIcon(QIcon("pylungviewer/resources/icons/import.png")) # Укажите путь к иконкам
        self.import_action.setStatusTip("Импортировать DICOM файлы или директорию")
        self.import_action.triggered.connect(self._on_import_dicom)

        self.load_model_action = QAction("Загрузить модель сегментации", self)
        # self.load_model_action.setIcon(QIcon("pylungviewer/resources/icons/model.png")) # Иконка модели
        self.load_model_action.setStatusTip("Загрузить файл .pth модели сегментации")
        self.load_model_action.triggered.connect(self._on_load_model)
        # Делаем неактивным, если сегментация в принципе недоступна
        self.load_model_action.setEnabled(SEGMENTATION_AVAILABLE)
        if not SEGMENTATION_AVAILABLE:
            self.load_model_action.setToolTip("Модуль сегментации или его зависимости не найдены")


        self.export_action = QAction("Экспорт DICOM", self)
        # self.export_action.setIcon(QIcon("pylungviewer/resources/icons/export.png"))
        self.export_action.setStatusTip("Экспортировать DICOM файлы (не реализовано)")
        self.export_action.triggered.connect(self._on_export_dicom)
        self.export_action.setEnabled(False) # Пока не реализовано

        self.exit_action = QAction("Выход", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.setStatusTip("Выйти из приложения")
        self.exit_action.triggered.connect(self.close)

        # --- Действия для работы с просмотром ---
        self.zoom_in_action = QAction("Увеличить", self)
        # self.zoom_in_action.setIcon(QIcon("pylungviewer/resources/icons/zoom_in.png"))
        self.zoom_in_action.setShortcut("Ctrl++")
        self.zoom_in_action.setStatusTip("Увеличить изображение")
        self.zoom_in_action.triggered.connect(self._on_zoom_in)

        self.zoom_out_action = QAction("Уменьшить", self)
        # self.zoom_out_action.setIcon(QIcon("pylungviewer/resources/icons/zoom_out.png"))
        self.zoom_out_action.setShortcut("Ctrl+-")
        self.zoom_out_action.setStatusTip("Уменьшить изображение")
        self.zoom_out_action.triggered.connect(self._on_zoom_out)

        self.reset_view_action = QAction("Сбросить вид", self)
        # self.reset_view_action.setIcon(QIcon("pylungviewer/resources/icons/reset_view.png"))
        self.reset_view_action.setShortcut("Ctrl+0")
        self.reset_view_action.setStatusTip("Сбросить масштаб и положение")
        self.reset_view_action.triggered.connect(self._on_reset_view)

        # --- Действия для инструментов ---
        self.segment_action = QAction("Сегментировать текущий срез", self)
        # self.segment_action.setIcon(QIcon("pylungviewer/resources/icons/segment.png")) # Иконка сегментации
        self.segment_action.setStatusTip("Выполнить сегментацию легких на текущем срезе")
        self.segment_action.triggered.connect(self._on_segment_slice)
        # Изначально неактивно, и неактивно если сегментация недоступна
        self.segment_action.setEnabled(False)
        if not SEGMENTATION_AVAILABLE:
             self.segment_action.setToolTip("Модуль сегментации или его зависимости не найдены")


    def _create_menus(self):
        """Создание меню приложения."""
        # Меню "Файл"
        self.file_menu = self.menuBar().addMenu("Файл")
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.load_model_action) # Добавляем загрузку модели
        self.file_menu.addAction(self.export_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)

        # Меню "Вид"
        self.view_menu = self.menuBar().addMenu("Вид")
        self.view_menu.addAction(self.zoom_in_action)
        self.view_menu.addAction(self.zoom_out_action)
        self.view_menu.addAction(self.reset_view_action)

        # Меню "Инструменты"
        self.tools_menu = self.menuBar().addMenu("Инструменты")
        self.tools_menu.addAction(self.segment_action) # Добавляем сегментацию

        # Меню "Справка"
        self.help_menu = self.menuBar().addMenu("Справка")
        # Можно добавить действие "О программе"

    def _create_toolbar(self):
        """Создание панели инструментов."""
        self.main_toolbar = QToolBar("Основная панель", self)
        self.main_toolbar.setMovable(False)
        self.main_toolbar.setIconSize(QSize(24, 24)) # Уменьшим размер иконок
        self.addToolBar(Qt.TopToolBarArea, self.main_toolbar)

        # Добавление действий на панель инструментов
        self.main_toolbar.addAction(self.import_action)
        self.main_toolbar.addAction(self.load_model_action) # Кнопка загрузки модели
        # self.main_toolbar.addAction(self.export_action) # Экспорт пока уберем
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(self.zoom_in_action)
        self.main_toolbar.addAction(self.zoom_out_action)
        self.main_toolbar.addAction(self.reset_view_action)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(self.segment_action) # Кнопка сегментации


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
            import_dialog = DicomImportDialog(self)
            if import_dialog.exec_():
                selected_files = import_dialog.get_selected_files()
                recursive_search = import_dialog.get_recursive_search()

                if selected_files:
                    self.progress_bar.setVisible(True)
                    self.progress_bar.setValue(0)
                    self.status_label.setText("Загрузка DICOM файлов...")
                    # Очищаем кэш перед новой загрузкой
                    self.dicom_loader.clear_cache()
                    self.dicom_loader.load_files(selected_files, recursive_search)
        except Exception as e:
            logger.error(f"Ошибка при импорте DICOM: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка импорта",
                               f"Произошла ошибка при импорте DICOM файлов: {str(e)}")
            self.status_label.setText("Ошибка при импорте DICOM")
            self.progress_bar.setVisible(False)

    def _on_load_model(self):
        """Обработчик загрузки файла модели сегментации."""
        # Проверяем доступность сегментации перед открытием диалога
        if not SEGMENTATION_AVAILABLE:
            QMessageBox.warning(self, "Сегментация недоступна",
                                "Модуль сегментации или его зависимости (например, PyTorch, segmentation-models-pytorch) не найдены.")
            return

        model_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл модели PyTorch",
            self.settings.value("Paths/last_model_dir", os.path.expanduser("~")), # Запоминаем последнюю директорию
            "PyTorch Model Files (*.pth *.pt);;All Files (*)"
        )
        if model_path:
            self.status_label.setText(f"Загрузка модели сегментации: {os.path.basename(model_path)}...")
            QApplication.setOverrideCursor(Qt.WaitCursor) # Используем импортированный QApplication
            try:
                # Передаем путь в ViewerPanel для загрузки
                # Убедимся, что segmenter существует перед вызовом
                if self.viewer_panel.segmenter:
                    self.viewer_panel.load_segmentation_model(model_path)
                    # Сохраняем директорию
                    self.settings.setValue("Paths/last_model_dir", os.path.dirname(model_path))
                    self.status_label.setText(f"Модель сегментации загружена: {os.path.basename(model_path)}")
                    # Активируем кнопку сегментации, если серия уже выбрана
                    self.segment_action.setEnabled(self.viewer_panel.current_series is not None)
                else:
                     # Эта ветка не должна сработать, если SEGMENTATION_AVAILABLE=True, но на всякий случай
                     logger.error("Объект segmenter не инициализирован в ViewerPanel.")
                     QMessageBox.critical(self, "Внутренняя ошибка", "Объект сегментатора не найден.")
                     self.status_label.setText("Ошибка загрузки модели")

            except Exception as e:
                 logger.error(f"Ошибка при загрузке модели: {e}", exc_info=True)
                 QMessageBox.critical(self, "Ошибка загрузки модели",
                                    f"Не удалось загрузить модель: {str(e)}")
                 self.status_label.setText("Ошибка загрузки модели")
            finally:
                QApplication.restoreOverrideCursor() # Используем импортированный QApplication


    def _on_loading_progress(self, current, total):
        """ Обработчик прогресса загрузки DICOM файлов. """
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self.status_label.setText(f"Загрузка DICOM файлов... ({current}/{total})")

    def _on_loading_complete(self, studies):
        """ Обработчик завершения загрузки DICOM файлов. """
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Загружено {len(studies)} исследований")
        self.sidebar_panel.set_studies(studies)
        # Сбрасываем панель просмотра
        self.viewer_panel._show_placeholder()
        # Деактивируем кнопку сегментации, т.к. серия еще не выбрана
        self.segment_action.setEnabled(False)
        logger.info(f"Загружено {len(studies)} исследований")

    def _on_loading_error(self, error_message):
        """ Обработчик ошибки загрузки DICOM файлов. """
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "Ошибка импорта", error_message)
        self.status_label.setText("Ошибка при импорте DICOM")

    def _on_series_selected(self, series_data):
        """ Обработчик выбора серии DICOM. """
        logger.info(f"Выбрана серия для отображения: {series_data.get('description', 'Неизвестно')}")
        self.viewer_panel.load_series(series_data)
        # Активируем кнопку сегментации, если сегментация доступна,
        # сегментатор создан И модель в нем загружена
        segmenter_ready = (
            SEGMENTATION_AVAILABLE and
            self.viewer_panel.segmenter is not None and
            self.viewer_panel.segmenter.model is not None
        )
        self.segment_action.setEnabled(segmenter_ready)


    def _on_export_dicom(self):
        """ Обработчик экспорта DICOM файлов (заглушка). """
        QMessageBox.information(self, "Экспорт DICOM", "Функция экспорта пока не реализована.")

    def _on_zoom_in(self):
        """ Обработчик увеличения масштаба. """
        if hasattr(self.viewer_panel, 'view_box'): # Проверяем наличие view_box
            self.viewer_panel.view_box.scaleBy((1.2, 1.2))
            logger.debug("Zoom In")

    def _on_zoom_out(self):
        """ Обработчик уменьшения масштаба. """
        if hasattr(self.viewer_panel, 'view_box'): # Проверяем наличие view_box
            self.viewer_panel.view_box.scaleBy((1/1.2, 1/1.2))
            logger.debug("Zoom Out")

    def _on_reset_view(self):
        """ Обработчик сброса вида. """
        if hasattr(self.viewer_panel, 'view_box'): # Проверяем наличие view_box
            self.viewer_panel.view_box.autoRange()
            logger.debug("Reset View")

    def _on_segment_slice(self):
        """ Обработчик запроса сегментации текущего среза. """
        # Проверяем доступность перед вызовом
        if SEGMENTATION_AVAILABLE and hasattr(self.viewer_panel, 'run_segmentation'):
            self.viewer_panel.run_segmentation() # Вызываем метод в ViewerPanel
        else:
            logger.warning("Попытка сегментации, но функция недоступна.")
            QMessageBox.warning(self, "Сегментация недоступна",
                                "Функция сегментации недоступна. Проверьте логи.")


    def closeEvent(self, event):
        """ Обработчик события закрытия окна. """
        self._save_window_settings()
        super().closeEvent(event)
