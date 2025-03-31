#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Панель просмотра DICOM изображений для приложения PyLungViewer.
"""

import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QSlider, QPushButton, QFrame, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QEvent, QObject, QDateTime
from PyQt5.QtGui import QIcon
import pyqtgraph as pg

# Для визуализации изображений
import numpy as np
import pydicom
from pyqtgraph import ImageView

logger = logging.getLogger(__name__)


class WheelEventFilter(QObject):
    """Фильтр событий колеса мыши для перенаправления их в ViewerPanel."""
    
    def __init__(self, viewer_panel):
        super().__init__()
        self.viewer_panel = viewer_panel
        self.last_scroll_time = 0
    
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            # Получаем текущее время
            current_time = QDateTime.currentMSecsSinceEpoch()
            
            # Базовый интервал прокрутки в мс
            scroll_interval = 100  # мс
            
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
                        step = max(1, total_slices // 50)  # 1 слайс для маленьких серий, больше для больших
                
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
                
            # Перехватываем все события прокрутки
            return True
            
        return False

class ViewerPanel(QWidget):
    """Панель просмотра DICOM изображений."""
    
    # Сигналы для коммуникации с другими компонентами
    slice_changed = pyqtSignal(int)
    
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
        
        # Для отслеживания свайпов на тачпаде
        self.touch_start_x = None
        
        # Инициализация UI
        self._init_ui()
        
        # Создаем фильтр событий
        self.wheel_filter = WheelEventFilter(self)
        
        # Устанавливаем фильтр событий для приложения
        QApplication.instance().installEventFilter(self.wheel_filter)
        
        logger.info("Панель просмотра инициализирована")
    
    def _init_ui(self):
        """Инициализация пользовательского интерфейса."""
        # Главный layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Верхняя панель информации
        info_panel = QWidget()
        info_layout = QHBoxLayout(info_panel)
        info_layout.setContentsMargins(5, 5, 5, 5)
        
        self.info_label = QLabel("Нет загруженных данных")
        info_layout.addWidget(self.info_label)
        
        # Добавляем информационную панель
        main_layout.addWidget(info_panel)
        
        # Виджет для отображения изображений
        self.image_view = ImageView(self)
        self.image_view.ui.histogram.hide()  # Скрываем гистограмму
        self.image_view.ui.roiBtn.hide()     # Скрываем кнопку ROI
        self.image_view.ui.menuBtn.hide()    # Скрываем кнопку меню
        
        # Настройка отображения
        self.image_view.view.setAspectLocked(True)  # Сохраняем пропорции
        self.image_view.view.invertY(False)         # Отключаем инвертирование по Y
        
        # Отключаем масштабирование мышью
        self.image_view.view.setMouseEnabled(x=False, y=False)
        
        # Добавляем основное изображение на панель
        main_layout.addWidget(self.image_view, 1)
        
        # Нижняя панель с слайдером
        bottom_panel = QWidget()
        bottom_layout = QHBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(5, 5, 5, 5)
        
        # Кнопки навигации по срезам
        self.prev_slice_btn = QPushButton("<<")
        self.prev_slice_btn.setFixedWidth(40)
        self.prev_slice_btn.clicked.connect(self._on_prev_slice)
        
        self.next_slice_btn = QPushButton(">>")
        self.next_slice_btn.setFixedWidth(40)
        self.next_slice_btn.clicked.connect(self._on_next_slice)
        
        # Слайдер для выбора среза
        self.slice_slider = QSlider(Qt.Horizontal)
        self.slice_slider.setMinimum(0)
        self.slice_slider.setMaximum(0)  # Будет обновлено при загрузке данных
        self.slice_slider.valueChanged.connect(self._on_slice_changed)
        
        # Индикатор текущего среза
        self.slice_label = QLabel("0/0")
        self.slice_label.setMinimumWidth(60)
        self.slice_label.setAlignment(Qt.AlignCenter)
        
        # Добавляем элементы управления
        bottom_layout.addWidget(self.prev_slice_btn)
        bottom_layout.addWidget(self.slice_slider)
        bottom_layout.addWidget(self.slice_label)
        bottom_layout.addWidget(self.next_slice_btn)
        
        main_layout.addWidget(bottom_panel)
        
        # Добавляем placeholder при отсутствии данных
        self._show_placeholder()
    
    def _show_placeholder(self):
        """Отображение заглушки при отсутствии данных."""
        # Создаем пустое изображение
        placeholder_image = np.zeros((512, 512), dtype=np.uint8)
        self.image_view.setImage(placeholder_image)
        
        # Отключаем контролы
        self.slice_slider.setEnabled(False)
        self.prev_slice_btn.setEnabled(False)
        self.next_slice_btn.setEnabled(False)
    
    def handle_wheel_event(self, event):
        """
        Обработчик события прокрутки колеса мыши.
        
        Args:
            event: Событие колеса мыши.
        """
        if self.current_series is None or not self.current_series.get('files', []):
            return
        
        # Определяем направление прокрутки
        delta = event.angleDelta().y()
        
        # Рассчитываем новый индекс среза
        if delta > 0:
            # Прокрутка вверх - предыдущий срез
            new_index = max(0, self.current_slice_index - 1)
        else:
            # Прокрутка вниз - следующий срез
            files = self.current_series.get('files', [])
            new_index = min(len(files) - 1, self.current_slice_index + 1)
        
        # Устанавливаем новый срез только если изменился индекс
        if new_index != self.current_slice_index:
            self.slice_slider.setValue(new_index)
        
        # Логируем событие (для отладки)
        logger.debug(f"Wheel event: delta={delta}, current={self.current_slice_index}, new={new_index}")
        
        # Предотвращаем дальнейшую обработку события
        event.accept()
    
    def event(self, event):
        """
        Обработчик всех событий, включая события касания.
        """
        if event.type() == QEvent.TouchBegin:
            # Запоминаем начальную позицию касания
            touch_point = event.touchPoints()[0]
            self.touch_start_x = touch_point.pos().x()
            return True
        
        elif event.type() == QEvent.TouchEnd:
            # Обрабатываем завершение касания
            if self.touch_start_x is not None:
                touch_point = event.touchPoints()[0]
                end_x = touch_point.pos().x()
                
                # Определяем направление свайпа
                if end_x - self.touch_start_x > 50:  # Свайп вправо
                    self._on_prev_slice()
                elif self.touch_start_x - end_x > 50:  # Свайп влево
                    self._on_next_slice()
                
                self.touch_start_x = None
            return True
        
        # Для всех остальных событий используем стандартную обработку
        return super().event(event)
        
    def load_series(self, series_data):
        """
        Загрузка серии DICOM изображений для отображения.
        
        Args:
            series_data: Данные серии DICOM снимков.
        """
        # Сохраняем ссылку на текущую серию
        self.current_series = series_data
        
        if series_data is None or not series_data.get('files', []):
            logger.warning("Попытка загрузить пустую серию")
            self._show_placeholder()
            return
        
        # Получаем список файлов серии
        files = series_data.get('files', [])
        
        # Настраиваем слайдер для выбора срезов
        slice_count = len(files)
        self.slice_slider.setMinimum(0)
        self.slice_slider.setMaximum(slice_count - 1)
        self.slice_slider.setValue(0)
        
        # Активируем контролы
        self.slice_slider.setEnabled(True)
        self.prev_slice_btn.setEnabled(True)
        self.next_slice_btn.setEnabled(True)
        
        # Отображаем первый срез
        self._update_slice_display(0)
        
        # Обновляем информацию
        series_desc = series_data.get('description', 'Неизвестно')
        modality = series_data.get('modality', '')
        self.info_label.setText(f"Серия: {series_desc} ({modality})")
        logger.info(f"Загружена серия из {slice_count} изображений")
    
    def _update_slice_display(self, slice_index):
        """
        Обновление отображения для указанного среза.
        
        Args:
            slice_index: Индекс среза для отображения.
        """
        if self.current_series is None or not self.current_series.get('files', []):
            logger.warning("Нет данных для отображения")
            return
        
        files = self.current_series.get('files', [])
        
        if slice_index < 0 or slice_index >= len(files):
            logger.warning(f"Индекс среза вне диапазона: {slice_index}, макс: {len(files)-1}")
            return
        
        # Получаем данные текущего среза
        try:
            # Получаем файл для текущего среза
            file_data = files[slice_index]
            file_path = file_data.get('file_path')
            
            # Загружаем DICOM файл полностью
            ds = pydicom.dcmread(file_path)
            
            # Преобразуем в массив numpy для отображения
            pixel_data = ds.pixel_array
            
            # Применяем трансформацию Hounsfield Units для КТ
            if hasattr(ds, 'RescaleSlope') and hasattr(ds, 'RescaleIntercept'):
                pixel_data = pixel_data * ds.RescaleSlope + ds.RescaleIntercept
            
            # Поворот против часовой стрелки (на 90 градусов)
            pixel_data = np.rot90(pixel_data, k=3)
            
            # Настраиваем окно отображения для КТ легких
            window_center = -600  # Центр окна для легочного режима
            window_width = 1500   # Ширина окна для легочного режима
            
            # Применяем оконные параметры
            min_value = window_center - window_width // 2
            max_value = window_center + window_width // 2
            
            # Отображаем изображение с явно указанными уровнями
            self.image_view.setImage(pixel_data, autoLevels=False)
            self.image_view.setLevels(min_value, max_value)
            
            # Обновляем индикатор текущего среза
            self.current_slice_index = slice_index
            self.slice_label.setText(f"{slice_index + 1}/{len(files)}")
            
            # Отправляем сигнал об изменении среза
            self.slice_changed.emit(slice_index)
            
            logger.info(f"Отображен срез {slice_index + 1} из {len(files)}")
            
        except Exception as e:
            logger.error(f"Ошибка при загрузке среза {slice_index}: {str(e)}", exc_info=True)
            # При ошибке отображаем заглушку
            placeholder_image = np.zeros((512, 512), dtype=np.uint8)
            self.image_view.setImage(placeholder_image)
    
    @pyqtSlot(int)
    def _on_slice_changed(self, value):
        """
        Обработчик изменения позиции слайдера.
        
        Args:
            value: Новое значение слайдера.
        """
        self._update_slice_display(value)
    
    def _on_prev_slice(self):
        """Обработчик кнопки предыдущего среза."""
        new_index = max(0, self.current_slice_index - 1)
        self.slice_slider.setValue(new_index)
    
    def _on_next_slice(self):
        """Обработчик кнопки следующего среза."""
        if self.current_series is None:
            return
            
        files = self.current_series.get('files', [])
        new_index = min(len(files) - 1, self.current_slice_index + 1)
        self.slice_slider.setValue(new_index)