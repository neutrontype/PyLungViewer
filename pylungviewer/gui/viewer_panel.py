import logging
import os
import traceback
import glob 
import math 
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QPushButton, QFrame, QApplication,
    QCheckBox, QMessageBox, QProgressDialog,
    QGraphicsProxyWidget, 
    QGraphicsItem,
    QGraphicsLineItem,
    QMenu, QAction
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QEvent, QObject, QDateTime, QPointF, QThread, QTimer, QRectF, QPoint 
from PyQt5.QtGui import QIcon, QColor, QPen, QKeyEvent, QContextMenuEvent 
import pyqtgraph as pg


import numpy as np
import pydicom
from pyqtgraph import ImageView, ImageItem, AxisItem

logger = logging.getLogger(__name__)

try:
    from pylungviewer.core.segmentation import LungSegmenter
    SEGMENTATION_AVAILABLE = True
    logger.info("Модуль сегментации успешно импортирован.")
except ImportError as e:
    LungSegmenter = None
    SEGMENTATION_AVAILABLE = False
    logger.error("!!! Ошибка при импорте модуля сегментации !!!")
    print("--------------------------------------------------")
    print("!!! Ошибка импорта модуля сегментации !!!")
    f"Ошибка: {e}"
    traceback.print_exc()
    print("--------------------------------------------------")
    logger.warning("Модуль сегментации не найден или его зависимости отсутствуют.")

from pylungviewer.utils.window_presets import WindowPresets
from pylungviewer.core.dicom_loader import DicomLoader

class SegmentationWorker(QObject):
    finished = pyqtSignal(object) 
    progress = pyqtSignal(int, int) 
    error = pyqtSignal(str) 

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
    """Панель просмотра DICOM изображений с поддержкой сегментации, отображением HU и измерением."""

    slice_changed = pyqtSignal(int)
    segmentation_progress = pyqtSignal(int, int)
    segmentation_status_update = pyqtSignal(str)
    model_loaded_status = pyqtSignal(bool)
    measurement_state_changed = pyqtSignal(bool, bool, bool)

    def __init__(self, models_dir: str, dicom_loader: DicomLoader, parent=None):
        super().__init__(parent)
        self.current_series = None
        self.current_slice_index = 0
        self.current_pixel_data_hu = None
        self.current_volume_hu = None
        self.segmentation_mask = None 
        self.full_segmentation_mask_volume = None
        self.models_dir = models_dir 
        self.dicom_loader = dicom_loader

        #  Переменные для инструмента измерения 
        self._measurement_mode_active = False 
        self._measurement_start_point = None
        self._current_measurement_item = None 
        self._measurements_by_slice = {} 
        self._selected_measurement_item = None 
        self.pixel_spacing = (1.0, 1.0)

        if SEGMENTATION_AVAILABLE:
            self.segmenter = LungSegmenter()
            self._auto_load_model()
        else:
            self.segmenter = None
            self.model_loaded_status.emit(False)

        self.segmentation_thread = None
        self.segmentation_worker = None
        self.progress_dialog = None

        self.touch_start_pos = None
        self._init_ui()

        self.graphics_widget.setMouseTracking(True)
        self.graphics_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.view_box.scene().sigMouseClicked.connect(self._on_view_box_clicked)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        logger.info("Панель просмотра инициализирована (с поддержкой сегментации, HU и измерением)")

    def contextMenuEvent(self, event: QContextMenuEvent):
        """
        Обработчик события контекстного меню (правый клик).
        Показывает меню с опцией удаления для выбранного измерения.
        """
        logger.debug(f"Context menu event at position: {event.pos()}")
        # Проверяем, есть ли выбранное измерение
        if self._selected_measurement_item:
            context_menu = QMenu(self)
            delete_action = QAction("Удалить измерение", self)
            # Подключаем действие к методу удаления
            delete_action.triggered.connect(self._delete_selected_measurement)
            context_menu.addAction(delete_action)
            # Показываем меню в глобальной позиции клика
            context_menu.exec_(self.mapToGlobal(event.pos()))
        else:

            logger.debug("Контекстное меню не показано: нет выбранного измерения.")
            pass # Ничего не делаем, если нет выбранного измерения

    @pyqtSlot(QPoint) 
    def _show_context_menu(self, pos: QPoint):
        """
        Слот для отображения контекстного меню по сигналу customContextMenuRequested.
        """
        logger.debug(f"Received customContextMenuRequested at pos: {pos}")
        # Преобразуем координаты виджета в глобальные координаты экрана
        global_pos = self.mapToGlobal(pos) 

        click_pos_scene = self.graphics_widget.mapToScene(pos) 
        # Проверяем, есть ли элементы сцены под курсоом
        items_at_pos = self.view_box.scene().items(click_pos_scene)
        logger.debug(f"Items at right-click position: {items_at_pos}")

        selected_measurement = None
        # Проходим по всем элементам под курсором
        for item in items_at_pos:
             current_slice_measurements = self._measurements_by_slice.get(self.current_slice_index, [])
             for measurement in current_slice_measurements:
                  if item == measurement['line']:
                       selected_measurement = measurement
                       break # Нашли совпадение, выбираем его
             if selected_measurement:
                  break # Прекращаем поиск по элементам, если измерение найдено

        # Снимаем выделение с предыдущего, если оно было
        self._deselect_measurement()

        context_menu = QMenu(self)

        if selected_measurement:
            # Если клик правой кнопкой мыши попал по измерению
            logger.debug("Правый клик по измерению. Выделяем и показываем меню удаления.")
            self._selected_measurement_item = selected_measurement
            self._selected_measurement_item['line'].setPen(pg.mkPen('cyan', width=3)) # Визуально выделяем
            delete_action = QAction("Удалить измерение", self)
            delete_action.triggered.connect(self._delete_selected_measurement)
            context_menu.addAction(delete_action)
        else:
            # Если клик правой кнопкой мыши не попал по измерению (пустое место)
            logger.debug("Правый клик по пустому месту. Контекстное меню измерений не показывается.")
            pass

        if context_menu.actions(): 
             context_menu.exec_(global_pos)

    @pyqtSlot()
    def run_single_slice_segmentation(self):
        """ Запускает сегментацию только для текущего среза. """
        if not self._check_segmentation_prerequisites(): return

        logger.info(f"Запуск сегментации для среза {self.current_slice_index + 1}...")
        self.segmentation_status_update.emit(f"Сегментация среза {self.current_slice_index + 1}...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.full_segmentation_mask_volume = None
            single_mask = self.segmenter.predict(self.current_pixel_data_hu)

            if single_mask is not None:
                logger.info("Сегментация среза завершена успешно.")
                self.segmentation_mask = single_mask
                self.segment_checkbox.setChecked(True) 
                self._update_mask_overlay() 
                self.segmentation_status_update.emit(f"Сегментация среза {self.current_slice_index + 1} завершена.")
            else:
                logger.error("Сегментация среза не удалась.")
                QMessageBox.critical(self, "Ошибка сегментации", "Не удалось выполнить сегментацию среза.")
                # Сбрасываем маску и деактивируем чекбокс
                self.segmentation_mask = None
                self.segment_checkbox.setChecked(False)
                self._update_mask_overlay() # Скрываем оверлей
                self.segmentation_status_update.emit("Ошибка сегментации среза.")
        except Exception as e:
             logger.error(f"Исключение при сегментации среза: {e}", exc_info=True)
             QMessageBox.critical(self, "Ошибка сегментации", f"Произошла ошибка при сегментации среза:\n{str(e)}")
             self.segmentation_mask = None
             self.segment_checkbox.setChecked(False)
             self._update_mask_overlay()
             self.segmentation_status_update.emit("Ошибка сегментации среза.")
        finally:
            QApplication.restoreOverrideCursor()
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
        self._set_segmentation_controls_enabled(False) 

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
        self.segmentation_thread.finished.connect(self._clear_segmentation_thread_refs)
        self.segmentation_thread.start()


    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        info_panel = QWidget()
        info_layout = QHBoxLayout(info_panel)
        info_layout.setContentsMargins(5, 5, 5, 5)
        self.info_label = QLabel("Нет загруженных данных")
        info_layout.addWidget(self.info_label)

        self.hu_label = QLabel("HU: N/A")
        self.hu_label.setMinimumWidth(100) 
        self.hu_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        info_layout.addWidget(self.hu_label)

        main_layout.addWidget(info_panel)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        main_layout.addWidget(self.graphics_widget, 1)

        self.left_axis = pg.AxisItem(orientation='left')
        self.left_axis.setLabel('Положение', units='мм')
        self.graphics_widget.addItem(self.left_axis, row=0, col=0)

        self.bottom_axis = pg.AxisItem(orientation='bottom')
        self.bottom_axis.setLabel('')
        self.graphics_widget.addItem(self.bottom_axis, row=1, col=1)

        self.view_box = self.graphics_widget.addViewBox(row=0, col=1) 
        self.view_box.setAspectLocked(True) 
        self.view_box.invertY(True)
        self.view_box.setMouseEnabled(x=True, y=True)
        self.view_box.wheelEvent = self._handle_view_box_wheel_event

        self.left_axis.linkToView(self.view_box)

        self.bottom_axis.linkToView(self.view_box)

        self.graphics_widget.ci.layout.setColumnStretchFactor(1, 10)

        self.graphics_widget.ci.layout.setRowStretchFactor(0, 10)

        self.img_item = pg.ImageItem()
        self.view_box.addItem(self.img_item) 

        self.mask_item = pg.ImageItem()
        self.mask_item.setCompositionMode(pg.QtGui.QPainter.CompositionMode_Plus)
        lut = np.array([[0, 0, 0, 0], [255, 0, 0, 255]], dtype=np.uint8) 
        self.mask_item.setLookupTable(lut)
        self.mask_item.setVisible(False)
        self.view_box.addItem(self.mask_item) 

        # Добавляем метки сторон (A, P, R, L) 
        label_style = "font-size: 16pt; font-weight: medium; color: white; background-color: transparent;" # Стиль меток
        self.label_a = QLabel("A")
        self.label_a.setStyleSheet(label_style)
        self.label_p = QLabel("P")
        self.label_p.setStyleSheet(label_style)
        self.label_l = QLabel("L")
        self.label_l.setStyleSheet(label_style)
        self.label_r = QLabel("R")
        self.label_r.setStyleSheet(label_style)

        self.proxy_a = QGraphicsProxyWidget()
        self.proxy_a.setWidget(self.label_a)
        self.proxy_p = QGraphicsProxyWidget()
        self.proxy_p.setWidget(self.label_p)
        self.proxy_l = QGraphicsProxyWidget()
        self.proxy_l.setWidget(self.label_l)
        self.proxy_r = QGraphicsProxyWidget()
        self.proxy_r.setWidget(self.label_r)

        # Добавляем proxy виджеты в ViewBox
        self.view_box.addItem(self.proxy_a)
        self.view_box.addItem(self.proxy_p)
        self.view_box.addItem(self.proxy_l)
        self.proxy_r.setFlag(QGraphicsItem.ItemIgnoresTransformations) 
        self.view_box.addItem(self.proxy_r)

        self.proxy_a.setZValue(100)
        self.proxy_p.setZValue(100)
        self.proxy_l.setZValue(100)
        self.proxy_r.setZValue(100)

        self.view_box.sigRangeChanged.connect(self._update_side_label_positions)

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

        controls_panel = QHBoxLayout()


        controls_panel.addStretch(1) 

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

        self._update_segmentation_button_tooltips()
        self._update_segmentation_controls_state()

        segment_panel.addWidget(self.segment_checkbox)
        segment_panel.addStretch(1)
        segment_panel.addWidget(self.run_segment_btn)
        segment_panel.addWidget(self.run_full_segment_btn)

        bottom_layout.addLayout(controls_panel)
        bottom_layout.addLayout(segment_panel)

        main_layout.addWidget(bottom_panel)
        self._show_placeholder()

        QTimer.singleShot(0, self._update_side_label_positions)

    def resizeEvent(self, event):
        """
        Обработчик события изменения размера виджета.
        Вызывается при изменении размера ViewerPanel.
        """
        super().resizeEvent(event) 
        self._update_side_label_positions()

    def keyPressEvent(self, event: QKeyEvent):
        """
        Обработчик нажатия клавиш.
        Используется для удаления выбранного измерения по клавише Delete.
        """
        logger.debug(f"Key press event: {event.key()}, text: '{event.text()}', isAccepted: {event.isAccepted()}")
        if event.key() == Qt.Key_Delete:
            logger.debug("Нажата клавиша Delete.")
            self._delete_selected_measurement()
            event.accept() 
        else:
            super().keyPressEvent(event)

    def _update_side_label_positions(self):
        """Обновляет позиции меток сторон (A, P, R, L) в зависимости от текущего диапазона ViewBox."""
        if self.view_box is None or self.img_item is None:
            return

        view_range = self.view_box.viewRange()
        x_min_data, x_max_data = view_range[0]
        y_min_data, y_max_data = view_range[1]

        # Получаем размеры изображения в координатах данных
        img_bounds_data = self.img_item.boundingRect()
        img_x_data = img_bounds_data.x()
        img_y_data = img_bounds_data.y()
        img_width_data = img_bounds_data.width()
        img_height_data = img_bounds_data.height()

        # Получаем текущие размеры ViewBox в координатах сцены (пикселях экрана)
        view_rect_scene = self.view_box.mapRectToScene(self.view_box.viewRect())
        view_width_scene = view_rect_scene.width()
        view_height_scene = view_rect_scene.height()

        # Получаем размеры меток в пикселях экрана
        label_a_size = self.proxy_a.size()
        label_p_size = self.proxy_p.size()
        label_l_size = self.proxy_l.size()
        label_r_size = self.proxy_r.size()

        # Рассчитываем небольшой отступ от края в пикселях экрана
        offset_pixels = 10 


        center_x_scene = view_rect_scene.x() + view_width_scene / 2

        top_y_scene = view_rect_scene.y() + offset_pixels

        pos_a_scene = QPointF(center_x_scene - label_a_size.width() / 2, top_y_scene)

        pos_a_data = self.view_box.mapFromScene(pos_a_scene)
        self.proxy_a.setPos(pos_a_data)

        center_x_scene = view_rect_scene.x() + view_width_scene / 2

        bottom_y_scene = view_rect_scene.y() + view_height_scene - offset_pixels - label_p_size.height()

        pos_p_scene = QPointF(center_x_scene - label_p_size.width() / 2, bottom_y_scene)

        pos_p_data = self.view_box.mapFromScene(pos_p_scene)
        self.proxy_p.setPos(pos_p_data)


        left_x_scene = view_rect_scene.x() + offset_pixels

        center_y_scene = view_rect_scene.y() + view_height_scene / 2
 
        pos_l_scene = QPointF(left_x_scene, center_y_scene - label_l_size.height() / 2)

        pos_l_data = self.view_box.mapFromScene(pos_l_scene)
        self.proxy_l.setPos(pos_l_data)


        right_x_scene = view_rect_scene.x() + view_width_scene - offset_pixels - label_r_size.width()

        center_y_scene = view_rect_scene.y() + view_height_scene / 2

        pos_r_scene = QPointF(right_x_scene, center_y_scene - label_r_size.height() / 2)

        pos_r_data = self.view_box.mapFromScene(pos_r_scene)
        self.proxy_r.setPos(pos_r_data)


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
             self.run_full_segment_btn.setToolTip("Сегментировать все срезы серии (может занять время, требуется загруженная модель)")


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

        placeholder_image = np.zeros((512, 512), dtype=np.uint8)
        self.img_item.setImage(placeholder_image)
        self.mask_item.clear()
        self.mask_item.setVisible(False)
        self.view_box.autoRange()
        self.slice_slider.setEnabled(False)
        self.prev_slice_btn.setEnabled(False)
        self.next_slice_btn.setEnabled(False)

        self.info_label.setText("Нет загруженных данных")

        self.hu_label.setText("HU: N/A")

        self.current_volume_hu = None
        self.current_pixel_data_hu = None
        self.segmentation_mask = None
        self.full_segmentation_mask_volume = None
        self.pixel_spacing = (1.0, 1.0) 
        self._clear_measurements_on_slice(self.current_slice_index) # Очищаем измерения при сбросе
        # Обновляем состояние кнопок после сброса данных
        self._update_segmentation_controls_state()
        # Обновляем позиции меток сторон после сброса вида
        self._update_side_label_positions()
        # Отключаем режим измерения и обновляем состояние действия в MainWindow
        self.toggle_measurement_mode(False)

    def _handle_view_box_wheel_event(self, event):
        """
        Обработчик события колеса мыши для ViewBox.
        Используется для прокрутки срезов.
        """
        if self.current_series is None or not self.current_series.get('files', []):

            return 

        delta = event.delta()


        step = 1
        total_slices = len(self.current_series.get('files', []))
        # Увеличиваем шаг прокрутки для больших серий
        if total_slices > 100: step = max(1, total_slices // 50)
        current_index = self.current_slice_index
        if delta > 0: new_index = max(0, current_index - step)
        else: new_index = min(total_slices - 1, current_index + step)

        if new_index != current_index:
            self.slice_slider.setValue(new_index)


        event.accept()


    def load_series(self, series_data):
        """Загрузка новой серии, с остановкой предыдущей сегментации."""
        logger.info("Загрузка новой серии...")
        # Отменяем любую текущую сегментацию перед загрузкой новой серии
        self.cancel_segmentation()

        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
             logger.warning("Предыдущая сегментация еще выполняется. Попытка отмены...")
             # Устанавливаем флаг отмены в воркере
             if self.segmentation_worker:
                  self.segmentation_worker.cancel()
             # Завершаем поток
             self.segmentation_thread.quit()
             # Ждем завершения потока, но с таймаутом
             if not self.segmentation_thread.wait(2000): 
                  logger.warning("Поток сегментации не завершился вовремя при смене серии.")

             self._clear_segmentation_thread_refs()

        elif self.segmentation_thread is not None:
             self._clear_segmentation_thread_refs()


        self._view_reset_done = False
        self._show_placeholder() # Сбрасываем UI и данные
        self.current_series = series_data
        # Очищаем все сохраненные измерения при загрузке новой серии
        self._measurements_by_slice.clear()
        logger.debug("Все сохраненные измерения очищены при загрузке новой серии.")


        if series_data is None or not series_data.get('files', []):
            logger.warning("Попытка загрузить пустую серию")
            # Обновляем состояние кнопок после загрузки пустой серии
            self._update_segmentation_controls_state()
            self._update_measurement_controls_state() # Обновляем состояние измерения
            self.measurement_state_changed.emit(False, self._measurement_mode_active, False) # Оповещаем MainWindow
            return
        files = series_data.get('files', [])
        slice_count = len(files)
        logger.info(f"Загрузка {slice_count} срезов в память...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        volume_hu_list = []
        first_ds = None
        try:
            # Используем переданный экземпляр DicomLoader
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
                     continue 

                volume_hu_list.append(pixel_data)

            if not volume_hu_list:
                 logger.error("Не удалось загрузить пиксельные данные ни для одного среза в серии.")
                 raise RuntimeError("Не удалось загрузить данные серии.")

            self.current_volume_hu = np.stack(volume_hu_list, axis=0)
            logger.info(f"Объем загружен. Форма: {self.current_volume_hu.shape}")

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

             row_spacing = 1.0 
             col_spacing = 1.0 
             if files and first_ds:
                  try:
                       pixel_spacing = getattr(first_ds, 'PixelSpacing', None)
                       if pixel_spacing and len(pixel_spacing) == 2:
                            row_spacing = float(pixel_spacing[0])
                            col_spacing = float(pixel_spacing[1])
                            logger.info(f"Pixel Spacing found: Row={row_spacing}mm, Col={col_spacing}mm")

                            if hasattr(self.img_item, 'setPixelSize'):
                                self.img_item.setPixelSize(x=col_spacing, y=row_spacing)
                            else:
                                logger.warning("Объект ImageItem не имеет метода setPixelSize. Масштабирование может быть некорректным.")

                            self.left_axis.setLabel('Положение', units='мм')

                            self.pixel_spacing = (row_spacing, col_spacing)

                            logger.info(f"Считанный Pixel Spacing: {self.pixel_spacing} (row, col)")


                  except Exception as e:
                       logger.warning(f"Не удалось получить Pixel Spacing или установить pixelization: {e}")
                       self.pixel_spacing = (1.0, 1.0) # Сбрасываем на дефолт
             else:
                  logger.warning("Pixel Spacing не доступен (нет файлов или ds). Использование дефолтного 1.0мм.")

                  if hasattr(self.img_item, 'setPixelSize'):
                      self.img_item.setPixelSize(x=1.0, y=1.0)
                  else:
                      logger.warning("Объект ImageItem не имеет метода setPixelSize. Масштабирование может быть некорректным.")

                  self.pixel_spacing = (1.0, 1.0) # Сбрасываем на дефолт


             self._update_slice_display(self.slice_slider.value())

             patient_name_obj = getattr(first_ds, 'PatientName', 'N/A') if first_ds else 'N/A'
             patient_name = str(patient_name_obj)
             study_desc = getattr(first_ds, 'StudyDescription', '') if first_ds else ''
             series_desc = series_data.get('description', 'Неизвестно')
             modality = series_data.get('modality', '')
             self.info_label.setText(f"Пациент: {patient_name} | Исслед.: {study_desc} | Серия: {series_desc} ({modality})")
             logger.info(f"Загружена серия '{series_desc}' из {slice_count} изображений")
        else:
    
             self._show_placeholder()
             logger.warning("Серия загружена, но не содержит изображений.")



        self._update_segmentation_controls_state()
        # Обновляем состояние кнопки измерения после загрузки данных серии
        self._update_measurement_controls_state()
        # Оповещаем MainWindow об изменении состояния данных
        self.measurement_state_changed.emit(self.current_volume_hu is not None, self._measurement_mode_active, len(self._measurements_by_slice.get(self.current_slice_index, [])) > 0)

        # Устанавливаем фокус на ViewerPanel после загрузки данных
        self.setFocus()


    def _update_slice_display(self, slice_index):
        """
        Обновляет отображение текущего среза, маски и измерений.
        """
        if self.current_volume_hu is None: return
        if slice_index < 0 or slice_index >= self.current_volume_hu.shape[0]: return

        # Скрываем измерения предыдущего среза перед обновлением
        self._hide_measurements_on_slice(self.current_slice_index)

        has_full_mask = self.full_segmentation_mask_volume is not None
        is_new_slice = slice_index != self.current_slice_index # Проверяем, изменился ли срез

        # Обновляем основные данные среза
        self.current_slice_index = slice_index
        self.current_pixel_data_hu = self.current_volume_hu[slice_index]

        # Отображаем КТ
        window_center, window_width = WindowPresets.get_preset("Легочное")
        display_image_hu = self.current_pixel_data_hu
        self.img_item.setImage(display_image_hu.T, autoLevels=False, levels=[window_center - window_width / 2.0, window_center + window_width / 2.0]) # Транспонируем для правильной ориентации

        # Автоматическое масштабирование при первой загрузке среза
        if not hasattr(self, '_view_reset_done') or not self._view_reset_done:
             self.view_box.autoRange()
             self._view_reset_done = True
             self._update_side_label_positions()

        # Обновляем маску и чекбокс
        if has_full_mask:
            # Если есть полная маска, берем срез из нее
            if slice_index < self.full_segmentation_mask_volume.shape[0]:
                self.segmentation_mask = self.full_segmentation_mask_volume[slice_index]
            else: 
                self.segmentation_mask = None

        elif is_new_slice:

            self.segmentation_mask = None

        self._update_mask_overlay() # Обновляем отображение маски

        total_slices = self.current_volume_hu.shape[0]
        self.slice_label.setText(f"{slice_index + 1}/{total_slices}")
        self.slice_changed.emit(slice_index)

        # Отображаем измерения для текущего среза 
        self._show_measurements_on_slice(self.current_slice_index)

        # Обновляем состояние действий измерения 
        self._update_measurement_controls_state()



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

    def _check_segmentation_prerequisites(self):
        """ Проверяет, доступны ли условия для выполнения сегментации. """
        if not SEGMENTATION_AVAILABLE or self.segmenter is None:
             logger.error("Попытка запуска сегментации, но модуль недоступен.")
             QMessageBox.critical(self, "Ошибка", "Модуль сегментации недоступен.")
             return False
        if self.segmenter.model is None:
            logger.warning("Модель сегментации не загружена.")
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
        single_mask_is_available = self.segmentation_mask is not None

        self.run_segment_btn.setEnabled(model_is_loaded and data_is_loaded)
        self.run_full_segment_btn.setEnabled(model_is_loaded and data_is_loaded)

        self.segment_checkbox.setEnabled(full_mask_is_available or single_mask_is_available)

        if not self.segment_checkbox.isEnabled():
             self.segment_checkbox.setChecked(False)


    @pyqtSlot(int, int)
    def _on_full_segmentation_progress(self, current, total):
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
            self._update_slice_display(self.current_slice_index)
            self.segment_checkbox.setChecked(True)
        else:
            if worker_cancelled:
                 logger.info("Сегментация была отменена пользователем.")
                 self.segmentation_status_update.emit("Сегментация отменена.")
            else:
                 logger.error("Сегментация всего объема не удалась (результат None).")
                 self.segmentation_status_update.emit("Ошибка сегментации всего объема.")
            self.full_segmentation_mask_volume = None

            self.segment_checkbox.setChecked(False)
            self._update_mask_overlay() # Скрываем оверлей

        self._set_segmentation_controls_enabled(True)
        self._update_segmentation_controls_state()



    @pyqtSlot(str)
    def _on_segmentation_error(self, error_message):

        logger.error(f"Ошибка из потока сегментации: {error_message}")

        QMessageBox.critical(self, "Ошибка сегментации", f"Произошла ошибка во время сегментации:\n{error_message}")
        self.segmentation_status_update.emit("Ошибка сегментации.")

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


    @pyqtSlot()
    def cancel_segmentation(self):
        """ Попытка отмены текущей сегментации объема. """
        logger.info("Попытка отмены сегментации...")
        if self.segmentation_worker is not None:
            self.segmentation_worker.cancel()
        # Затем пытаемся завершить поток
        if self.segmentation_thread is not None and self.segmentation_thread.isRunning():
             logger.info("Завершаем поток сегментации...")
             self.segmentation_thread.quit()

        if self.progress_dialog:
            self.progress_dialog.setLabelText("Отмена сегментации...")


    def _clear_segmentation_thread_refs(self):
        """ Очищает ссылки на поток и воркер сегментации, если они существуют. """
        logger.debug("Очистка ссылок на поток и воркер сегментации.")

        # Отключаем сигналы воркера, если он еще существует
        if self.segmentation_worker:
             try: self.segmentation_worker.progress.disconnect(self._on_full_segmentation_progress)
             except (TypeError, RuntimeError): pass 
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

        self.segmentation_thread = None
        self.segmentation_worker = None
        logger.debug("Ссылки на поток и воркер сегментации очищены.")


    def _update_mask_overlay(self):
        """ Обновляет отображение маски сегментации. """
        if (self.full_segmentation_mask_volume is not None and self.segment_checkbox.isChecked()) or \
           (self.segmentation_mask is not None and self.full_segmentation_mask_volume is None): # Отображаем временную маску, если нет полной
            mask_to_display = None
            if self.full_segmentation_mask_volume is not None and self.segment_checkbox.isChecked():
                 # Берем срез из полной маски
                 if self.current_slice_index < self.full_segmentation_mask_volume.shape[0]:
                      mask_to_display = self.full_segmentation_mask_volume[self.current_slice_index]
                 else:
                      logger.warning(f"Индекс среза {self.current_slice_index} вне диапазона полной маски {self.full_segmentation_mask_volume.shape[0]}. Полная маска не отображена.")
            elif self.segmentation_mask is not None and self.full_segmentation_mask_volume is None:
                 # Используем временную маску среза, если нет полной маски
                 mask_to_display = self.segmentation_mask


            if mask_to_display is not None:
                 self.mask_item.setImage(mask_to_display.T, autoLevels=False, levels=(0, 1)) # Транспонируем
                 self.mask_item.setVisible(True)
                 # Убеждаемся, что маска выравнивается с изображением
                 img_bounds = self.img_item.boundingRect()
                 if img_bounds:
                     self.mask_item.setPos(img_bounds.topLeft())
                     self.mask_item.setTransform(self.img_item.transform())
            else:
                 # Если маска для отображения не определена
                 self.mask_item.clear()
                 self.mask_item.setVisible(False)

        else:
            # Скрываем маску, если нет данных полной маски И нет временной маски ИЛИ (есть полная маска, но чекбокс выключен)
            self.mask_item.clear()
            self.mask_item.setVisible(False)

    def _set_segmentation_controls_enabled(self, enabled):
        """ Временно включает/отключает кнопки сегментации (не чекбокс). """
        # Получаем текущее состояние доступности кнопок на основе наличия модели и данных
        model_is_loaded = SEGMENTATION_AVAILABLE and self.segmenter is not None and self.segmenter.model is not None
        data_is_loaded = self.current_volume_hu is not None
        can_segment = model_is_loaded and data_is_loaded

        self.run_segment_btn.setEnabled(enabled and can_segment)
        self.run_full_segment_btn.setEnabled(enabled and can_segment)

    def _on_mouse_moved(self, pos):
        """
        Обработчик движения мыши для отображения HU и обновления текущего измерения.
        Позиция 'pos' находится в координатах сцены (graphics_widget).
        """

        pos_in_img_item = self.img_item.mapFromScene(pos)


        x = int(pos_in_img_item.x())
        y = int(pos_in_img_item.y())

        # Получаем размеры текущего среза (height, width)
        height, width = self.current_pixel_data_hu.shape if self.current_pixel_data_hu is not None else (0, 0)

        # Обновление HU Label 
        if self.current_pixel_data_hu is not None and 0 <= y < height and 0 <= x < width:
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
            # Если курсор вне изображения или нет данных
            self.hu_label.setText("HU: N/A (вне изображения)")

        if self._measurement_mode_active and self._measurement_start_point is not None and self._current_measurement_item is not None:
            end_point_data = QPointF(x, y)

            self._current_measurement_item['line'].setData([self._measurement_start_point.x(), end_point_data.x()],
                                                          [self._measurement_start_point.y(), end_point_data.y()])

            distance_mm = self._calculate_distance_mm(self._measurement_start_point, end_point_data)
            self._current_measurement_item['text'].setHtml(f"<div style='text-align: center; color: white; background-color: rgba(0,0,0,100); padding: 2px;'>{distance_mm:.1f} mm</div>")


            text_pos_x = (self._measurement_start_point.x() + end_point_data.x()) / 2.0
            text_pos_y = (self._measurement_start_point.y() + end_point_data.y()) / 2.0
            offset_x = 5 
            offset_y = 5 
            self._current_measurement_item['text'].setPos(text_pos_x + offset_x, text_pos_y + offset_y)


    @pyqtSlot(object) 
    def _on_view_box_clicked(self, event):
        """
        Обработчик кликов мыши в ViewBox для режима измерения и выбора измерений.
        """
        logger.debug(f"ViewBox clicked: button={event.button()}, pos={event.pos()}, scenePos={event.scenePos()}")

        # Если клик правой кнопкой мыши, снимаем выделение и игнорируем дальнейшую обработку клика
        if event.button() == Qt.RightButton:
             logger.debug("Правый клик мыши.")
             self.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, event.pos().toPoint(), event.globalPos()))
             event.accept() 
             return

        # Проверяем, был ли клик левой кнопкой мыши
        if event.button() != Qt.LeftButton:
            if self._measurement_mode_active and self._measurement_start_point is not None:
                 logger.debug("Отмена текущего измерения (клик не левой кнопкой).")
                 self._measurement_start_point = None
                 if self._current_measurement_item:
                      # Удаляем временные элементы с ViewBox
                      self.view_box.removeItem(self._current_measurement_item['line'])
                      self.view_box.removeItem(self._current_measurement_item['text'])
                      self._current_measurement_item = None
                 event.accept() # Принимаем событие, чтобы оно не обрабатывалось далее
                 return
            event.ignore() # Игнорируем, чтобы не было неожиданного поведения
            return

        # Получаем позицию клика в координатах сцены
        click_pos_scene = event.scenePos()

        # Логика для режима измерения 
        if self._measurement_mode_active:
            click_pos_data = self.img_item.mapFromScene(click_pos_scene)

            x = int(click_pos_data.x())
            y = int(click_pos_data.y())

            height, width = self.current_pixel_data_hu.shape if self.current_pixel_data_hu is not None else (0, 0)
            if not (0 <= y < height and 0 <= x < width):
                 logger.debug("Клик вне границ изображения в режиме измерения.")

                 if self._measurement_mode_active and self._measurement_start_point is not None:
                      logger.debug("Отмена текущего измерения (клик вне изображения).")
                      self._measurement_start_point = None
                      if self._current_measurement_item:
                           self.view_box.removeItem(self._current_measurement_item['line'])
                           self.view_box.removeItem(self._current_measurement_item['text'])
                           self._current_measurement_item = None
                      # Снимаем выделение с любого выбранного измерения при выходе из режима рисования
                      self._deselect_measurement()
                      event.accept()
                      return

                 event.ignore() 
                 return


            # Если это первый клик, сохраняем начальную точку и создаем временные элементы
            if self._measurement_start_point is None:
                logger.debug(f"Начало измерения в ({x}, {y}) (пиксели изображения)")
                self._measurement_start_point = QPointF(x, y)

                line_item = pg.PlotCurveItem([x, x], [y, y], pen=pg.mkPen('yellow', width=2))
                text_item = pg.TextItem("0.0 mm", color='white', anchor=(0.5, 0.5))
                text_item.setPos(x, y)

                self.view_box.addItem(line_item)
                self.view_box.addItem(text_item)

                self._current_measurement_item = {'line': line_item, 'text': text_item}

            else:
                logger.debug(f"Конец измерения в ({x}, {y}) (пиксели изображения)")
                end_point = QPointF(x, y)

                self._current_measurement_item['line'].setData([self._measurement_start_point.x(), end_point.x()],
                                                              [self._measurement_start_point.y(), end_point.y()])

                # Рассчитываем окончательное расстояние
                distance_mm = self._calculate_distance_mm(self._measurement_start_point, end_point)
                logger.info(f"Измерение завершено. Расстояние: {distance_mm:.2f} mm")

                # Обновляем текст с окончательным значением
                self._current_measurement_item['text'].setHtml(f"<div style='text-align: center; color: white; background-color: rgba(0,0,0,100); padding: 2px;'>{distance_mm:.1f} mm</div>")

                text_pos_x = (self._measurement_start_point.x() + end_point.x()) / 2.0
                text_pos_y = (self._measurement_start_point.y() + end_point.y()) / 2.0
                offset_x = 5 
                offset_y = 5 
                self._current_measurement_item['text'].setPos(text_pos_x + offset_x, text_pos_y + offset_y)


                # Сохраняем завершенное измерение в списке для текущего среза
                if self.current_slice_index not in self._measurements_by_slice:
                     self._measurements_by_slice[self.current_slice_index] = []
                self._measurements_by_slice[self.current_slice_index].append(self._current_measurement_item)
                logger.debug(f"Измерение сохранено для среза {self.current_slice_index}. Всего на срезе: {len(self._measurements_by_slice[self.current_slice_index])}")


                # Сбрасываем переменные для нового измерения
                self._measurement_start_point = None
                self._current_measurement_item = None

                # После завершения рисования, выходим из режима рисования
                self.toggle_measurement_mode(False) 

                self._update_measurement_controls_state()

            event.accept() 

        else:
            # Получаем список элементов сцены под курсором
            items_at_pos = self.view_box.scene().items(click_pos_scene)
            logger.debug(f"Items at click position: {items_at_pos}")

            selected_measurement = None
            # Проходим по всем элементам под курсором
            for item in items_at_pos:

                 current_slice_measurements = self._measurements_by_slice.get(self.current_slice_index, [])
                 for measurement in current_slice_measurements:
                      if item == measurement['line']:
                           selected_measurement = measurement
                           break 
                 if selected_measurement:
                      break 

            self._deselect_measurement()

            if selected_measurement:
                 logger.debug("Выбрано измерение для удаления.")
                 self._selected_measurement_item = selected_measurement
                 # Визуально выделяем линию (например, меняем цвет или толщину)
                 self._selected_measurement_item['line'].setPen(pg.mkPen('cyan', width=3)) 
                 
                 self.setFocus()
                 event.accept() 
            else:
                 logger.debug("Клик не попал по измерению.")

                 event.ignore()
            return 


    def _on_measurement_hover(self, event, measurement_item):
        """ Обработчик наведения мыши на линию измерения. """
        if event.isEnter():
            pass 
        elif event.isExit():
            pass


    def _deselect_measurement(self):
        """ Снимает выделение с текущего выбранного измерения. """
        if self._selected_measurement_item:
            logger.debug("Снятие выделения с измерения.")
            self._selected_measurement_item['line'].setPen(pg.mkPen('yellow', width=2))
            self._selected_measurement_item = None
            self._update_measurement_controls_state()


    def _delete_selected_measurement(self):
        """ Удаляет текущее выбранное измерение. """
        logger.debug("Попытка удаления выбранного измерения.")
        if self._selected_measurement_item:
            logger.info("Удаление выбранного измерения.")
            try:

                self.view_box.removeItem(self._selected_measurement_item['line'])
                self.view_box.removeItem(self._selected_measurement_item['text'])
                logger.debug("Элементы измерения удалены из ViewBox.")


                current_slice_measurements = self._measurements_by_slice.get(self.current_slice_index, [])
                index_to_remove = -1
                for i, measurement in enumerate(current_slice_measurements):
                     # Сравниваем по ссылке на словарь или по ссылкам на графические элементы внутри
                     if measurement == self._selected_measurement_item:
                          index_to_remove = i
                          break
                if index_to_remove != -1:
                     del current_slice_measurements[index_to_remove]
                     # Обновляем список измерений для среза в словаре
                     self._measurements_by_slice[self.current_slice_index] = current_slice_measurements
                     logger.debug(f"Измерение успешно удалено из списка для среза {self.current_slice_index}. Осталось: {len(current_slice_measurements)}")
                else:
                     logger.warning("Попытка удалить измерение, которое не найдено в списке для текущего среза.")


            except Exception as e:
                logger.error(f"Ошибка при удалении измерения: {e}", exc_info=True)
            finally:
                # Сбрасываем выбранное измерение
                self._selected_measurement_item = None
                # Обновляем состояние кнопки очистки измерений
                self._update_measurement_controls_state()
        else:
            logger.debug("Нет выбранного измерения для удаления.")


    def _calculate_distance_mm(self, point1: QPointF, point2: QPointF):
        """
        Рассчитывает расстояние между двумя точками в миллиметрах,
        используя Pixel Spacing.
        Точки должны быть в координатах изображения (пикселях).
        """
        # Разница в пикселях
        delta_x_pixels = point2.x() - point1.x()
        delta_y_pixels = point2.y() - point1.y()
        row_spacing, col_spacing = self.pixel_spacing

        # Добавляем логирование для проверки значений
        logger.debug(f"Calculating distance: Point1=({point1.x()}, {point1.y()}), Point2=({point2.x()}, {point2.y()})")
        logger.debug(f"Pixel differences: delta_x={delta_x_pixels}, delta_y={delta_y_pixels}")
        logger.debug(f"Pixel Spacing used: row_spacing={row_spacing}, col_spacing={col_spacing}")

        # Разница в миллиметрах
        delta_x_mm = delta_x_pixels * col_spacing
        delta_y_mm = delta_y_pixels * row_spacing

        # Расстояние по теореме Пифагора
        distance_mm = math.sqrt(delta_x_mm**2 + delta_y_mm**2)

        return distance_mm

    @pyqtSlot(bool)
    def toggle_measurement_mode(self, active: bool):
        """
        Публичный метод для установки режима измерения (рисования).
        Вызывается из MainWindow действием "Начать измерение".
        """
        if self._measurement_mode_active == active:
             return

        self._measurement_mode_active = active

        if active:
            logger.info("Режим рисования измерения активирован.")
            QApplication.setOverrideCursor(Qt.CrossCursor) # Меняем курсор на перекрестие
            # Отключаем стандартное панорамирование ViewBox
            self.view_box.setMouseEnabled(x=False, y=False)
            # Сбрасываем начальную точку и временный элемент на всякий случай
            self._measurement_start_point = None
            if self._current_measurement_item:
                 self.view_box.removeItem(self._current_measurement_item['line'])
                 self.view_box.removeItem(self._current_measurement_item['text'])
                 self._current_measurement_item = None
            # Снимаем выделение с любого выбранного измерения при входе в режим рисования
            self._deselect_measurement()


        else:
            logger.info("Режим рисования измерения деактивирован.")
            QApplication.restoreOverrideCursor() # Восстанавливаем стандартный курсор
            # Включаем стандартное панорамирование ViewBox
            self.view_box.setMouseEnabled(x=True, y=True)
            # Сбрасываем начальную точку и временный элемент, если они остались
            self._measurement_start_point = None
            if self._current_measurement_item:
                 self.view_box.removeItem(self._current_measurement_item['line'])
                 self.view_box.removeItem(self._current_measurement_item['text'])
                 self._current_measurement_item = None
            # Снимаем выделение с любого выбранного измерения при выходе из режима рисования
            self._deselect_measurement()


        # Обновляем состояние кнопки очистки измерений
        self._update_measurement_controls_state()
        # Оповещаем MainWindow об изменении состояния режима
        self.measurement_state_changed.emit(self.current_volume_hu is not None, self._measurement_mode_active, len(self._measurements_by_slice.get(self.current_slice_index, [])) > 0)


    @pyqtSlot()
    def _clear_measurements_on_slice(self, slice_index: int):
        """ Удаляет все измерения с указанного среза с ViewBox и очищает список для этого среза. """
        logger.info(f"Очистка измерений на срезе {slice_index}.")
        measurements_to_clear = self._measurements_by_slice.get(slice_index, [])
        for measurement in measurements_to_clear:
             # Проверяем, что элементы еще существуют в ViewBox перед удалением
             if measurement['line'] in self.view_box.addedItems:
                  self.view_box.removeItem(measurement['line'])
             if measurement['text'] in self.view_box.addedItems:
                  self.view_box.removeItem(measurement['text'])

        # Удаляем список измерений для этого среза из словаря
        if slice_index in self._measurements_by_slice:
             del self._measurements_by_slice[slice_index]
             logger.debug(f"Измерения для среза {slice_index} удалены из словаря.")

        # Если очищается текущий срез, сбрасываем выбранное измерение
        if slice_index == self.current_slice_index:
             self._selected_measurement_item = None

        # Обновляем состояние кнопки очистки измерений (для текущего среза)
        self._update_measurement_controls_state()


    @pyqtSlot()
    def clear_all_measurements(self):
        """ Публичный слот для очистки всех измерений на текущем срезе. """
        self._clear_measurements_on_slice(self.current_slice_index)
        logger.info("Вызван публичный метод clear_all_measurements для текущего среза.")

    def _hide_measurements_on_slice(self, slice_index: int):
        measurements_to_hide = self._measurements_by_slice.get(slice_index, [])
        for measurement in measurements_to_hide:
             if measurement['line'] in self.view_box.addedItems:
                  measurement['line'].setVisible(False)
             if measurement['text'] in self.view_box.addedItems:
                  measurement['text'].setVisible(False)
        logger.debug(f"Измерения на срезе {slice_index} скрыты.")

    def _show_measurements_on_slice(self, slice_index: int):
        measurements_to_show = self._measurements_by_slice.get(slice_index, [])
        for measurement in measurements_to_show:
             if measurement['line'] in self.view_box.addedItems:
                  measurement['line'].setVisible(True)
             if measurement['text'] in self.view_box.addedItems:
                  measurement['text'].setVisible(True)
        logger.debug(f"Измерения на срезе {slice_index} отображены. Количество: {len(measurements_to_show)}")


    def _update_measurement_controls_state(self):
        """ Обновляет состояние кнопки очистки измерений. """
        has_measurements_on_current_slice = len(self._measurements_by_slice.get(self.current_slice_index, [])) > 0 or self._current_measurement_item is not None
        data_is_loaded = self.current_volume_hu is not None
        self.measurement_state_changed.emit(data_is_loaded, self._measurement_mode_active, has_measurements_on_current_slice)
