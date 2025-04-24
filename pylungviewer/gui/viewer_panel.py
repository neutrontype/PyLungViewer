#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Панель просмотра DICOM изображений для приложения PyLungViewer.
(Версия с интеграцией сегментации)
"""

import logging
import os # Добавлено
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QPushButton, QFrame, QApplication,
    QCheckBox, QMessageBox # Добавлено QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QEvent, QObject, QDateTime, QPointF # Добавлено QPointF
from PyQt5.QtGui import QIcon, QColor # Добавлено QColor
import pyqtgraph as pg

# Для визуализации изображений
import numpy as np
import pydicom
from pyqtgraph import ImageView, ImageItem # Добавлено ImageItem

# --- Определяем logger ДО блока try-except ---
logger = logging.getLogger(__name__)
# -------------------------------------------

# Импорт модуля сегментации
# Добавляем обработку возможного ImportError
try:
    from pylungviewer.core.segmentation import LungSegmenter
    SEGMENTATION_AVAILABLE = True
except ImportError:
    LungSegmenter = None # Определяем как None, если импорт не удался
    SEGMENTATION_AVAILABLE = False
    # Теперь logger определен и его можно использовать
    logger.warning("Модуль сегментации (pylungviewer.core.segmentation) не найден или его зависимости отсутствуют.")

from pylungviewer.utils.window_presets import WindowPresets # Импортируем пресеты


class WheelEventFilter(QObject):
    """Фильтр событий колеса мыши для перенаправления их в ViewerPanel."""

    def __init__(self, viewer_panel):
        super().__init__()
        self.viewer_panel = viewer_panel
        self.last_scroll_time = 0

    def eventFilter(self, obj, event):
        # Фильтруем события только если они происходят над ViewerPanel или его дочерними виджетами
        is_target_widget = False
        if isinstance(obj, QWidget):
            widget = obj
            while widget:
                if widget == self.viewer_panel:
                    is_target_widget = True
                    break
                widget = widget.parent()

        if is_target_widget and event.type() == QEvent.Wheel:
            # Получаем текущее время
            current_time = QDateTime.currentMSecsSinceEpoch()

            # Базовый интервал прокрутки в мс
            scroll_interval = 50 # Уменьшим интервал для большей отзывчивости

            # Если прошло достаточно времени с последней прокрутки
            if current_time - self.last_scroll_time >= scroll_interval:
                # Обновляем время последней прокрутки
                self.last_scroll_time = current_time

                # Определяем направление прокрутки
                delta = event.angleDelta().y()

                # Определяем шаг прокрутки на основе размера серии
                step = 1  # По умолчанию перемещаемся на 1 слайс
                if self.viewer_panel.current_series and self.viewer_panel.current_series.get('files'):
                    total_slices = len(self.viewer_panel.current_series.get('files', []))

                    # Увеличиваем шаг для больших серий
                    if total_slices > 100:
                        step = max(1, total_slices // 50)

                # Определяем новый индекс
                current_index = self.viewer_panel.current_slice_index
                if delta > 0:
                    new_index = max(0, current_index - step)
                else:
                    files = self.viewer_panel.current_series.get('files', [])
                    new_index = min(len(files) - 1 if files else 0, current_index + step)

                # Устанавливаем новое значение слайдера
                if new_index != current_index:
                    self.viewer_panel.slice_slider.setValue(new_index)

            # Перехватываем все события прокрутки над панелью
            return True

        # Для остальных событий возвращаем False, чтобы они обрабатывались дальше
        return False


class ViewerPanel(QWidget):
    """Панель просмотра DICOM изображений с поддержкой сегментации."""

    # Сигналы для коммуникации с другими компонентами
    slice_changed = pyqtSignal(int)
    segmentation_requested = pyqtSignal() # Сигнал для запроса сегментации

    def __init__(self, parent=None):
        """
        Инициализация панели просмотра.

        Args:
            parent: Родительский виджет.
        """
        super().__init__(parent)

        # Текущие данные
        self.current_series = None
        self.current_slice_index = 0
        self.current_pixel_data_hu = None # Храним текущий срез в HU
        self.current_volume_hu = None # Храним весь объем в HU
        self.segmentation_mask = None # Храним маску сегментации для текущего среза

        # Модуль сегментации
        if SEGMENTATION_AVAILABLE:
            self.segmenter = LungSegmenter() # Инициализируем без модели
        else:
            self.segmenter = None # Сегментация недоступна

        # Для отслеживания свайпов на тачпаде
        self.touch_start_pos = None # Используем QPointF

        # Инициализация UI
        self._init_ui()

        # Устанавливаем фильтр на саму панель просмотра
        self.installEventFilter(self)

        logger.info("Панель просмотра инициализирована (с поддержкой сегментации)")

    def _init_ui(self):
        """Инициализация пользовательского интерфейса."""
        # Главный layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0) # Убираем промежутки

        # Верхняя панель информации (оставляем как есть)
        info_panel = QWidget()
        info_layout = QHBoxLayout(info_panel)
        info_layout.setContentsMargins(5, 5, 5, 5)
        self.info_label = QLabel("Нет загруженных данных")
        info_layout.addWidget(self.info_label)
        main_layout.addWidget(info_panel)

        # --- Используем GraphicsLayoutWidget для лучшего контроля ---
        self.graphics_widget = pg.GraphicsLayoutWidget()
        main_layout.addWidget(self.graphics_widget, 1) # Растягиваем

        # Создаем ViewBox
        self.view_box = self.graphics_widget.addViewBox(row=0, col=0)
        self.view_box.setAspectLocked(True) # Сохраняем пропорции
        self.view_box.invertY(True) # Инвертируем Y для соответствия отображению ImageView
        self.view_box.setMouseEnabled(x=True, y=True) # Включаем панорамирование/масштаб мышью

        # Создаем ImageItem для основного изображения
        self.img_item = pg.ImageItem()
        self.view_box.addItem(self.img_item)

        # Создаем ImageItem для маски сегментации (будет добавлен позже)
        self.mask_item = pg.ImageItem()
        self.mask_item.setCompositionMode(pg.QtGui.QPainter.CompositionMode_Plus) # Режим наложения
        # Устанавливаем цвет для маски (например, полупрозрачный красный)
        lut = np.array([[0, 0, 0, 0], [255, 0, 0, 150]], dtype=np.uint8) # RGBA
        self.mask_item.setLookupTable(lut)
        self.mask_item.setVisible(False) # Изначально скрыт
        self.view_box.addItem(self.mask_item)
        #-------------------------------------------------------------

        # Нижняя панель с слайдером и контролами сегментации
        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel) # Используем QVBoxLayout
        bottom_layout.setContentsMargins(5, 5, 5, 5)
        bottom_layout.setSpacing(5)

        # Панель слайдера
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
        slider_panel.addWidget(self.slice_slider, 1) # Растягиваем слайдер
        slider_panel.addWidget(self.slice_label)
        slider_panel.addWidget(self.next_slice_btn)
        bottom_layout.addLayout(slider_panel)

        # Панель сегментации (создаем, даже если модуль недоступен, но делаем неактивной)
        segment_panel = QHBoxLayout()
        self.segment_checkbox = QCheckBox("Показать сегментацию")
        self.segment_checkbox.toggled.connect(self._on_segment_toggle)
        self.segment_checkbox.setEnabled(False) # Изначально неактивен

        self.run_segment_btn = QPushButton("Сегментировать срез")
        self.run_segment_btn.clicked.connect(self.run_segmentation)
        self.run_segment_btn.setEnabled(False) # Изначально неактивен

        # Если сегментация недоступна, отключаем элементы управления сегментацией
        if not SEGMENTATION_AVAILABLE:
            self.segment_checkbox.setToolTip("Модуль сегментации или его зависимости не найдены")
            self.run_segment_btn.setToolTip("Модуль сегментации или его зависимости не найдены")
        else:
             self.segment_checkbox.setToolTip("Показать/скрыть маску сегментации")
             self.run_segment_btn.setToolTip("Запустить сегментацию для текущего среза (требуется загруженная модель)")


        segment_panel.addWidget(self.segment_checkbox)
        segment_panel.addStretch(1)
        segment_panel.addWidget(self.run_segment_btn)
        bottom_layout.addLayout(segment_panel)

        main_layout.addWidget(bottom_panel)

        # Добавляем placeholder при отсутствии данных
        self._show_placeholder()

    def _show_placeholder(self):
        """Отображение заглушки при отсутствии данных."""
        placeholder_image = np.zeros((512, 512), dtype=np.uint8)
        self.img_item.setImage(placeholder_image)
        self.mask_item.clear() # Очищаем маску
        self.mask_item.setVisible(False)
        self.view_box.autoRange() # Сбрасываем вид

        # Отключаем контролы
        self.slice_slider.setEnabled(False)
        self.prev_slice_btn.setEnabled(False)
        self.next_slice_btn.setEnabled(False)
        self.segment_checkbox.setEnabled(False)
        self.segment_checkbox.setChecked(False)
        self.run_segment_btn.setEnabled(False)
        self.info_label.setText("Нет загруженных данных")
        self.current_volume_hu = None
        self.current_pixel_data_hu = None
        self.segmentation_mask = None

    def eventFilter(self, obj, event):
        """Обработчик событий, установленный на саму панель."""
        if obj is self: # Применяем только к событиям самой панели
            if event.type() == QEvent.Wheel:
                # Обработка колеса мыши для смены срезов
                self._handle_wheel_scroll(event)
                return True # Событие обработано

            elif event.type() == QEvent.MouseButtonPress:
                 # Начало панорамирования/свайпа
                 if event.button() == Qt.LeftButton:
                     self.touch_start_pos = event.pos()
                     # logger.debug(f"Touch Start: {self.touch_start_pos}")
                     return True # Перехватываем для возможного свайпа

            elif event.type() == QEvent.MouseMove:
                 # Движение мыши (панорамирование)
                 if self.touch_start_pos is not None and event.buttons() & Qt.LeftButton:
                     # Стандартное панорамирование ViewBox обычно работает,
                     # но если нужно кастомное поведение, можно добавить здесь.
                     # logger.debug(f"Touch Move: {event.pos()}")
                     pass # Позволяем ViewBox обрабатывать панорамирование

            elif event.type() == QEvent.MouseButtonRelease:
                 # Конец панорамирования/свайпа
                 if event.button() == Qt.LeftButton and self.touch_start_pos is not None:
                     end_pos = event.pos()
                     delta = end_pos - self.touch_start_pos
                     # logger.debug(f"Touch End: {end_pos}, Delta: {delta}")

                     # Проверяем, был ли это свайп (больше горизонтальное смещение, чем вертикальное)
                     swipe_threshold = 50 # Пиксели
                     if abs(delta.x()) > abs(delta.y()) and abs(delta.x()) > swipe_threshold:
                         if delta.x() > 0: # Свайп вправо
                             self._on_prev_slice()
                             # logger.debug("Swipe Right -> Prev Slice")
                         else: # Свайп влево
                             self._on_next_slice()
                             # logger.debug("Swipe Left -> Next Slice")
                         self.touch_start_pos = None # Сброс после свайпа
                         return True # Свайп обработан

                     # Если не свайп, сбрасываем начальную позицию
                     self.touch_start_pos = None
                     # Возвращаем False, чтобы ViewBox мог обработать клик/масштаб, если нужно
                     # return False

                 self.touch_start_pos = None # Сброс на всякий случай

        # Для всех остальных событий передаем управление дальше
        return super().eventFilter(obj, event)

    def _handle_wheel_scroll(self, event):
        """Обработка прокрутки колеса мыши для смены срезов."""
        if self.current_series is None or not self.current_series.get('files', []):
            return

        # Получаем текущее время
        current_time = QDateTime.currentMSecsSinceEpoch()
        scroll_interval = 50 # мс

        # Используем атрибут _last_scroll_time, созданный в __init__ или при первом вызове
        if not hasattr(self, '_last_scroll_time'):
            self._last_scroll_time = 0

        if current_time - self._last_scroll_time >= scroll_interval:
            self._last_scroll_time = current_time
            delta = event.angleDelta().y()
            step = 1
            total_slices = len(self.current_series.get('files', []))
            if total_slices > 100:
                step = max(1, total_slices // 50)

            current_index = self.current_slice_index
            if delta > 0:
                new_index = max(0, current_index - step)
            else:
                new_index = min(total_slices - 1, current_index + step)

            if new_index != current_index:
                self.slice_slider.setValue(new_index)


    def load_segmentation_model(self, model_path):
        """
        Загружает модель сегментации.

        Args:
            model_path (str): Путь к файлу .pth модели.
        """
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
             logger.error("Попытка загрузить модель, но модуль сегментации недоступен.")
             QMessageBox.critical(self, "Ошибка", "Модуль сегментации недоступен. Проверьте зависимости.")
             return

        if self.segmenter.load_model(model_path):
            # Активируем кнопку сегментации, если серия уже загружена
            self.run_segment_btn.setEnabled(self.current_series is not None)
            logger.info(f"Модель сегментации загружена: {model_path}")
        else:
            self.run_segment_btn.setEnabled(False)
            QMessageBox.critical(self, "Ошибка загрузки модели", f"Не удалось загрузить модель из файла:\n{model_path}\n\nПроверьте логи для детальной информации.")
            logger.error("Не удалось загрузить модель сегментации.")


    def load_series(self, series_data):
        """
        Загрузка серии DICOM изображений для отображения и подготовки к сегментации.

        Args:
            series_data: Данные серии DICOM снимков.
        """
        # Сбрасываем флаг сброса вида для новой серии
        self._view_reset_done = False
        self._show_placeholder() # Сброс перед загрузкой новой серии
        self.current_series = series_data

        if series_data is None or not series_data.get('files', []):
            logger.warning("Попытка загрузить пустую серию")
            return

        files = series_data.get('files', [])
        slice_count = len(files)

        # --- Загрузка всего объема в память (в HU) ---
        logger.info(f"Загрузка {slice_count} срезов в память...")
        QApplication.setOverrideCursor(Qt.WaitCursor) # Курсор ожидания
        volume_hu_list = []
        first_ds = None
        try:
            for i, file_meta in enumerate(files):
                file_path = file_meta.get('file_path')
                ds = pydicom.dcmread(file_path)
                if i == 0: first_ds = ds # Сохраняем метаданные первого среза

                pixel_data = ds.pixel_array.astype(np.float32)

                # Применяем Rescale Slope/Intercept
                slope = getattr(ds, 'RescaleSlope', 1.0)
                intercept = getattr(ds, 'RescaleIntercept', 0.0)
                # Проверяем типы перед умножением/сложением
                if not isinstance(slope, (int, float)): slope = 1.0
                if not isinstance(intercept, (int, float)): intercept = 0.0

                pixel_data = pixel_data * float(slope) + float(intercept)
                volume_hu_list.append(pixel_data)

                # Опционально: можно добавить прогресс-бар сюда

            self.current_volume_hu = np.stack(volume_hu_list, axis=0)
            logger.info(f"Объем загружен. Форма: {self.current_volume_hu.shape}")

        except Exception as e:
            logger.error(f"Ошибка при загрузке объема серии: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка загрузки серии", f"Не удалось загрузить данные серии:\n{str(e)}")
            self._show_placeholder()
            return
        finally:
            QApplication.restoreOverrideCursor() # Возвращаем курсор
        # ---------------------------------------------

        # Настраиваем слайдер
        self.slice_slider.setMinimum(0)
        self.slice_slider.setMaximum(slice_count - 1)
        # Устанавливаем значение после настройки min/max
        self.slice_slider.setValue(slice_count // 2) # Начинаем с середины

        # Активируем контролы
        self.slice_slider.setEnabled(True)
        self.prev_slice_btn.setEnabled(True)
        self.next_slice_btn.setEnabled(True)
        # Активируем кнопку сегментации, если модуль доступен и модель загружена
        self.run_segment_btn.setEnabled(SEGMENTATION_AVAILABLE and self.segmenter is not None and self.segmenter.model is not None)
        self.segment_checkbox.setEnabled(False) # Чекбокс активен только после сегментации

        # Отображаем первый (или средний) срез
        # Вызываем _update_slice_display *после* настройки слайдера
        self._update_slice_display(self.slice_slider.value())

        # Обновляем информацию
        series_desc = series_data.get('description', 'Неизвестно')
        modality = series_data.get('modality', '')
        # Получаем информацию из первого датасета (если он был загружен)
        patient_name_obj = getattr(first_ds, 'PatientName', 'N/A') if first_ds else 'N/A'
        patient_name = str(patient_name_obj) # Преобразуем PersonName в строку
        study_desc = getattr(first_ds, 'StudyDescription', '') if first_ds else ''
        self.info_label.setText(f"Пациент: {patient_name} | Исслед.: {study_desc} | Серия: {series_desc} ({modality})")
        logger.info(f"Загружена серия '{series_desc}' из {slice_count} изображений")


    def _update_slice_display(self, slice_index):
        """
        Обновление отображения для указанного среза, включая маску.

        Args:
            slice_index: Индекс среза для отображения.
        """
        if self.current_volume_hu is None:
            # logger.warning("Объем данных (HU) не загружен.")
            return

        if slice_index < 0 or slice_index >= self.current_volume_hu.shape[0]:
            logger.warning(f"Индекс среза вне диапазона: {slice_index}")
            return

        self.current_slice_index = slice_index

        # Получаем срез из загруженного объема
        self.current_pixel_data_hu = self.current_volume_hu[slice_index]

        # --- Отображение основного изображения ---
        # Используем стандартное окно "Легочное" для отображения
        # Можно добавить выбор пресетов окна
        window_center, window_width = WindowPresets.get_preset("Легочное")
        # Применяем окно к данным в HU
        display_image_hu = self.current_pixel_data_hu

        # Отображаем изображение в HU, pyqtgraph сам применит уровни
        self.img_item.setImage(display_image_hu.T) # Транспонируем для ImageItem

        # Устанавливаем уровни отображения (min/max для окна)
        min_level = window_center - window_width / 2.0
        max_level = window_center + window_width / 2.0
        self.img_item.setLevels([min_level, max_level])

        # Сбрасываем вид только при первой загрузке среза серии
        if not hasattr(self, '_view_reset_done') or not self._view_reset_done:
             self.view_box.autoRange() # Автомасштабирование
             self._view_reset_done = True


        # --- Обновление маски ---
        # Сбрасываем текущую маску при смене среза
        self.segmentation_mask = None
        self.mask_item.clear()
        self.mask_item.setVisible(False)
        self.segment_checkbox.setEnabled(False)
        self.segment_checkbox.setChecked(False)

        # Обновляем индикатор текущего среза
        total_slices = self.current_volume_hu.shape[0]
        self.slice_label.setText(f"{slice_index + 1}/{total_slices}")

        # Отправляем сигнал об изменении среза
        self.slice_changed.emit(slice_index)
        # logger.debug(f"Отображен срез {slice_index + 1}/{total_slices}")


    @pyqtSlot(int)
    def _on_slice_changed(self, value):
        """ Обработчик изменения позиции слайдера. """
        if value == self.current_slice_index: # Избегаем лишнего обновления, если значение не изменилось
             return
        self._update_slice_display(value)

    def _on_prev_slice(self):
        """ Обработчик кнопки предыдущего среза. """
        new_index = max(0, self.current_slice_index - 1)
        if new_index != self.current_slice_index:
            self.slice_slider.setValue(new_index)

    def _on_next_slice(self):
        """ Обработчик кнопки следующего среза. """
        if self.current_volume_hu is None: return
        total_slices = self.current_volume_hu.shape[0]
        new_index = min(total_slices - 1, self.current_slice_index + 1)
        if new_index != self.current_slice_index:
            self.slice_slider.setValue(new_index)

    @pyqtSlot()
    def run_segmentation(self):
        """ Запускает сегментацию для текущего отображаемого среза. """
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
             logger.error("Попытка запуска сегментации, но модуль недоступен.")
             QMessageBox.critical(self, "Ошибка", "Модуль сегментации недоступен.")
             return

        if self.segmenter.model is None:
            logger.warning("Модель сегментации не загружена для запуска предсказания.")
            QMessageBox.warning(self, "Модель не загружена", "Пожалуйста, сначала загрузите модель сегментации (Файл -> Загрузить модель).")
            return
        if self.current_pixel_data_hu is None:
            logger.warning("Нет данных среза для сегментации.")
            return

        logger.info(f"Запуск сегментации для среза {self.current_slice_index + 1}...")
        QApplication.setOverrideCursor(Qt.WaitCursor) # Показываем курсор ожидания

        try:
            # Выполняем предсказание
            self.segmentation_mask = self.segmenter.predict(self.current_pixel_data_hu)

            if self.segmentation_mask is not None:
                logger.info("Сегментация завершена успешно.")
                # Обновляем отображение маски
                self._update_mask_overlay()
                # Активируем чекбокс и устанавливаем его
                self.segment_checkbox.setEnabled(True)
                self.segment_checkbox.setChecked(True)
            else:
                logger.error("Сегментация не удалась.")
                QMessageBox.critical(self, "Ошибка сегментации", "Не удалось выполнить сегментацию среза.")
                self.segmentation_mask = None
                self.mask_item.clear()
                self.mask_item.setVisible(False)
                self.segment_checkbox.setEnabled(False)
                self.segment_checkbox.setChecked(False)

        finally:
            QApplication.restoreOverrideCursor() # Возвращаем обычный курсор

    def _update_mask_overlay(self):
        """ Обновляет отображение маски сегментации. """
        if self.segmentation_mask is not None and self.segment_checkbox.isChecked():
            # Устанавливаем маску. Транспонируем для ImageItem.
            self.mask_item.setImage(self.segmentation_mask.T, autoLevels=False, levels=(0, 1)) # Уровни для бинарной маски

            # Убеждаемся, что маска видима
            self.mask_item.setVisible(True)

            # Устанавливаем позицию и трансформацию маски такими же, как у основного изображения
            # Это важно для правильного наложения при панорамировании/масштабировании
            img_bounds = self.img_item.boundingRect()
            if img_bounds:
                self.mask_item.setPos(img_bounds.topLeft())
                self.mask_item.setTransform(self.img_item.transform())

            # logger.debug("Маска сегментации отображена.")
        else:
            self.mask_item.clear()
            self.mask_item.setVisible(False)
            # logger.debug("Маска сегментации скрыта.")

    @pyqtSlot(bool)
    def _on_segment_toggle(self, checked):
        """ Обработчик переключения чекбокса отображения сегментации. """
        self._update_mask_overlay()
