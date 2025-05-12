#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Панель просмотра DICOM изображений для приложения PyLungViewer.
(Версия с интеграцией сегментации + Сегментация всего объема + Отображение HU при наведении мыши + Две линейки без нижней метки и единиц)
"""

import logging
import os
import traceback
import glob # Добавляем для поиска файлов
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QPushButton, QFrame, QApplication,
    QCheckBox, QMessageBox, QProgressDialog
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QEvent, QObject, QDateTime, QPointF, QThread, QTimer
from PyQt5.QtGui import QIcon, QColor
import pyqtgraph as pg

# Для визуализации изображений
import numpy as np
import pydicom
from pyqtgraph import ImageView, ImageItem, AxisItem # Импортируем AxisItem

# --- Определяем logger ДО блока try-except ---
logger = logging.getLogger(__name__)
# -------------------------------------------

# Импорт модуля сегментации
try:
    from pylungviewer.core.segmentation import LungSegmenter
    SEGMENTATION_AVAILABLE = True
    logger.info("Модуль сегментации успешно импортирован.")
except ImportError as e:
    LungSegmenter = None
    SEGMENTATION_AVAILABLE = False
    logger.error("!!! Ошибка при импорте модуля сегментации !!!")
    print("--------------------------------------------------")
    print("!!! Ошибка импорта модуля сегментации (viewer_panel.py) !!!")
    print(f"Ошибка: {e}")
    traceback.print_exc()
    print("--------------------------------------------------")
    logger.warning("Модуль сегментации (pylungviewer.core.segmentation) не найден или его зависимости отсутствуют.")

from pylungviewer.utils.window_presets import WindowPresets
from pylungviewer.core.dicom_loader import DicomLoader # Импортируем DicomLoader

# --- Класс для выполнения сегментации в фоновом потоке ---
class SegmentationWorker(QObject):
    finished = pyqtSignal(object) # Сигнал завершения (передает результат - 3D маску или None)
    progress = pyqtSignal(int, int) # Сигнал прогресса (current, total)
    error = pyqtSignal(str) # Сигнал ошибки

    def __init__(self, segmenter: LungSegmenter, volume_hu: np.ndarray):
        super().__init__()
        self.segmenter = segmenter
        self.volume_hu = volume_hu
        self.is_cancelled = False

    def run(self):
        """Выполняет сегментацию объема."""
        if self.segmenter is None or self.volume_hu is None:
            self.error.emit("Сегментатор или данные объема не инициализированы.")
            self.finished.emit(None)
            return

        signals_connected = False
        if hasattr(self.segmenter, 'signals'):
            try:
                if hasattr(self.segmenter.signals, 'progress'):
                    # Подключаем сигнал прогресса сегментатора к сигналу прогресса воркера
                    self.segmenter.signals.progress.connect(self.report_progress)
                    signals_connected = True
                else:
                    logger.warning("Атрибут 'progress' не найден у объекта segmenter.signals.")
            except (AttributeError, TypeError):
                 logger.warning("Не удалось подключить сигнал прогресса сегментатора.")

        try:
            # Передаем флаг отмены в predict_volume, если он поддерживает
            if hasattr(self.segmenter, 'predict_volume') and hasattr(self.segmenter.predict_volume, '__code__') and 'is_cancelled' in self.segmenter.predict_volume.__code__.co_varnames:
                 result = self.segmenter.predict_volume(self.volume_hu, is_cancelled=lambda: self.is_cancelled)
            else:
                 result = self.segmenter.predict_volume(self.volume_hu)

            if not self.is_cancelled: # Проверяем флаг отмены еще раз после выполнения
                self.finished.emit(result)
            else:
                logger.info("Сегментация отменена воркером, результат не передается.")
                self.finished.emit(None)
        except Exception as e:
            logger.error(f"Ошибка в потоке сегментации: {e}", exc_info=True)
            self.error.emit(f"Ошибка во время сегментации: {e}")
            self.finished.emit(None)
        finally:
             # Отключаем сигнал прогресса сегментатора
             if signals_connected and hasattr(self.segmenter, 'signals') and hasattr(self.segmenter.signals, 'progress'):
                 try:
                     self.segmenter.signals.progress.disconnect(self.report_progress)
                 except (TypeError, AttributeError):
                     pass

    def report_progress(self, current, total):
        """Передает сигнал прогресса от сегментатора."""
        if not self.is_cancelled:
            self.progress.emit(current, total)

    def cancel(self):
        """Устанавливает флаг отмены для воркера."""
        logger.info("Получен запрос на отмену сегментации.")
        self.is_cancelled = True
        # Если сегментатор поддерживает отмену, вызываем ее
        if hasattr(self.segmenter, 'cancel'):
             self.segmenter.cancel()


# --- Основной класс панели ---
class ViewerPanel(QWidget):
    """Панель просмотра DICOM изображений с поддержкой сегментации и отображением HU."""

    slice_changed = pyqtSignal(int)
    segmentation_progress = pyqtSignal(int, int)
    segmentation_status_update = pyqtSignal(str)
    # Новый сигнал для оповещения главного окна о статусе загрузки модели
    model_loaded_status = pyqtSignal(bool)

    # Добавляем models_dir И dicom_loader в параметры конструктора
    def __init__(self, models_dir: str, dicom_loader: DicomLoader, parent=None):
        super().__init__(parent)
        self.current_series = None
        self.current_slice_index = 0
        self.current_pixel_data_hu = None
        self.current_volume_hu = None
        self.segmentation_mask = None # Маска для ТЕКУЩЕГО среза
        self.full_segmentation_mask_volume = None # 3D массив масок
        self.models_dir = models_dir # Сохраняем путь к папке моделей
        self.dicom_loader = dicom_loader # Сохраняем экземпляр DicomLoader

        if SEGMENTATION_AVAILABLE:
            self.segmenter = LungSegmenter()
            # !!! Автоматическая загрузка модели при инициализации !!!
            self._auto_load_model()
        else:
            self.segmenter = None
            # Если сегментация недоступна, отправляем сигнал с False
            self.model_loaded_status.emit(False)

        self.segmentation_thread = None
        self.segmentation_worker = None
        self.progress_dialog = None

        self.touch_start_pos = None
        self._init_ui()
        self.installEventFilter(self)

        # Включаем отслеживание мыши на graphics_widget
        self.graphics_widget.setMouseTracking(True)
        # Подключаем сигнал sigMouseMoved от сцены к слоту _on_mouse_moved
        self.graphics_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        logger.info("Панель просмотра инициализирована (с поддержкой сегментации и отображением HU)")

    def _init_ui(self):
        # ... (UI initialization code remains the same) ...
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        info_panel = QWidget()
        info_layout = QHBoxLayout(info_panel)
        info_layout.setContentsMargins(5, 5, 5, 5)
        self.info_label = QLabel("Нет загруженных данных")
        info_layout.addWidget(self.info_label)

        # --- Добавляем QLabel для отображения HU ---
        self.hu_label = QLabel("HU: N/A")
        self.hu_label.setMinimumWidth(100) # Обеспечиваем минимальную ширину
        self.hu_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        info_layout.addWidget(self.hu_label)
        # ------------------------------------------

        main_layout.addWidget(info_panel)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        main_layout.addWidget(self.graphics_widget, 1)

        # --- Добавляем AxisItem для левой линейки (вертикальная) ---
        self.left_axis = pg.AxisItem(orientation='left')
        self.left_axis.setLabel('Положение', units='мм')
        # Добавляем линейку в GraphicsLayoutWidget в колонку 0, строку 0
        self.graphics_widget.addItem(self.left_axis, row=0, col=0)

        # --- Добавляем AxisItem для нижней линейки (горизонтальная) ---
        self.bottom_axis = pg.AxisItem(orientation='bottom')
        # Устанавливаем пустую строку в качестве метки И единиц измерения
        self.bottom_axis.setLabel('')
        # Добавляем линейку в GraphicsLayoutWidget в колонку 1, строку 1
        self.graphics_widget.addItem(self.bottom_axis, row=1, col=1)


        # --- Создаем ViewBox для изображения в следующей колонке ---
        self.view_box = self.graphics_widget.addViewBox(row=0, col=1) # Размещаем в колонке 1, строке 0
        self.view_box.setAspectLocked(True) # Сохраняем пропорции
        self.view_box.invertY(True) # Инвертируем ось Y, чтобы начало было сверху (как в DICOM)
        self.view_box.setMouseEnabled(x=True, y=True) # Включаем панорамирование и масштабирование мышью

        # --- Связываем левую линейку с осью Y ViewBox ---
        self.left_axis.linkToView(self.view_box)

        # --- Связываем нижнюю линейку с осью X ViewBox ---
        self.bottom_axis.linkToView(self.view_box)


        # --- Устанавливаем фактор растяжения колонок ---
        # Колонка с ViewBox и нижней линейкой получает больше пространства
        self.graphics_widget.ci.layout.setColumnStretchFactor(1, 10)
        # Устанавливаем фактор растяжения строк (строка с ViewBox и левой линейкой получает больше пространства)
        self.graphics_widget.ci.layout.setRowStretchFactor(0, 10)


        # --- Создаем ImageItem для отображения КТ снимка ---
        self.img_item = pg.ImageItem()
        self.view_box.addItem(self.img_item) # Добавляем img_item в view_box

        # --- Создаем ImageItem для отображения маски сегментации ---
        self.mask_item = pg.ImageItem()
        self.mask_item.setCompositionMode(pg.QtGui.QPainter.CompositionMode_Plus)
        lut = np.array([[0, 0, 0, 0], [255, 0, 0, 100]], dtype=np.uint8) # Красный, 100 из 255 прозрачность
        self.mask_item.setLookupTable(lut)
        self.mask_item.setVisible(False) # Изначально скрываем маску
        self.view_box.addItem(self.mask_item) # Добавляем mask_item в view_box


        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(5, 5, 5, 5)
        bottom_layout.setSpacing(5)

        slider_panel = QHBoxLayout()
        self.prev_slice_btn = QPushButton("<<")
        self.prev_slice_btn.setFixedWidth(40)
        self.prev_slice_btn.clicked.connect(self._on_prev_slice)
        self.next_slice_btn = QPushButton(">>")
        self.next_slice_btn.setFixedWidth(40)
        self.next_slice_btn.clicked.connect(self._on_next_slice)
        self.slice_slider = QSlider(Qt.Horizontal)
        self.slice_slider.setMinimum(0)
        self.slice_slider.setMaximum(0)
        self.slice_slider.valueChanged.connect(self._on_slice_changed)
        self.slice_label = QLabel("0/0")
        self.slice_label.setMinimumWidth(60)
        self.slice_label.setAlignment(Qt.AlignCenter)
        slider_panel.addWidget(self.prev_slice_btn)
        slider_panel.addWidget(self.slice_slider, 1)
        slider_panel.addWidget(self.slice_label)
        slider_panel.addWidget(self.next_slice_btn)
        bottom_layout.addLayout(slider_panel)

        segment_panel = QHBoxLayout()
        self.segment_checkbox = QCheckBox("Показать сегментацию")
        self.segment_checkbox.toggled.connect(self._on_segment_toggle)
        # Изначально выключен, включается при наличии маски
        self.segment_checkbox.setEnabled(False)

        self.run_segment_btn = QPushButton("Сегм. срез")
        self.run_segment_btn.clicked.connect(self.run_single_slice_segmentation)
        # Изначально выключен, включается при наличии модели и данных
        self.run_segment_btn.setEnabled(False)

        self.run_full_segment_btn = QPushButton("Сегм. весь объем")
        self.run_full_segment_btn.clicked.connect(self.start_full_segmentation)
        # Изначально выключен, включается при наличии модели и данных
        self.run_full_segment_btn.setEnabled(False)

        # Обновляем подсказки и состояние кнопок сегментации после инициализации UI
        self._update_segmentation_button_tooltips()
        self._update_segmentation_controls_state()


        segment_panel.addWidget(self.segment_checkbox)
        segment_panel.addStretch(1)
        segment_panel.addWidget(self.run_segment_btn)
        segment_panel.addWidget(self.run_full_segment_btn)
        bottom_layout.addLayout(segment_panel)

        main_layout.addWidget(bottom_panel)
        self._show_placeholder()

    def _update_segmentation_button_tooltips(self):
         """ Обновляет подсказки для кнопок сегментации в зависимости от доступности модуля. """
         if not SEGMENTATION_AVAILABLE:
             tooltip = "Модуль сегментации или его зависимости не найдены"
             self.segment_checkbox.setToolTip(tooltip)
             self.run_segment_btn.setToolTip(tooltip)
             self.run_full_segment_btn.setToolTip(tooltip)
         else:
             self.segment_checkbox.setToolTip("Показать/скрыть маску сегментации")
             self.run_segment_btn.setToolTip("Сегментировать только текущий срез (требуется загруженная модель)")
             self.run_full_segment_btn.setToolTip("Сегментировать все срезы серии (требуется загруженная модель)")


    def _auto_load_model(self):
        """ Попытка автоматической загрузки модели из папки models_dir. """
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
            logger.warning("Автоматическая загрузка модели пропущена: модуль сегментации недоступен.")
            self.model_loaded_status.emit(False)
            return

        if not os.path.isdir(self.models_dir):
            logger.warning(f"Папка моделей не найдена: {self.models_dir}")
            self.model_loaded_status.emit(False)
            return

        # Ищем первый файл модели (.pth или .pt) в папке моделей
        model_files = glob.glob(os.path.join(self.models_dir, '*.pth')) + glob.glob(os.path.join(self.models_dir, '*.pt'))

        if not model_files:
            logger.warning(f"Файлы моделей (.pth, .pt) не найдены в папке: {self.models_dir}")
            self.model_loaded_status.emit(False)
            return

        # Берем первый найденный файл
        model_path = model_files[0]
        logger.info(f"Попытка автоматической загрузки модели: {model_path}")

        # Используем QTimer.singleShot для загрузки модели после того, как UI будет полностью готов
        # Это предотвращает блокировку основного потока при запуске
        QTimer.singleShot(100, lambda: self._perform_model_loading(model_path))


    def _perform_model_loading(self, model_path):
        """ Выполняет фактическую загрузку модели. """
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            success = self.segmenter.load_model(model_path)
            self.model_loaded_status.emit(success)
            if success:
                 logger.info(f"Автоматическая загрузка модели успешна: {os.path.basename(model_path)}")
            else:
                 logger.error(f"Автоматическая загрузка модели не удалась: {os.path.basename(model_path)}")
                 QMessageBox.critical(self, "Ошибка загрузки модели", f"Не удалось автоматически загрузить модель из файла:\n{model_path}\n\nПроверьте логи.")
        except Exception as e:
            logger.error(f"Исключение при автоматической загрузке модели: {e}", exc_info=True)
            self.model_loaded_status.emit(False)
            QMessageBox.critical(self, "Ошибка загрузки модели", f"Произошла ошибка при автоматической загрузке модели:\n{str(e)}\n\nПроверьте логи.")
        finally:
            QApplication.restoreOverrideCursor()
            # Обновляем состояние кнопок сегментации после попытки загрузки модели
            self._update_segmentation_controls_state()


    def _show_placeholder(self):
        # ... (remains the same) ...
        placeholder_image = np.zeros((512, 512), dtype=np.uint8)
        self.img_item.setImage(placeholder_image)
        self.mask_item.clear()
        self.mask_item.setVisible(False)
        self.view_box.autoRange()
        self.slice_slider.setEnabled(False)
        self.prev_slice_btn.setEnabled(False)
        self.next_slice_btn.setEnabled(False)
        # Состояние чекбокса и кнопок сегментации управляется _update_segmentation_controls_state
        # self.segment_checkbox.setEnabled(False)
        # self.segment_checkbox.setChecked(False)
        # self.run_segment_btn.setEnabled(False)
        # self.run_full_segment_btn.setEnabled(False)
        self.info_label.setText("Нет загруженных данных")
        # --- Очищаем HU Label ---
        self.hu_label.setText("HU: N/A")
        # ------------------------
        self.current_volume_hu = None
        self.current_pixel_data_hu = None
        self.segmentation_mask = None
        self.full_segmentation_mask_volume = None
        # Обновляем состояние кнопок после сброса данных
        self._update_segmentation_controls_state()


    def eventFilter(self, obj, event):
        # ... (remains the same) ...
        if obj is self:
            if event.type() == QEvent.Wheel:
                self._handle_wheel_scroll(event)
                return True
            elif event.type() == QEvent.MouseButtonPress:
                 if event.button() == Qt.LeftButton:
                     self.touch_start_pos = event.pos()
                     return True
            elif event.type() == QEvent.MouseMove:
                 if self.touch_start_pos is not None and event.buttons() & Qt.LeftButton:
                     pass # Пока не обрабатываем движение для панорамирования
            elif event.type() == QEvent.MouseButtonRelease:
                 if event.button() == Qt.LeftButton and self.touch_start_pos is not None:
                     end_pos = event.pos()
                     delta = end_pos - self.touch_start_pos
                     swipe_threshold = 50 # Порог для определения свайпа
                     if abs(delta.x()) > abs(delta.y()) and abs(delta.x()) > swipe_threshold:
                         if delta.x() > 0: self._on_prev_slice()
                         else: self._on_next_slice()
                         self.touch_start_pos = None
                         return True
                     self.touch_start_pos = None
                 self.touch_start_pos = None # Сбрасываем даже если не было свайпа
        return super().eventFilter(obj, event)


    def _handle_wheel_scroll(self, event):
        # ... (remains the same) ...
        if self.current_series is None or not self.current_series.get('files', []): return
        current_time = QDateTime.currentMSecsSinceEpoch()
        scroll_interval = 50 # Минимальный интервал между прокрутками в мс
        if not hasattr(self, '_last_scroll_time'): self._last_scroll_time = 0
        if current_time - self._last_scroll_time >= scroll_interval:
            self._last_scroll_time = current_time
            delta = event.angleDelta().y()
            step = 1
            total_slices = len(self.current_series.get('files', []))
            # Увеличиваем шаг прокрутки для больших серий
            if total_slices > 100: step = max(1, total_slices // 50)
            current_index = self.current_slice_index
            if delta > 0: new_index = max(0, current_index - step)
            else: new_index = min(total_slices - 1, current_index + step)
            if new_index != current_index: self.slice_slider.setValue(new_index)


    # Удаляем метод load_segmentation_model, т.к. загрузка теперь автоматическая
    # def load_segmentation_model(self, model_path):
    #     ...


    def load_series(self, series_data):
        """Загрузка новой серии, с остановкой предыдущей сегментации."""
        logger.info("Загрузка новой серии...")
        # Отменяем любую текущую сегментацию перед загрузкой новой серии
        self.cancel_segmentation()
        # Ожидаем завершения потока, если он еще работает
        # Добавим проверку, что поток существует перед попыткой quit/wait
        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
             logger.warning("Предыдущая сегментация еще выполняется. Попытка отмены...")
             # Устанавливаем флаг отмены в воркере
             if self.segmentation_worker:
                  self.segmentation_worker.cancel()
             # Завершаем поток
             self.segmentation_thread.quit()
             # Ждем завершения потока, но с таймаутом
             if not self.segmentation_thread.wait(2000): # Ждем до 2 секунд
                  logger.warning("Поток сегментации не завершился вовремя при смене серии.")
             # Очищаем ссылки после ожидания
             self._clear_segmentation_thread_refs()
        # Если поток существовал, но не был запущен (например, после ошибки),
        # просто очищаем ссылки
        elif self.segmentation_thread is not None:
             self._clear_segmentation_thread_refs()


        self._view_reset_done = False
        self._show_placeholder() # Сбрасываем UI и данные
        self.current_series = series_data
        if series_data is None or not series_data.get('files', []):
            logger.warning("Попытка загрузить пустую серию")
            # Обновляем состояние кнопок после загрузки пустой серии
            self._update_segmentation_controls_state()
            return
        files = series_data.get('files', [])
        slice_count = len(files)
        logger.info(f"Загрузка {slice_count} срезов в память...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        volume_hu_list = []
        first_ds = None
        try:
            # Используем переданный экземпямпляр DicomLoader
            dicom_loader = self.dicom_loader
            if dicom_loader is None:
                 # Этого не должно произойти, если DicomLoader передан в конструктор
                 logger.error("Экземпляр DicomLoader не был передан в ViewerPanel.")
                 raise RuntimeError("DicomLoader недоступен.")

            for i, file_meta in enumerate(files):
                # Используем метод load_pixel_data из DicomLoader
                pixel_data = dicom_loader.load_pixel_data(file_meta)
                if pixel_data is None:
                     logger.warning(f"Не удалось загрузить пиксельные данные для файла: {file_meta.get('file_path', 'N/A')}")
                     # Можно пропустить этот срез или обработать ошибку иначе
                     continue # Пропускаем срез с ошибкой

                volume_hu_list.append(pixel_data)

            if not volume_hu_list:
                 logger.error("Не удалось загрузить пиксельные данные ни для одного среза в серии.")
                 raise RuntimeError("Не удалось загрузить данные серии.")

            self.current_volume_hu = np.stack(volume_hu_list, axis=0)
            logger.info(f"Объем загружен. Форма: {self.current_volume_hu.shape}")

            # Пытаемся получить первый датасет для информации
            if files:
                 try:
                     first_ds = pydicom.dcmread(files[0].get('file_path'), force=True, stop_before_pixels=True)
                 except Exception as e:
                     logger.warning(f"Не удалось прочитать первый DICOM файл для информации: {e}")
                     first_ds = None

        except Exception as e:
            logger.error(f"Ошибка при загрузке объема серии: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка загрузки серии", f"Не удалось загрузить данные серии:\n{str(e)}")
            self._show_placeholder()
            return
        finally:
            QApplication.restoreOverrideCursor()

        slice_count = self.current_volume_hu.shape[0] if self.current_volume_hu is not None else 0
        if slice_count > 0:
             self.slice_slider.setMinimum(0)
             self.slice_slider.setMaximum(slice_count - 1)
             self.slice_slider.setValue(slice_count // 2)
             self.slice_slider.setEnabled(True)
             self.prev_slice_btn.setEnabled(True)
             self.next_slice_btn.setEnabled(True)

             # --- Get Pixel Spacing and set img_item pixelization ---
             row_spacing = 1.0 # Default if not found
             col_spacing = 1.0 # Default if not found
             if files and first_ds:
                  try:
                       pixel_spacing = getattr(first_ds, 'PixelSpacing', None)
                       if pixel_spacing and len(pixel_spacing) == 2:
                            # PixelSpacing is [row_spacing, column_spacing]
                            row_spacing = float(pixel_spacing[0])
                            col_spacing = float(pixel_spacing[1])
                            logger.info(f"Pixel Spacing found: Row={row_spacing}mm, Col={col_spacing}mm")
                            # Устанавливаем реальный размер пикселя для img_item
                            self.img_item.setPixelSize(x=col_spacing, y=row_spacing)
                            # Устанавливаем единицы измерения для левой линейки
                            self.left_axis.setLabel('Положение', units='мм')


                            # Optional: Set origin if Image Position (Patient) is available
                            # This is more complex and might require calculating the position of the first pixel.
                            # Let's skip this for now and assume the origin is at the top-left of the image data.
                            # The setPixelSize should handle scaling correctly for the axis.
                            # image_position = getattr(first_ds, 'ImagePositionPatient', None)
                            # if image_position and len(image_position) == 3:
                            #      pass # Handle origin if needed later

                  except Exception as e:
                       logger.warning(f"Не удалось получить Pixel Spacing или установить pixelization: {e}")
             else:
                  logger.warning("Pixel Spacing не доступен (нет файлов или ds). Использование дефолтного 1.0мм.")
                  # Используем дефолтный размер пикселя, если информация недоступна
                  self.img_item.setPixelSize(x=1.0, y=1.0)

             self._update_slice_display(self.slice_slider.value())

             patient_name_obj = getattr(first_ds, 'PatientName', 'N/A') if first_ds else 'N/A'
             patient_name = str(patient_name_obj)
             study_desc = getattr(first_ds, 'StudyDescription', '') if first_ds else ''
             series_desc = series_data.get('description', 'Неизвестно')
             modality = series_data.get('modality', '')
             self.info_label.setText(f"Пациент: {patient_name} | Исслед.: {study_desc} | Серия: {series_desc} ({modality})")
             logger.info(f"Загружена серия '{series_desc}' из {slice_count} изображений")
        else:
             # Если после загрузки объем пуст
             self._show_placeholder()
             logger.warning("Серия загружена, но не содержит изображений.")


        # Обновляем состояние кнопок сегментации после загрузки данных серии
        self._update_segmentation_controls_state()


    def _update_slice_display(self, slice_index):
        if self.current_volume_hu is None: return
        if slice_index < 0 or slice_index >= self.current_volume_hu.shape[0]: return

        # --- Исправлено: Логика обновления маски и чекбокса ---
        has_full_mask = self.full_segmentation_mask_volume is not None
        is_new_slice = slice_index != self.current_slice_index # Проверяем, изменился ли срез

        # Обновляем основные данные среза
        self.current_slice_index = slice_index
        self.current_pixel_data_hu = self.current_volume_hu[slice_index]

        # Отображаем КТ
        # Получаем настройки окна из WindowPresets
        window_center, window_width = WindowPresets.get_preset("Легочное")
        display_image_hu = self.current_pixel_data_hu
        # Используем setImage с autoLevels=False и levels для применения окна
        self.img_item.setImage(display_image_hu.T, autoLevels=False, levels=[window_center - window_width / 2.0, window_center + window_width / 2.0]) # Транспонируем для правильной ориентации

        # Автоматическое масштабирование при первой загрузке среза
        if not hasattr(self, '_view_reset_done') or not self._view_reset_done:
             self.view_box.autoRange()
             self._view_reset_done = True

        # Обновляем маску и чекбокс
        if has_full_mask:
            # Если есть полная маска, берем срез из нее
            if slice_index < self.full_segmentation_mask_volume.shape[0]:
                self.segmentation_mask = self.full_segmentation_mask_volume[slice_index]
            else: # На всякий случай, если индекс выходит за пределы
                self.segmentation_mask = None
            # Чекбокс включается в _update_segmentation_controls_state
            # self.segment_checkbox.setEnabled(True)
        elif is_new_slice:
            # Если полной маски нет И мы перешли на НОВЫЙ срез,
            # сбрасываем временную маску
            self.segmentation_mask = None
            # Состояние чекбокса обновится в _update_segmentation_controls_state

        self._update_mask_overlay() # Обновляем отображение маски

        total_slices = self.current_volume_hu.shape[0]
        self.slice_label.setText(f"{slice_index + 1}/{total_slices}")
        self.slice_changed.emit(slice_index)
        # -------------------------------------------------------

    @pyqtSlot(int)
    def _on_slice_changed(self, value):
        if value == self.current_slice_index: return
        self._update_slice_display(value)

    def _on_prev_slice(self):
        new_index = max(0, self.current_slice_index - 1)
        if new_index != self.current_slice_index: self.slice_slider.setValue(new_index)

    def _on_next_slice(self):
        if self.current_volume_hu is None: return
        total_slices = self.current_volume_hu.shape[0]
        new_index = min(total_slices - 1, self.current_slice_index + 1)
        if new_index != self.current_slice_index: self.slice_slider.setValue(new_index)

    @pyqtSlot(bool)
    def _on_segment_toggle(self, checked):
        """ Обработчик переключения чекбокса отображения сегментации. """
        # Просто обновляем оверлей на основе нового состояния чекбокса
        self._update_mask_overlay()

    @pyqtSlot()
    def run_single_slice_segmentation(self):
        """ Запускает сегментацию только для текущего среза. """
        # Проверка доступности сегментации
        if not self._check_segmentation_prerequisites(): return

        logger.info(f"Запуск сегментации для среза {self.current_slice_index + 1}...")
        self.segmentation_status_update.emit(f"Сегментация среза {self.current_slice_index + 1}...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Сбрасываем полную маску, так как результат будет только для одного среза
            self.full_segmentation_mask_volume = None
            # Деактивируем чекбокс полной маски
            # self.segment_checkbox.setEnabled(False) # Это делается в _update_segmentation_controls_state

            # Выполняем предсказание
            single_mask = self.segmenter.predict(self.current_pixel_data_hu)

            if single_mask is not None:
                logger.info("Сегментация среза завершена успешно.")
                # Сохраняем маску для текущего среза
                self.segmentation_mask = single_mask
                # Активируем и включаем чекбокс отображения текущей маски
                self.segment_checkbox.setEnabled(True)
                self.segment_checkbox.setChecked(True)
                self._update_mask_overlay() # Отображаем маску
                self.segmentation_status_update.emit(f"Сегментация среза {self.current_slice_index + 1} завершена.")
            else:
                logger.error("Сегментация среза не удалась.")
                QMessageBox.critical(self, "Ошибка сегментации", "Не удалось выполнить сегментацию среза.")
                # Сбрасываем маску и деактивируем чекбокс
                self.segmentation_mask = None
                self.segment_checkbox.setEnabled(False)
                self.segment_checkbox.setChecked(False)
                self._update_mask_overlay() # Скрываем оверлей
                self.segmentation_status_update.emit("Ошибка сегментации среза.")
        except Exception as e:
             logger.error(f"Исключение при сегментации среза: {e}", exc_info=True)
             QMessageBox.critical(self, "Ошибка сегментации", f"Произошла ошибка при сегментации среза:\n{str(e)}")
             self.segmentation_mask = None
             self.segment_checkbox.setEnabled(False)
             self.segment_checkbox.setChecked(False)
             self._update_mask_overlay()
             self.segmentation_status_update.emit("Ошибка сегментации среза.")
        finally:
            QApplication.restoreOverrideCursor()
            # Обновляем состояние кнопок после завершения (успеха или ошибки)
            self._update_segmentation_controls_state()


    @pyqtSlot()
    def start_full_segmentation(self):
        """ Запускает сегментацию всего объема в фоновом потоке. """
        # Проверка доступности сегментации
        if not self._check_segmentation_prerequisites(): return
        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
            QMessageBox.information(self, "Сегментация", "Сегментация всего объема уже запущена.")
            return

        logger.info("Запуск сегментации всего объема...")
        self.segmentation_status_update.emit("Сегментация всего объема...")
        self._set_segmentation_controls_enabled(False) # Отключаем кнопки во время процесса

        num_slices = self.current_volume_hu.shape[0] if self.current_volume_hu is not None else 0
        parent_widget = self.parent() if self.parent() else self
        self.progress_dialog = QProgressDialog("Сегментация всего объема...", "Отмена", 0, num_slices, parent_widget)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(1000) # Показываем диалог только если процесс занимает > 1 сек
        self.progress_dialog.setAutoReset(True)
        self.progress_dialog.setAutoClose(True)
        self.progress_dialog.setValue(0)
        self.progress_dialog.canceled.connect(self.cancel_segmentation)
        self.progress_dialog.show()

        self.segmentation_thread = QThread(self)
        self.segmentation_worker = SegmentationWorker(self.segmenter, self.current_volume_hu)
        self.segmentation_worker.moveToThread(self.segmentation_thread)
        self.segmentation_worker.progress.connect(self._on_full_segmentation_progress)
        self.segmentation_worker.finished.connect(self._on_full_segmentation_finished)
        self.segmentation_worker.error.connect(self._on_segmentation_error)
        self.segmentation_thread.started.connect(self.segmentation_worker.run)
        # Подключаем finished потока для очистки ссылок
        self.segmentation_thread.finished.connect(self.segmentation_thread.deleteLater)
        self.segmentation_worker.finished.connect(self.segmentation_worker.deleteLater)
        # Подключаем finished потока к _clear_segmentation_thread_refs
        self.segmentation_thread.finished.connect(self._clear_segmentation_thread_refs)
        self.segmentation_thread.start()

    def _check_segmentation_prerequisites(self):
        """ Проверяет, доступны ли условия для выполнения сегментации. """
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
             logger.error("Попытка запуска сегментации, но модуль недоступен.")
             QMessageBox.critical(self, "Ошибка", "Модуль сегментации недоступен.")
             return False
        if self.segmenter.model is None:
            logger.warning("Модель сегментации не загружена.")
            # Убрали сообщение, т.к. модель должна загружаться автоматически
            # QMessageBox.warning(self, "Модель не загружена", "Загрузите модель (Файл -> Загрузить модель).")
            QMessageBox.warning(self, "Модель не загружена", "Модель сегментации не загружена автоматически. Проверьте наличие файла модели в папке ~/.pylungviewer/models.")
            return False
        if self.current_volume_hu is None:
            logger.warning("Нет данных серии для сегментации.")
            QMessageBox.warning(self, "Нет данных", "Загрузите серию DICOM для сегментации.")
            return False
        return True


    def _update_segmentation_controls_state(self):
        """
        Обновляет состояние кнопок и чекбокса сегментации
        в зависимости от наличия модели и загруженных данных.
        """
        model_is_loaded = SEGMENTATION_AVAILABLE and self.segmenter is not None and self.segmenter.model is not None
        data_is_loaded = self.current_volume_hu is not None
        full_mask_is_available = self.full_segmentation_mask_volume is not None

        # Кнопки "Сегм. срез" и "Сегм. весь объем" активны, если есть модель И данные
        self.run_segment_btn.setEnabled(model_is_loaded and data_is_loaded)
        self.run_full_segment_btn.setEnabled(model_is_loaded and data_is_loaded)

        # Чекбокс "Показать сегментацию" активен, если доступна полная маска ИЛИ
        # если есть временная маска для одного среза (self.segmentation_mask)
        # Но мы хотим, чтобы чекбокс управлял только видимостью полной маски.
        # Временная маска среза отображается автоматически после single_slice_segmentation.
        # Поэтому чекбокс активен только при наличии полной маски.
        self.segment_checkbox.setEnabled(full_mask_is_available)

        # Если чекбокс деактивируется (например, при смене серии или сбросе),
        # снимаем с него галочку и скрываем оверлей.
        if not self.segment_checkbox.isEnabled():
             self.segment_checkbox.setChecked(False)
             self._update_mask_overlay() # Убедимся, что маска скрыта


    @pyqtSlot(int, int)
    def _on_full_segmentation_progress(self, current, total):
        # ... (remains the same) ...
        self.segmentation_progress.emit(current, total)
        if self.progress_dialog:
            if self.progress_dialog.wasCanceled():
                self.cancel_segmentation()
                return
            self.progress_dialog.setMaximum(total)
            self.progress_dialog.setValue(current)


    @pyqtSlot(object)
    def _on_full_segmentation_finished(self, result_volume):
        """ Обрабатывает результат сегментации всего объема. """
        logger.info("Поток сегментации завершен (сигнал от воркера).")

        if self.progress_dialog:
            try:
                self.progress_dialog.canceled.disconnect(self.cancel_segmentation)
            except TypeError:
                pass
            self.progress_dialog.close()
            self.progress_dialog = None

        worker_cancelled = self.segmentation_worker is not None and self.segmentation_worker.is_cancelled

        if result_volume is not None and not worker_cancelled:
            self.full_segmentation_mask_volume = result_volume
            logger.info(f"Получен 3D массив масок формы: {result_volume.shape}")
            self.segmentation_status_update.emit("Сегментация всего объема завершена.")
            # Обновляем отображение текущего среза, что обновит и маску, и состояние чекбокса
            self._update_slice_display(self.current_slice_index)
            # Включаем чекбокс и ставим галочку
            # self.segment_checkbox.setEnabled(True) # Делается в _update_segmentation_controls_state
            self.segment_checkbox.setChecked(True)
        else:
            if worker_cancelled:
                 logger.info("Сегментация была отменена пользователем.")
                 self.segmentation_status_update.emit("Сегментация отменена.")
            else:
                 logger.error("Сегментация всего объема не удалась (результат None).")
                 self.segmentation_status_update.emit("Ошибка сегментации всего объема.")
            self.full_segmentation_mask_volume = None
            # Деактивируем чекбокс и снимаем галочку
            # self.segment_checkbox.setEnabled(False) # Делается в _update_segmentation_controls_state
            self.segment_checkbox.setChecked(False)
            self._update_mask_overlay() # Скрываем оверлей

        # Включаем кнопки обратно и обновляем их состояние
        self._set_segmentation_controls_enabled(True)
        self._update_segmentation_controls_state()
        # Очищаем ссылки здесь, т.k. finished потока уже сработал
        # _clear_segmentation_thread_refs вызывается по сигналу finished потока
        # self._clear_segmentation_thread_refs() # Убрали повторный вызов


    @pyqtSlot(str)
    def _on_segmentation_error(self, error_message):
        # ... (remains the same) ...
        logger.error(f"Ошибка из потока сегментации: {error_message}")
        # Показываем сообщение об ошибке пользователю
        QMessageBox.critical(self, "Ошибка сегментации", f"Произошла ошибка во время сегментации:\n{error_message}")
        self.segmentation_status_update.emit("Ошибка сегментации.")

        # Убеждаемся, что прогресс-диалог закрыт и кнопки включены
        if self.progress_dialog:
            try:
                self.progress_dialog.canceled.disconnect(self.cancel_segmentation)
            except TypeError:
                pass
            self.progress_dialog.close()
            self.progress_dialog = None

        self.full_segmentation_mask_volume = None
        self._update_segmentation_controls_state() # Обновляем состояние кнопок и чекбокса
        self._update_mask_overlay() # Скрываем оверлей
        # Очистка ссылок произойдет по сигналу finished потока
        # self._clear_segmentation_thread_refs() # Убрали повторный вызов


    @pyqtSlot()
    def cancel_segmentation(self):
        """ Попытка отмены текущей сегментации объема. """
        logger.info("Попытка отмены сегментации...")
        # Сначала устанавливаем флаг отмены в воркере
        if self.segmentation_worker is not None:
            self.segmentation_worker.cancel()
        # Затем пытаемся завершить поток
        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
             logger.info("Завершаем поток сегментации...")
             self.segmentation_thread.quit()
             # Не ждем здесь, чтобы не блокировать UI
             # Очистка ссылок произойдет по сигналу finished потока

        if self.progress_dialog:
            self.progress_dialog.setLabelText("Отмена сегментации...")
            # Не закрываем диалог сразу, даем потоку время завершиться
            # self.progress_dialog.close() # Убрали немедленное закрытие


    def _clear_segmentation_thread_refs(self):
        """ Очищает ссылки на поток и воркер сегментации, если они существуют. """
        logger.debug("Очистка ссылок на поток и воркер сегментации.")

        # Отключаем сигналы воркера, если он еще существует
        if self.segmentation_worker:
             try: self.segmentation_worker.progress.disconnect(self._on_full_segmentation_progress)
             except (TypeError, RuntimeError): pass # Добавил RuntimeError на случай уже удаленного объекта
             try: self.segmentation_worker.finished.disconnect(self._on_full_segmentation_finished)
             except (TypeError, RuntimeError): pass
             try: self.segmentation_worker.error.disconnect(self._on_segmentation_error)
             except (TypeError, RuntimeError): pass
             # Отключаем сигнал deleteLater, если он был подключен
             try: self.segmentation_worker.finished.disconnect(self.segmentation_worker.deleteLater)
             except (TypeError, RuntimeError): pass

        # Отключаем сигналы потока, если он еще существует
        if self.segmentation_thread:
            try: self.segmentation_thread.started.disconnect(self.segmentation_worker.run)
            except (TypeError, RuntimeError): pass # Может вызвать ошибку, если worker уже удален
            try: self.segmentation_thread.finished.disconnect(self.segmentation_thread.deleteLater)
            except (TypeError, RuntimeError): pass
            try: self.segmentation_thread.finished.disconnect(self._clear_segmentation_thread_refs)
            except (TypeError, RuntimeError): pass # Убрали рекурсивный вызов, но оставим disconnect на всякий случай

            # Убеждаемся, что поток завершен, если он еще работает
            # Этот блок может быть опасен, если поток уже удален.
            # Лучше положиться на deleteLater, подключенный к finished.
            # Но для надежности можно оставить с проверкой isRunning().
            # if self.segmentation_thread.isRunning():
            #      self.segmentation_thread.quit()
            #      if not self.segmentation_thread.wait(500): # Ждем немного
            #           logger.warning("Поток сегментации не завершился при очистке ссылок.")

        # Обнуляем ссылки ПОСЛЕ попытки отключения сигналов
        self.segmentation_thread = None
        self.segmentation_worker = None
        logger.debug("Ссылки на поток и воркер сегментации очищены.")


    def _update_mask_overlay(self):
        """ Обновляет отображение маски сегментации. """
        # Маска отображается, если доступна ПОЛНАЯ маска И чекбокс включен
        if self.full_segmentation_mask_volume is not None and self.segment_checkbox.isChecked():
            # Берем срез из полной маски, соответствующий текущему срезу изображения
            if self.current_slice_index < self.full_segmentation_mask_volume.shape[0]:
                 mask_slice_to_display = self.full_segmentation_mask_volume[self.current_slice_index]
                 self.mask_item.setImage(mask_slice_to_display.T, autoLevels=False, levels=(0, 1)) # Транспонируем
                 self.mask_item.setVisible(True)
                 # Убеждаемся, что маска выравнивается с изображением
                 img_bounds = self.img_item.boundingRect()
                 if img_bounds:
                     self.mask_item.setPos(img_bounds.topLeft())
                     self.mask_item.setTransform(self.img_item.transform())
            else:
                 # Если индекс среза вне диапазона полной маски
                 self.mask_item.clear()
                 self.mask_item.setVisible(False)
                 logger.warning(f"Индекс среза {self.current_slice_index} вне диапазона полной маски {self.full_segmentation_mask_volume.shape[0]}. Маска не отображена.")

        # Если есть временная маска для одного среза (после run_single_slice_segmentation)
        # и нет полной маски, отображаем временную маску.
        # Чекбокс "Показать сегментацию" при этом должен быть выключен/неактивен.
        elif self.segmentation_mask is not None and self.full_segmentation_mask_volume is None:
             self.mask_item.setImage(self.segmentation_mask.T, autoLevels=False, levels=(0, 1)) # Транспонируем
             self.mask_item.setVisible(True)
             # Убеждаемся, что маска выравнивается с изображением
             img_bounds = self.img_item.boundingRect()
             if img_bounds:
                 self.mask_item.setPos(img_bounds.topLeft())
                 self.mask_item.setTransform(self.img_item.transform())
             # Убедимся, что чекбокс выключен, если отображается только временная маска
             # self.segment_checkbox.setChecked(False) # Это может вызвать рекурсию
             # Вместо этого, просто убедимся, что чекбокс не активен
             # self.segment_checkbox.setEnabled(False) # Это делается в _update_segmentation_controls_state


        else:
            # Скрываем маску, если нет данных полной маски И нет временной маски ИЛИ чекбокс выключен
            self.mask_item.clear()
            self.mask_item.setVisible(False)

    # Метод _set_segmentation_controls_enabled теперь используется только для временного
    # отключения кнопок во время выполнения сегментации объема.
    # Общее состояние кнопок и чекбокса управляется _update_segmentation_controls_state.
    def _set_segmentation_controls_enabled(self, enabled):
        """ Временно включает/отключает кнопки сегментации (не чекбокс). """
        # Получаем текущее состояние доступности кнопок на основе наличия модели и данных
        model_is_loaded = SEGMENTATION_AVAILABLE and self.segmenter is not None and self.segmenter.model is not None
        data_is_loaded = self.current_volume_hu is not None
        can_segment = model_is_loaded and data_is_loaded

        # Устанавливаем состояние кнопок. Если enabled=False, отключаем их безусловно.
        # Если enabled=True, включаем только если can_segment=True.
        self.run_segment_btn.setEnabled(enabled and can_segment)
        self.run_full_segment_btn.setEnabled(enabled and can_segment)

        # Состояние чекбокса не меняем этим методом
        # self.segment_checkbox.setEnabled(...)

    # @pyqtSlot(QPointF) # Удален декоратор
    def _on_mouse_moved(self, pos):
        """
        Обработчик движения мыши для отображения HU.
        Позиция 'pos' находится в координатах сцены (graphics_widget).
        """
        # Проверяем, есть ли загруженные данные среза
        if self.current_pixel_data_hu is None:
            self.hu_label.setText("HU: N/A")
            return

        # Преобразуем координаты сцены в координаты изображения
        # Используем mapFromScene для преобразования из координат сцены в координаты ImageItem
        pos_in_img_item = self.img_item.mapFromScene(pos)

        # Получаем целочисленные координаты пикселя в системе координат ImageItem
        # Эти координаты должны соответствовать индексам numpy массива после учета setPixelSize
        x = int(pos_in_img_item.x())
        y = int(pos_in_img_item.y())

        # Получаем размеры текущего среза (height, width)
        height, width = self.current_pixel_data_hu.shape

        # Проверяем, находится ли курсор внутри границ изображения по индексам массива
        # Учитываем, что y соответствует строкам (height), x - столбцам (width)
        if 0 <= y < height and 0 <= x < width:
            try:
                # Получаем значение HU из исходных данных по индексам [строка, столбец]
                hu_value = self.current_pixel_data_hu[y, x]
                self.hu_label.setText(f"HU: {hu_value:.1f}") # Форматируем до 1 знака после запятой
            except IndexError:
                 # Этого не должно произойти, если проверки границ выше верны,
                 # но на всякий случай обрабатываем
                 self.hu_label.setText("HU: N/A (вне границ)")
            except Exception as e:
                 logger.error(f"Ошибка при получении значения HU: {e}")
                 self.hu_label.setText("HU: Ошибка")
        else:
            # Если курсор вне изображения
            self.hu_label.setText("HU: N/A (вне изображения)")

