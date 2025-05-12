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

    # Добавляем models_dir в параметры конструктора
    def __init__(self, settings: QSettings, models_dir: str, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.models_dir = models_dir # Сохраняем путь к папке моделей
        # Инициализируем DicomLoader здесь
        self.dicom_loader = DicomLoader(settings)
        self.dicom_loader.loading_complete.connect(self._on_loading_complete)
        self.dicom_loader.loading_error.connect(self._on_loading_error)
        self.dicom_loader.loading_progress.connect(self._on_loading_progress)
        self._init_ui()
        self._load_window_settings()
        logger.info("Главное окно инициализировано")

    def _init_ui(self):
        self.setWindowTitle("PyLungViewer") # Исправлен заголовок
        self.setMinimumSize(1024, 768)
        self.central_widget = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.central_widget)

        # --- Сначала создаем viewer_panel ---
        # Передаем путь к папке моделей И экземпляр dicom_loader в ViewerPanel
        self.viewer_panel = ViewerPanel(models_dir=self.models_dir, dicom_loader=self.dicom_loader, parent=self)
        # --- Потом создаем sidebar_panel, передавая viewer_panel ---
        self.sidebar_panel = SidebarPanel(viewer_panel=self.viewer_panel, parent=self)
        # ----------------------------------------------------------

        self.sidebar_panel.series_selected.connect(self._on_series_selected)
        self.sidebar_panel.export_progress.connect(self._on_export_progress)
        self.sidebar_panel.export_status_update.connect(self._update_status_bar)
        self.sidebar_panel.study_removed_from_view.connect(self._on_study_removed)

        self.viewer_panel.segmentation_progress.connect(self._on_segmentation_progress)
        self.viewer_panel.segmentation_status_update.connect(self._update_status_bar)
        # Подключаем сигнал из ViewerPanel об успешной загрузке модели
        self.viewer_panel.model_loaded_status.connect(self._on_model_loaded_status)


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

    def _create_actions(self):
        # --- Файловые действия ---
        self.import_action = QAction("Импорт DICOM", self)
        self.import_action.setStatusTip("Импортировать DICOM файлы или директорию")
        self.import_action.triggered.connect(self._on_import_dicom)

        # Удаляем действие "Загрузить модель", т.к. она будет загружаться автоматически
        # self.load_model_action = QAction("Загрузить модель", self)
        # self.load_model_action.setStatusTip("Загрузить файл .pth модели сегментации")
        # self.load_model_action.triggered.connect(self._on_load_model)
        # self.load_model_action.setEnabled(SEGMENTATION_AVAILABLE)
        # if not SEGMENTATION_AVAILABLE:
        #     self.load_model_action.setToolTip("Модуль сегментации или его зависимости не найдены")

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
        self.segment_slice_action = QAction("Сегм. срез", self)
        self.segment_slice_action.setStatusTip("Выполнить сегментацию только для текущего среза")
        self.segment_slice_action.triggered.connect(self._on_segment_slice)
        # Изначально выключены, будут включены после загрузки модели и данных
        self.segment_slice_action.setEnabled(False)

        self.segment_volume_action = QAction("Сегм. весь объем", self)
        self.segment_volume_action.setStatusTip("Выполнить сегментацию для всех срезов серии (может занять время)")
        self.segment_volume_action.triggered.connect(self._on_segment_volume)
        # Изначально выключены, будут включены после загрузки модели и данных
        self.segment_volume_action.setEnabled(False)

        # Подсказки, если сегментация недоступна
        if not SEGMENTATION_AVAILABLE:
             self.segment_slice_action.setToolTip("Модуль сегментации или его зависимости не найдены")
             self.segment_volume_action.setToolTip("Модуль сегментации или его зависимости не найдены")


    def _create_menus(self):
        self.file_menu = self.menuBar().addMenu("Файл")
        self.file_menu.addAction(self.import_action)
        # Удаляем действие "Загрузить модель" из меню
        # self.file_menu.addAction(self.load_model_action)
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
        # Удаляем действие "Загрузить модель" из панели инструментов
        # self.main_toolbar.addAction(self.load_model_action)
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

    # Удаляем метод _on_load_model, т.к. загрузка модели теперь автоматическая
    # def _on_load_model(self):
    #     ...


    @pyqtSlot(bool)
    def _on_model_loaded_status(self, success):
        """
        Обрабатывает сигнал из ViewerPanel о статусе загрузки модели.
        Обновляет статус-бар и состояние кнопок сегментации.
        """
        if success:
            # Здесь мы не знаем точное имя загруженной модели,
            # но можем показать общий статус.
            self._update_status_bar("Модель сегментации загружена.")
        else:
            self._update_status_bar("Ошибка загрузки модели сегментации.")
            # Если модель не загружена, кнопки сегментации должны быть выключены
            # Это уже обрабатывается в _update_segmentation_actions_state
            # после получения данных серии.

        # Обновляем состояние кнопок после загрузки модели (или ее неудачи)
        # Но делаем это только после загрузки данных серии,
        # т.к. для сегментации нужны и модель, и данные.
        # Поэтому _update_segmentation_actions_state вызывается после _on_series_selected
        # и _on_loading_complete.


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
        # Обновляем состояние кнопок сегментации после загрузки данных
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
        # Обновляем состояние кнопок сегментации после выбора серии
        self._update_segmentation_actions_state()

    def _update_segmentation_actions_state(self):
        """
        Обновляет состояние действий сегментации (доступность).
        Действия доступны, если модуль сегментации есть, модель загружена И данные серии загружены.
        """
        can_segment = (
            SEGMENTATION_AVAILABLE and
            self.viewer_panel.segmenter is not None and
            self.viewer_panel.segmenter.model is not None and # Проверяем, загружена ли модель
            self.viewer_panel.current_series is not None and # Проверяем, загружены ли данные серии
            self.viewer_panel.current_volume_hu is not None # Проверяем, загружен ли объем
        )
        self.segment_slice_action.setEnabled(can_segment)
        self.segment_volume_action.setEnabled(can_segment)
        # Состояние чекбокса "Показать сегментацию" управляется внутри ViewerPanel
        # self.viewer_panel.segment_checkbox.setEnabled(...)


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
        # Проверка доступности сегментации теперь делается в _check_segmentation_prerequisites
        # внутри ViewerPanel, вызываемой из run_single_slice_segmentation
        if hasattr(self.viewer_panel, 'run_single_slice_segmentation'):
            self.viewer_panel.run_single_slice_segmentation()
        else:
            logger.warning("Попытка сегментации среза, но функция недоступна.")
            QMessageBox.warning(self, "Сегментация недоступна", "Функция сегментации недоступна.")

    def _on_segment_volume(self):
        # Проверка доступности сегментации теперь делается в _check_segmentation_prerequisites
        # внутри ViewerPanel, вызываемой из start_full_segmentation
        if hasattr(self.viewer_panel, 'start_full_segmentation'):
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
        # Скрываем прогресс-бар через 2 секунды, если сообщение не указывает на текущий процесс
        # и прогресс-бар видим.
        if "..." not in message and self.progress_bar.isVisible():
             # Используем QTimer.singleShot для задержки скрытия
             QTimer.singleShot(2000, lambda: self._hide_progress_bar_if_idle())
        elif "..." in message and not self.progress_bar.isVisible():
             self.progress_bar.setVisible(True)

    def _hide_progress_bar_if_idle(self):
         """ Скрывает прогресс-бар, только если текущий статус не указывает на процесс. """
         if "..." not in self.status_label.text():
              self.progress_bar.setVisible(False)


    @pyqtSlot(str)
    def _on_study_removed(self, study_id):
        """Обрабатывает сигнал удаления исследования из Sidebar."""
        logger.info(f"Получен сигнал об удалении исследования {study_id} из вида.")
        # Можно добавить очистку кэша DicomLoader, если он хранит данные по ID
        # Например: self.dicom_loader.clear_study_cache(study_id)
        # Также, если удалено текущее исследование, нужно сбросить ViewerPanel
        if self.viewer_panel.current_series:
             current_study_id = None
             # Ищем исследование текущей серии в sidebar_panel.studies (если нужно)
             # Или можно передать study_id текущей серии из ViewerPanel
             # Пока просто сбрасываем, если study_id совпадает (предполагая, что study_id уникален)
             current_study_data = self.viewer_panel.current_series.get('study_data') # Если вы храните ссылку на исследование в серии
             if current_study_data and current_study_data.get('id') == study_id:
                  logger.info("Удалено текущее исследование, сбрасываем ViewerPanel.")
                  self.viewer_panel.load_series(None) # Сброс ViewerPanel
                  self._update_segmentation_actions_state()


    def closeEvent(self, event):
        # ... (остановка потоков остается такой же) ...
        if hasattr(self.viewer_panel, 'cancel_segmentation'):
            self.viewer_panel.cancel_segmentation()
            # Добавим небольшую задержку, чтобы поток успел отреагировать на отмену
            QApplication.processEvents() # Обрабатываем события, чтобы сигнал отмены дошел
            if self.viewer_panel.segmentation_thread and self.viewer_panel.segmentation_thread.isRunning():
                logger.info("Ожидание завершения потока сегментации перед выходом...")
                # Увеличим время ожидания, если необходимо
                if not self.viewer_panel.segmentation_thread.wait(5000): # Ждем до 5 секунд
                     logger.warning("Поток сегментации не завершился вовремя.")
        if hasattr(self.sidebar_panel, 'export_worker') and self.sidebar_panel.export_worker is not None:
             if self.sidebar_panel.export_thread and self.sidebar_panel.export_thread.isRunning():
                  logger.info("Остановка потока экспорта перед выходом...")
                  # Отменяем воркер, если он есть
                  if hasattr(self.sidebar_panel.export_worker, 'cancel'):
                      self.sidebar_panel.export_worker.cancel()
                  # Добавим небольшую задержку для обработки отмены
                  QApplication.processEvents()
                  if not self.sidebar_panel.export_thread.wait(2000): # Ждем до 2 секунд
                       logger.warning("Поток экспорта не завершился вовремя.")

        self._save_window_settings()
        super().closeEvent(event)

