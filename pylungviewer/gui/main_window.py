#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Главное окно приложения PyLungViewer.
(Версия с передачей viewer_panel в sidebar)
"""

import os
import logging
from PyQt5.QtWidgets import (
    QMainWindow, QDockWidget, QAction, QToolBar,
    QSplitter, QFileDialog, QMessageBox, QLabel,
    QStatusBar, QVBoxLayout, QWidget, QProgressBar,
    QApplication
)
from PyQt5.QtCore import Qt, QSettings, QSize, pyqtSlot, QTimer
from PyQt5.QtGui import QIcon

# Импорт модулей приложения
from pylungviewer.gui.viewer_panel import ViewerPanel, SEGMENTATION_AVAILABLE
from pylungviewer.gui.sidebar import SidebarPanel
from pylungviewer.gui.dialogs.import_dialog import DicomImportDialog
from pylungviewer.core.dicom_loader import DicomLoader

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Главное окно приложения PyLungViewer."""

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.dicom_loader = DicomLoader(settings)
        self.dicom_loader.loading_complete.connect(self._on_loading_complete)
        self.dicom_loader.loading_error.connect(self._on_loading_error)
        self.dicom_loader.loading_progress.connect(self._on_loading_progress)
        self._init_ui()
        self._load_window_settings()
        logger.info("Главное окно инициализировано")

    def _init_ui(self):
        self.setWindowTitle("🤬")
        self.setMinimumSize(1024, 768)
        self.central_widget = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.central_widget)

        # --- Сначала создаем viewer_panel ---
        self.viewer_panel = ViewerPanel(self)
        # --- Потом создаем sidebar_panel, передавая viewer_panel ---
        self.sidebar_panel = SidebarPanel(viewer_panel=self.viewer_panel, parent=self)
        # ----------------------------------------------------------

        self.sidebar_panel.series_selected.connect(self._on_series_selected)
        self.sidebar_panel.export_progress.connect(self._on_export_progress)
        self.sidebar_panel.export_status_update.connect(self._update_status_bar)
        self.sidebar_panel.study_removed_from_view.connect(self._on_study_removed)

        self.viewer_panel.segmentation_progress.connect(self._on_segmentation_progress)
        self.viewer_panel.segmentation_status_update.connect(self._update_status_bar)

        self.central_widget.addWidget(self.sidebar_panel)
        self.central_widget.addWidget(self.viewer_panel)
        self.central_widget.setStretchFactor(0, 1)
        self.central_widget.setStretchFactor(1, 4)
        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Готово")
        self.status_bar.addWidget(self.status_label, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

    # ... (остальные методы без изменений) ...
    def _create_actions(self):
        # --- Файловые действия ---
        self.import_action = QAction("Импорт DICOM", self)
        self.import_action.setStatusTip("Импортировать DICOM файлы или директорию")
        self.import_action.triggered.connect(self._on_import_dicom)

        self.load_model_action = QAction("Загрузить модель", self) # Укоротил
        self.load_model_action.setStatusTip("Загрузить файл .pth модели сегментации")
        self.load_model_action.triggered.connect(self._on_load_model)
        self.load_model_action.setEnabled(SEGMENTATION_AVAILABLE)
        if not SEGMENTATION_AVAILABLE:
            self.load_model_action.setToolTip("Модуль сегментации или его зависимости не найдены")

        self.exit_action = QAction("Выход", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)

        # --- Действия для работы с просмотром ---
        self.zoom_in_action = QAction("Уменьшить", self)
        self.zoom_in_action.setShortcut("Ctrl++")
        self.zoom_in_action.triggered.connect(self._on_zoom_in)
        self.zoom_out_action = QAction("Увеличить", self)
        self.zoom_out_action.setShortcut("Ctrl+-")
        self.zoom_out_action.triggered.connect(self._on_zoom_out)
        self.reset_view_action = QAction("Сбросить вид", self)
        self.reset_view_action.setShortcut("Ctrl+0")
        self.reset_view_action.triggered.connect(self._on_reset_view)

        # --- Действия для инструментов ---
        self.segment_slice_action = QAction("Сегм. срез", self) # Укоротил
        self.segment_slice_action.setStatusTip("Выполнить сегментацию только для текущего среза")
        self.segment_slice_action.triggered.connect(self._on_segment_slice)
        self.segment_slice_action.setEnabled(False)

        self.segment_volume_action = QAction("Сегм. весь объем", self) # Новое действие
        self.segment_volume_action.setStatusTip("Выполнить сегментацию для всех срезов серии (может занять время)")
        self.segment_volume_action.triggered.connect(self._on_segment_volume)
        self.segment_volume_action.setEnabled(False)

        if not SEGMENTATION_AVAILABLE:
             self.segment_slice_action.setToolTip("Модуль сегментации или его зависимости не найдены")
             self.segment_volume_action.setToolTip("Модуль сегментации или его зависимости не найдены")


    def _create_menus(self):
        self.file_menu = self.menuBar().addMenu("Файл")
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.load_model_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)

        self.view_menu = self.menuBar().addMenu("Вид")
        self.view_menu.addAction(self.zoom_in_action)
        self.view_menu.addAction(self.zoom_out_action)
        self.view_menu.addAction(self.reset_view_action)

        self.tools_menu = self.menuBar().addMenu("Инструменты")
        self.tools_menu.addAction(self.segment_slice_action)
        self.tools_menu.addAction(self.segment_volume_action)

        self.help_menu = self.menuBar().addMenu("Справка")


    def _create_toolbar(self):
        self.main_toolbar = QToolBar("Основная панель", self)
        self.main_toolbar.setObjectName("MainToolBar")
        self.main_toolbar.setMovable(False)
        self.main_toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(Qt.TopToolBarArea, self.main_toolbar)

        self.main_toolbar.addAction(self.import_action)
        self.main_toolbar.addAction(self.load_model_action)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(self.zoom_in_action)
        self.main_toolbar.addAction(self.zoom_out_action)
        self.main_toolbar.addAction(self.reset_view_action)
        self.main_toolbar.addSeparator()
        self.main_toolbar.addAction(self.segment_slice_action)
        self.main_toolbar.addAction(self.segment_volume_action)


    def _load_window_settings(self):
        geometry = self.settings.value("MainWindow/geometry")
        if geometry: self.restoreGeometry(geometry)
        state = self.settings.value("MainWindow/state")
        if isinstance(state, (bytes, bytearray)):
            try:
                self.restoreState(state)
            except TypeError as e:
                 logger.warning(f"Не удалось восстановить состояние окна: {e}. Возможно, изменилась версия PyQt.")
        elif state is not None:
             logger.warning(f"Неверный тип сохраненного состояния окна: {type(state)}. Пропуск восстановления.")
        splitter_state = self.settings.value("MainWindow/splitter")
        if isinstance(splitter_state, (bytes, bytearray)):
             try:
                 self.central_widget.restoreState(splitter_state)
             except TypeError as e:
                  logger.warning(f"Не удалось восстановить состояние сплиттера: {e}.")
        elif splitter_state is not None:
             logger.warning(f"Неверный тип сохраненного состояния сплиттера: {type(splitter_state)}. Пропуск.")


    def _save_window_settings(self):
        self.settings.setValue("MainWindow/geometry", self.saveGeometry())
        self.settings.setValue("MainWindow/state", self.saveState())
        self.settings.setValue("MainWindow/splitter", self.central_widget.saveState())

    def _on_import_dicom(self):
        try:
            import_dialog = DicomImportDialog(self)
            if import_dialog.exec_():
                selected_files = import_dialog.get_selected_files()
                recursive_search = import_dialog.get_recursive_search()
                if selected_files:
                    self.progress_bar.setMaximum(0)
                    self.progress_bar.setValue(0)
                    self.progress_bar.setVisible(True)
                    self._update_status_bar("Загрузка DICOM файлов...")
                    self.dicom_loader.clear_cache()
                    QTimer.singleShot(50, lambda: self.dicom_loader.load_files(selected_files, recursive_search))
        except Exception as e:
            logger.error(f"Ошибка при импорте DICOM: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка импорта", f"Произошла ошибка: {str(e)}")
            self._update_status_bar("Ошибка при импорте DICOM")
            self.progress_bar.setVisible(False)


    def _on_load_model(self):
        if not SEGMENTATION_AVAILABLE:
            QMessageBox.warning(self, "Сегментация недоступна", "Модуль сегментации или зависимости не найдены.")
            return
        model_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл модели PyTorch",
            self.settings.value("Paths/last_model_dir", os.path.expanduser("~")),
            "PyTorch Model Files (*.pth *.pt);;All Files (*)"
        )
        if model_path:
            self._update_status_bar(f"Загрузка модели: {os.path.basename(model_path)}...")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                if self.viewer_panel.segmenter:
                    self.viewer_panel.load_segmentation_model(model_path)
                    self.settings.setValue("Paths/last_model_dir", os.path.dirname(model_path))
                    self._update_status_bar(f"Модель загружена: {os.path.basename(model_path)}")
                    self._update_segmentation_actions_state()
                else:
                     logger.error("Объект segmenter не инициализирован в ViewerPanel.")
                     QMessageBox.critical(self, "Внутренняя ошибка", "Объект сегментатора не найден.")
                     self._update_status_bar("Ошибка загрузки модели")
            except Exception as e:
                 logger.error(f"Ошибка при загрузке модели: {e}", exc_info=True)
                 QMessageBox.critical(self, "Ошибка загрузки модели", f"Не удалось загрузить модель: {str(e)}")
                 self._update_status_bar("Ошибка загрузки модели")
            finally:
                QApplication.restoreOverrideCursor()


    @pyqtSlot(int, int)
    def _on_loading_progress(self, current, total):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self._update_status_bar(f"Загрузка DICOM... ({current}/{total})")
        else:
            self.progress_bar.setMaximum(0)
            self.progress_bar.setValue(0)


    @pyqtSlot(list)
    def _on_loading_complete(self, studies):
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(100)
        self._update_status_bar(f"Загружено {len(studies)} исследований")
        self.sidebar_panel.set_studies(studies)
        self.viewer_panel._show_placeholder()
        self._update_segmentation_actions_state()
        logger.info(f"Загружено {len(studies)} исследований")

    @pyqtSlot(str)
    def _on_loading_error(self, error_message):
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "Ошибка импорта", error_message)
        self._update_status_bar("Ошибка при импорте DICOM")

    @pyqtSlot(object)
    def _on_series_selected(self, series_data):
        logger.info(f"Выбрана серия для отображения: {series_data.get('description', 'Неизвестно')}")
        self.viewer_panel.load_series(series_data)
        self._update_segmentation_actions_state()

    def _update_segmentation_actions_state(self):
        can_segment = (
            SEGMENTATION_AVAILABLE and
            self.viewer_panel.segmenter is not None and
            self.viewer_panel.segmenter.model is not None and
            self.viewer_panel.current_series is not None
        )
        self.segment_slice_action.setEnabled(can_segment)
        self.segment_volume_action.setEnabled(can_segment)

    def _on_zoom_in(self):
        if hasattr(self.viewer_panel, 'view_box'):
            self.viewer_panel.view_box.scaleBy((1.2, 1.2))
            logger.debug("Zoom In")

    def _on_zoom_out(self):
        if hasattr(self.viewer_panel, 'view_box'):
            self.viewer_panel.view_box.scaleBy((1/1.2, 1/1.2))
            logger.debug("Zoom Out")

    def _on_reset_view(self):
        if hasattr(self.viewer_panel, 'view_box'):
            self.viewer_panel.view_box.autoRange()
            logger.debug("Reset View")

    def _on_segment_slice(self):
        if SEGMENTATION_AVAILABLE and hasattr(self.viewer_panel, 'run_single_slice_segmentation'):
            self.viewer_panel.run_single_slice_segmentation()
        else:
            logger.warning("Попытка сегментации среза, но функция недоступна.")
            QMessageBox.warning(self, "Сегментация недоступна", "Функция сегментации недоступна.")

    def _on_segment_volume(self):
        if SEGMENTATION_AVAILABLE and hasattr(self.viewer_panel, 'start_full_segmentation'):
            self.viewer_panel.start_full_segmentation()
        else:
            logger.warning("Попытка сегментации объема, но функция недоступна.")
            QMessageBox.warning(self, "Сегментация недоступна", "Функция сегментации недоступна.")

    @pyqtSlot(int, int)
    def _on_segmentation_progress(self, current, total):
        if not self.progress_bar.isVisible():
            self.progress_bar.setVisible(True)
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self._update_status_bar(f"Сегментация... ({current}/{total})")
        else:
            self.progress_bar.setMaximum(0)
            self.progress_bar.setValue(0)

    # --- Слот для статуса экспорта ---
    @pyqtSlot(int, int)
    def _on_export_progress(self, current, total):
        """ Обновляет прогресс-бар в строке состояния при экспорте. """
        if not self.progress_bar.isVisible():
            self.progress_bar.setVisible(True)
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self._update_status_bar(f"Экспорт... ({current}/{total})")
        else:
            self.progress_bar.setMaximum(0)
            self.progress_bar.setValue(0)
    # ---------------------------------

    @pyqtSlot(str)
    def _update_status_bar(self, message):
        """ Обновляет текст в строке состояния и скрывает прогресс-бар, если нужно. """
        self.status_label.setText(message)
        if "..." not in message:
             if self.progress_bar.isVisible():
                 QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False) if "..." not in self.status_label.text() else None)
        elif not self.progress_bar.isVisible():
             self.progress_bar.setVisible(True)

    @pyqtSlot(str)
    def _on_study_removed(self, study_id):
        """Обрабатывает сигнал удаления исследования из Sidebar."""
        logger.info(f"Получен сигнал об удалении исследования {study_id} из вида.")
        # Можно добавить очистку кэша DicomLoader, если он хранит данные по ID
        # Например: self.dicom_loader.clear_study_cache(study_id)
        pass

    def closeEvent(self, event):
        # ... (остановка потоков остается такой же) ...
        if hasattr(self.viewer_panel, 'cancel_segmentation'):
            self.viewer_panel.cancel_segmentation()
            if self.viewer_panel.segmentation_thread and self.viewer_panel.segmentation_thread.isRunning():
                logger.info("Ожидание завершения потока сегментации перед выходом...")
                self.viewer_panel.segmentation_thread.quit()
                self.viewer_panel.segmentation_thread.wait(3000)
        if hasattr(self.sidebar_panel, 'export_worker') and self.sidebar_panel.export_worker is not None:
             if self.sidebar_panel.export_thread and self.sidebar_panel.export_thread.isRunning():
                  logger.info("Остановка потока экспорта перед выходом...")
                  # Отменяем воркер, если он есть
                  if hasattr(self.sidebar_panel.export_worker, 'cancel'):
                      self.sidebar_panel.export_worker.cancel()
                  self.sidebar_panel.export_thread.quit()
                  self.sidebar_panel.export_thread.wait(1000)
        self._save_window_settings()
        super().closeEvent(event)

