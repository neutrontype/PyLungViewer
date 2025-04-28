#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Панель просмотра DICOM изображений для приложения PyLungViewer.
(Версия с интеграцией сегментации + Сегментация всего объема - Исправлена активация чекбокса для одного среза)
"""

import logging
import os
import traceback
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
from pyqtgraph import ImageView, ImageItem

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
    print("Полный стек ошибки:")
    traceback.print_exc()
    print("--------------------------------------------------")
    logger.warning("Модуль сегментации (pylungviewer.core.segmentation) не найден или его зависимости отсутствуют.")

from pylungviewer.utils.window_presets import WindowPresets

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
                    self.segmenter.signals.progress.connect(self.report_progress)
                    signals_connected = True
                else:
                    logger.warning("Атрибут 'progress' не найден у объекта segmenter.signals.")
            except (AttributeError, TypeError):
                 logger.warning("Не удалось подключить сигнал прогресса сегментатора.")

        try:
            result = self.segmenter.predict_volume(self.volume_hu)
            if not self.is_cancelled:
                self.finished.emit(result)
            else:
                logger.info("Сегментация отменена воркером, результат не передается.")
                self.finished.emit(None)
        except Exception as e:
            logger.error(f"Ошибка в потоке сегментации: {e}", exc_info=True)
            self.error.emit(f"Ошибка во время сегментации: {e}")
            self.finished.emit(None)
        finally:
             if signals_connected and hasattr(self.segmenter, 'signals') and hasattr(self.segmenter.signals, 'progress'):
                 try:
                     self.segmenter.signals.progress.disconnect(self.report_progress)
                 except (TypeError, AttributeError):
                     pass

    def report_progress(self, current, total):
        if not self.is_cancelled:
            self.progress.emit(current, total)

    def cancel(self):
        logger.info("Получен запрос на отмену сегментации.")
        self.is_cancelled = True


# --- Основной класс панели ---
class ViewerPanel(QWidget):
    """Панель просмотра DICOM изображений с поддержкой сегментации."""

    slice_changed = pyqtSignal(int)
    segmentation_progress = pyqtSignal(int, int)
    segmentation_status_update = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_series = None
        self.current_slice_index = 0
        self.current_pixel_data_hu = None
        self.current_volume_hu = None
        self.segmentation_mask = None # Маска для ТЕКУЩЕГО среза
        self.full_segmentation_mask_volume = None # 3D массив масок

        if SEGMENTATION_AVAILABLE:
            self.segmenter = LungSegmenter()
        else:
            self.segmenter = None
        self.segmentation_thread = None
        self.segmentation_worker = None
        self.progress_dialog = None

        self.touch_start_pos = None
        self._init_ui()
        self.installEventFilter(self)
        logger.info("Панель просмотра инициализирована (с поддержкой сегментации)")

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
        main_layout.addWidget(info_panel)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        main_layout.addWidget(self.graphics_widget, 1)
        self.view_box = self.graphics_widget.addViewBox(row=0, col=0)
        self.view_box.setAspectLocked(True)
        self.view_box.invertY(True)
        self.view_box.setMouseEnabled(x=True, y=True)
        self.img_item = pg.ImageItem()
        self.view_box.addItem(self.img_item)
        self.mask_item = pg.ImageItem()
        self.mask_item.setCompositionMode(pg.QtGui.QPainter.CompositionMode_Plus)
        lut = np.array([[0, 0, 0, 0], [255, 0, 0, 150]], dtype=np.uint8)
        self.mask_item.setLookupTable(lut)
        self.mask_item.setVisible(False)
        self.view_box.addItem(self.mask_item)

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
        self.segment_checkbox.setEnabled(False)

        self.run_segment_btn = QPushButton("Сегм. срез")
        self.run_segment_btn.clicked.connect(self.run_single_slice_segmentation)
        self.run_segment_btn.setEnabled(False)

        self.run_full_segment_btn = QPushButton("Сегм. весь объем")
        self.run_full_segment_btn.clicked.connect(self.start_full_segmentation)
        self.run_full_segment_btn.setEnabled(False)

        if not SEGMENTATION_AVAILABLE:
            self.segment_checkbox.setToolTip("Модуль сегментации или его зависимости не найдены")
            self.run_segment_btn.setToolTip("Модуль сегментации или его зависимости не найдены")
            self.run_full_segment_btn.setToolTip("Модуль сегментации или его зависимости не найдены")
        else:
             self.segment_checkbox.setToolTip("Показать/скрыть маску сегментации") # Изменено описание
             self.run_segment_btn.setToolTip("Сегментировать только текущий срез (требуется модель)")
             self.run_full_segment_btn.setToolTip("Сегментировать все срезы серии (требуется модель)")

        segment_panel.addWidget(self.segment_checkbox)
        segment_panel.addStretch(1)
        segment_panel.addWidget(self.run_segment_btn)
        segment_panel.addWidget(self.run_full_segment_btn)
        bottom_layout.addLayout(segment_panel)

        main_layout.addWidget(bottom_panel)
        self._show_placeholder()

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
        self.segment_checkbox.setEnabled(False)
        self.segment_checkbox.setChecked(False)
        self.run_segment_btn.setEnabled(False)
        self.run_full_segment_btn.setEnabled(False)
        self.info_label.setText("Нет загруженных данных")
        self.current_volume_hu = None
        self.current_pixel_data_hu = None
        self.segmentation_mask = None
        self.full_segmentation_mask_volume = None

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
                     pass
            elif event.type() == QEvent.MouseButtonRelease:
                 if event.button() == Qt.LeftButton and self.touch_start_pos is not None:
                     end_pos = event.pos()
                     delta = end_pos - self.touch_start_pos
                     swipe_threshold = 50
                     if abs(delta.x()) > abs(delta.y()) and abs(delta.x()) > swipe_threshold:
                         if delta.x() > 0: self._on_prev_slice()
                         else: self._on_next_slice()
                         self.touch_start_pos = None
                         return True
                     self.touch_start_pos = None
                 self.touch_start_pos = None
        return super().eventFilter(obj, event)


    def _handle_wheel_scroll(self, event):
        # ... (remains the same) ...
        if self.current_series is None or not self.current_series.get('files', []): return
        current_time = QDateTime.currentMSecsSinceEpoch()
        scroll_interval = 50
        if not hasattr(self, '_last_scroll_time'): self._last_scroll_time = 0
        if current_time - self._last_scroll_time >= scroll_interval:
            self._last_scroll_time = current_time
            delta = event.angleDelta().y()
            step = 1
            total_slices = len(self.current_series.get('files', []))
            if total_slices > 100: step = max(1, total_slices // 50)
            current_index = self.current_slice_index
            if delta > 0: new_index = max(0, current_index - step)
            else: new_index = min(total_slices - 1, current_index + step)
            if new_index != current_index: self.slice_slider.setValue(new_index)


    def load_segmentation_model(self, model_path):
        # ... (remains the same) ...
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
             logger.error("Попытка загрузить модель, но модуль сегментации недоступен.")
             QMessageBox.critical(self, "Ошибка", "Модуль сегментации недоступен. Проверьте зависимости.")
             return
        if self.segmenter.load_model(model_path):
            model_loaded = True
            logger.info(f"Модель сегментации загружена: {model_path}")
        else:
            model_loaded = False
            QMessageBox.critical(self, "Ошибка загрузки модели", f"Не удалось загрузить модель из файла:\n{model_path}\n\nПроверьте логи.")
            logger.error("Не удалось загрузить модель сегментации.")
        series_loaded = self.current_series is not None
        self.run_segment_btn.setEnabled(model_loaded and series_loaded)
        self.run_full_segment_btn.setEnabled(model_loaded and series_loaded)


    def load_series(self, series_data):
        """Загрузка новой серии, с остановкой предыдущей сегментации."""
        logger.info("Загрузка новой серии...")
        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
            logger.warning("Предыдущая сегментация еще выполняется. Попытка отмены...")
            self.cancel_segmentation()
            if self.segmentation_thread is not None:
                 self.segmentation_thread.quit()
                 if not self.segmentation_thread.wait(1000):
                      logger.warning("Поток сегментации не завершился вовремя.")
                 self._clear_segmentation_thread_refs()
        elif self.segmentation_thread is not None:
             self._clear_segmentation_thread_refs()

        self._view_reset_done = False
        self._show_placeholder()
        self.current_series = series_data
        if series_data is None or not series_data.get('files', []):
            logger.warning("Попытка загрузить пустую серию")
            return
        files = series_data.get('files', [])
        slice_count = len(files)
        logger.info(f"Загрузка {slice_count} срезов в память...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        volume_hu_list = []
        first_ds = None
        try:
            for i, file_meta in enumerate(files):
                file_path = file_meta.get('file_path')
                ds = pydicom.dcmread(file_path)
                if i == 0: first_ds = ds
                pixel_data = ds.pixel_array.astype(np.float32)
                slope = getattr(ds, 'RescaleSlope', 1.0)
                intercept = getattr(ds, 'RescaleIntercept', 0.0)
                if not isinstance(slope, (int, float)): slope = 1.0
                if not isinstance(intercept, (int, float)): intercept = 0.0
                pixel_data = pixel_data * float(slope) + float(intercept)
                volume_hu_list.append(pixel_data)
            self.current_volume_hu = np.stack(volume_hu_list, axis=0)
            logger.info(f"Объем загружен. Форма: {self.current_volume_hu.shape}")
        except Exception as e:
            logger.error(f"Ошибка при загрузке объема серии: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка загрузки серии", f"Не удалось загрузить данные серии:\n{str(e)}")
            self._show_placeholder()
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.slice_slider.setMinimum(0)
        self.slice_slider.setMaximum(slice_count - 1)
        self.slice_slider.setValue(slice_count // 2)
        self.slice_slider.setEnabled(True)
        self.prev_slice_btn.setEnabled(True)
        self.next_slice_btn.setEnabled(True)

        model_loaded = SEGMENTATION_AVAILABLE and self.segmenter is not None and self.segmenter.model is not None
        self.run_segment_btn.setEnabled(model_loaded)
        self.run_full_segment_btn.setEnabled(model_loaded)
        self.segment_checkbox.setEnabled(False)

        self._update_slice_display(self.slice_slider.value())
        patient_name_obj = getattr(first_ds, 'PatientName', 'N/A') if first_ds else 'N/A'
        patient_name = str(patient_name_obj)
        study_desc = getattr(first_ds, 'StudyDescription', '') if first_ds else ''
        series_desc = series_data.get('description', 'Неизвестно')
        modality = series_data.get('modality', '')
        self.info_label.setText(f"Пациент: {patient_name} | Исслед.: {study_desc} | Серия: {series_desc} ({modality})")
        logger.info(f"Загружена серия '{series_desc}' из {slice_count} изображений")


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
        window_center, window_width = WindowPresets.get_preset("Легочное")
        display_image_hu = self.current_pixel_data_hu
        self.img_item.setImage(display_image_hu.T)
        min_level = window_center - window_width / 2.0
        max_level = window_center + window_width / 2.0
        self.img_item.setLevels([min_level, max_level])

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
            self.segment_checkbox.setEnabled(True) # Чекбокс активен
        elif is_new_slice:
            # Если полной маски нет И мы перешли на НОВЫЙ срез,
            # сбрасываем временную маску и деактивируем чекбокс
            self.segmentation_mask = None
            self.segment_checkbox.setEnabled(False)
            self.segment_checkbox.setChecked(False)
        # Если полной маски нет и срез тот же (например, после run_single_slice),
        # оставляем self.segmentation_mask и состояние чекбокса как есть

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

    @pyqtSlot()
    def run_single_slice_segmentation(self):
        """ Запускает сегментацию только для текущего среза. """
        if not self._check_segmentation_prerequisites(): return

        logger.info(f"Запуск сегментации для среза {self.current_slice_index + 1}...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Сбрасываем полную маску, так как результат будет только для одного среза
            self.full_segmentation_mask_volume = None

            # Выполняем предсказание
            single_mask = self.segmenter.predict(self.current_pixel_data_hu)

            if single_mask is not None:
                logger.info("Сегментация среза завершена успешно.")
                # --- Исправлено: Сохраняем маску и активируем чекбокс ---
                self.segmentation_mask = single_mask # Сохраняем маску для текущего среза
                self.segment_checkbox.setEnabled(True) # Активируем чекбокс
                self.segment_checkbox.setChecked(True) # Включаем его
                self._update_mask_overlay() # Отображаем маску
                # -------------------------------------------------------
            else:
                logger.error("Сегментация среза не удалась.")
                QMessageBox.critical(self, "Ошибка сегментации", "Не удалось выполнить сегментацию среза.")
                # Сбрасываем маску и чекбокс
                self.segmentation_mask = None
                self.segment_checkbox.setEnabled(False)
                self.segment_checkbox.setChecked(False)
                self._update_mask_overlay() # Скрываем оверлей
        finally:
            QApplication.restoreOverrideCursor()

    @pyqtSlot()
    def start_full_segmentation(self):
        """ Запускает сегментацию всего объема в фоновом потоке. """
        if not self._check_segmentation_prerequisites(): return
        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
            QMessageBox.information(self, "Сегментация", "Сегментация всего объема уже запущена.")
            return

        logger.info("Запуск сегментации всего объема...")
        self.segmentation_status_update.emit("Сегментация всего объема...")
        self._set_segmentation_controls_enabled(False)

        num_slices = self.current_volume_hu.shape[0] if self.current_volume_hu is not None else 0
        parent_widget = self.parent() if self.parent() else self
        self.progress_dialog = QProgressDialog("Сегментация всего объема...", "Отмена", 0, num_slices, parent_widget)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(1000)
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
        self.segmentation_thread.finished.connect(self.segmentation_thread.deleteLater)
        self.segmentation_worker.finished.connect(self.segmentation_worker.deleteLater)
        self.segmentation_thread.finished.connect(self._clear_segmentation_thread_refs)
        self.segmentation_thread.start()

    def _check_segmentation_prerequisites(self):
        # ... (remains the same) ...
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
             logger.error("Попытка запуска сегментации, но модуль недоступен.")
             QMessageBox.critical(self, "Ошибка", "Модуль сегментации недоступен.")
             return False
        if self.segmenter.model is None:
            logger.warning("Модель сегментации не загружена.")
            QMessageBox.warning(self, "Модель не загружена", "Загрузите модель (Файл -> Загрузить модель).")
            return False
        if self.current_volume_hu is None:
            logger.warning("Нет данных серии для сегментации.")
            QMessageBox.warning(self, "Нет данных", "Загрузите серию DICOM для сегментации.")
            return False
        return True


    def _set_segmentation_controls_enabled(self, enabled):
        # ... (remains the same) ...
        can_segment = (SEGMENTATION_AVAILABLE and
                       self.segmenter is not None and
                       self.segmenter.model is not None and
                       self.current_volume_hu is not None)
        self.run_segment_btn.setEnabled(enabled and can_segment)
        self.run_full_segment_btn.setEnabled(enabled and can_segment)
        self.segment_checkbox.setEnabled(self.full_segmentation_mask_volume is not None)


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
        logger.info("Поток сегментации завершен.")

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
            self._update_slice_display(self.current_slice_index) # Обновит и чекбокс, и маску
            # self.segment_checkbox.setEnabled(True) # Делается в _update_slice_display
            self.segment_checkbox.setChecked(True) # Включаем по умолчанию
        else:
            if worker_cancelled:
                 logger.info("Сегментация была отменена пользователем.")
                 self.segmentation_status_update.emit("Сегментация отменена.")
            else:
                 logger.error("Сегментация всего объема не удалась (результат None).")
                 self.segmentation_status_update.emit("Ошибка сегментации всего объема.")
            self.full_segmentation_mask_volume = None
            # self.segment_checkbox.setEnabled(False) # Делается в _update_slice_display
            self.segment_checkbox.setChecked(False)
            self._update_mask_overlay()

        # Включаем кнопки обратно
        self._set_segmentation_controls_enabled(True)
        # Очищаем ссылки здесь, т.к. finished потока уже сработал
        self._clear_segmentation_thread_refs()


    @pyqtSlot(str)
    def _on_segmentation_error(self, error_message):
        # ... (remains the same) ...
        logger.error(f"Ошибка из потока сегментации: {error_message}")


    @pyqtSlot()
    def cancel_segmentation(self):
        # ... (remains the same) ...
        logger.info("Попытка отмены сегментации...")
        if self.segmentation_worker is not None:
            self.segmentation_worker.cancel()
        if self.progress_dialog:
            self.progress_dialog.setLabelText("Отмена сегментации...")
        if self.segmentation_thread and self.segmentation_thread.isRunning():
             self.segmentation_thread.quit()
        self._clear_segmentation_thread_refs()


    def _clear_segmentation_thread_refs(self):
        # ... (remains the same) ...
        self.segmentation_thread = None
        self.segmentation_worker = None


    def _update_mask_overlay(self):
        """ Обновляет отображение маски сегментации. """
        # Маска отображается, если она есть (self.segmentation_mask) И чекбокс включен
        if self.segmentation_mask is not None and self.segment_checkbox.isChecked():
            self.mask_item.setImage(self.segmentation_mask.T, autoLevels=False, levels=(0, 1))
            self.mask_item.setVisible(True)
            img_bounds = self.img_item.boundingRect()
            if img_bounds:
                self.mask_item.setPos(img_bounds.topLeft())
                self.mask_item.setTransform(self.img_item.transform())
        else:
            # Скрываем маску, если нет данных или чекбокс выключен
            self.mask_item.clear()
            self.mask_item.setVisible(False)

    @pyqtSlot(bool)
    def _on_segment_toggle(self, checked):
        """ Обработчик переключения чекбокса отображения сегментации. """
        # Просто обновляем оверлей на основе нового состояния чекбокса
        self._update_mask_overlay()

