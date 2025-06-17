
"""
Боковая панель с списком исследований для приложения PyLungViewer.
(Версия с явной передачей viewer_panel)
"""

import logging
import os
import shutil
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QHBoxLayout, QLineEdit,
    QComboBox, QMenu, QAction, QMessageBox,
    QFileDialog, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QThread
from PyQt5.QtGui import QIcon, QContextMenuEvent, QCursor

try:
    from .dialogs.export_dialog import ExportDialog
    EXPORT_DIALOG_AVAILABLE = True
except ImportError:
    ExportDialog = None
    EXPORT_DIALOG_AVAILABLE = False
    try: logger = logging.getLogger(__name__)
    except NameError: import logging; logger = logging.getLogger(__name__)
    logger.warning("Файл диалога экспорта (export_dialog.py) не найден.")
try:
    from ..core.dicom_exporter import ExportWorker
    EXPORTER_AVAILABLE = True
except ImportError:
    ExportWorker = None
    EXPORTER_AVAILABLE = False
    try: logger = logging.getLogger(__name__)
    except NameError: import logging; logger = logging.getLogger(__name__)
    logger.warning("Файл экспортера (dicom_exporter.py) не найден.")

logger = logging.getLogger(__name__)


class SidebarPanel(QWidget):
    """Боковая панель с списком исследований."""

    study_selected = pyqtSignal(object)
    series_selected = pyqtSignal(object)
    study_removed_from_view = pyqtSignal(str)
    export_progress = pyqtSignal(int, int)
    export_status_update = pyqtSignal(str)

    def __init__(self, viewer_panel, parent=None):
        """
        Инициализация боковой панели.
            viewer_panel: Ссылка на экземпляр ViewerPanel.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self.viewer_panel = viewer_panel 
        self.studies = []
        self.export_thread = None
        self.export_worker = None
        self._init_ui()
        logger.info("Боковая панель инициализирована")

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        header_layout = QHBoxLayout()
        header_label = QLabel("Исследования")
        header_label.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(header_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск...")
        self.search_input.textChanged.connect(self._on_search_changed)

        main_layout.addLayout(header_layout)
        main_layout.addWidget(self.search_input)

        self.study_tree = QTreeWidget()
        self.study_tree.setHeaderHidden(True)
        self.study_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.study_tree.customContextMenuRequested.connect(self._show_context_menu)
        self.study_tree.itemClicked.connect(self._on_item_clicked)
        self.study_tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        main_layout.addWidget(self.study_tree, 1)

        button_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setToolTip("Обновить список исследований (пока не реализовано)")
        self.refresh_btn.setEnabled(False)
        button_layout.addWidget(self.refresh_btn)

        self.details_btn = QPushButton("Детали")
        self.details_btn.setToolTip("Показать детальную информацию (пока не реализовано)")
        self.details_btn.clicked.connect(self._show_study_details)
        self.details_btn.setEnabled(False)
        button_layout.addWidget(self.details_btn)

        main_layout.addLayout(button_layout)


    def set_studies(self, studies):
        self.studies = studies if studies else []
        self.update_study_list()

    def update_study_list(self):
        self.study_tree.clear()
        if not self.studies:
            logger.info("Нет данных для отображения")
            return
        for study_idx, study in enumerate(self.studies):
            study_item = QTreeWidgetItem(self.study_tree)
            study_date = study.get('date', '')
            study_desc = study.get('description', 'Без описания')
            patient_name = study.get('patient_name', 'Без имени')
            if study_date and len(study_date) == 8:
                study_date = f"{study_date[6:8]}.{study_date[4:6]}.{study_date[0:4]}"
            study_label = f"{patient_name} - {study_desc}"
            if study_date: study_label += f" ({study_date})"
            study_item.setText(0, study_label)
            study_item.setData(0, Qt.UserRole, {'type': 'study', 'index': study_idx, 'data': study})
            for series_idx, series in enumerate(study.get('series', [])):
                series_item = QTreeWidgetItem(study_item)
                series_desc = series.get('description', 'Без описания')
                modality = series.get('modality', '')
                num_files = len(series.get('files', []))
                series_label = f"  - {series_desc} ({modality}, {num_files} сл.)"
                series_item.setText(0, series_label)
                series_item.setData(0, Qt.UserRole, {'type': 'series', 'study_index': study_idx, 'series_index': series_idx, 'data': series})
        if self.study_tree.topLevelItemCount() > 0:
            self.study_tree.topLevelItem(0).setExpanded(True)
        logger.info(f"Отображено {len(self.studies)} исследований")

    def _on_search_changed(self, text):
        search_text = text.lower()
        for i in range(self.study_tree.topLevelItemCount()):
            study_item = self.study_tree.topLevelItem(i)
            study_data = study_item.data(0, Qt.UserRole)['data']
            study_match = (search_text in study_item.text(0).lower() or
                           search_text in study_data.get('patient_id', '').lower())
            series_match_found = False
            for j in range(study_item.childCount()):
                series_item = study_item.child(j)
                series_data = series_item.data(0, Qt.UserRole)['data']
                series_match = (search_text in series_item.text(0).lower() or
                                search_text in series_data.get('id', '').lower())
                series_item.setHidden(not (study_match or series_match))
                if series_match:
                    series_match_found = True
            study_item.setHidden(not (study_match or series_match_found))
            if series_match_found and not study_item.isExpanded():
                 study_item.setExpanded(True)
            elif not series_match_found and study_match and not study_item.isExpanded():
                 study_item.setExpanded(True)


    def _on_item_clicked(self, item, column):
        item_info = item.data(0, Qt.UserRole)
        if item_info is None: return
        if item_info['type'] == 'study':
            self.study_selected.emit(item_info['data'])
        elif item_info['type'] == 'series':
            self.series_selected.emit(item_info['data'])

    def _on_item_double_clicked(self, item, column):
        item_info = item.data(0, Qt.UserRole)
        if item_info is None: return
        if item_info['type'] == 'study':
            item.setExpanded(not item.isExpanded())
        elif item_info['type'] == 'series':
            logger.info(f"Загружаем серию для просмотра: {item.text(0).strip()}")
            self.series_selected.emit(item_info['data'])

    def _show_context_menu(self, position):
        item = self.study_tree.itemAt(position)
        if not item: return
        item_info = item.data(0, Qt.UserRole)
        if item_info is None: return
        context_menu = QMenu(self)
        export_action = None
        remove_action = None
        if item_info['type'] == 'study':
            export_action = QAction("Экспортировать исследование...", self)
            remove_action = QAction("Удалить исследование из списка", self)
            if EXPORT_DIALOG_AVAILABLE and EXPORTER_AVAILABLE:
                context_menu.addAction(export_action)
            context_menu.addAction(remove_action)
        elif item_info['type'] == 'series':
            export_action = QAction("Экспортировать серию...", self)
            if EXPORT_DIALOG_AVAILABLE and EXPORTER_AVAILABLE:
                context_menu.addAction(export_action)
        if not context_menu.actions():
            return
        selected_action = context_menu.exec_(self.study_tree.mapToGlobal(position))
        if selected_action == export_action:
            self._show_export_dialog(item_info)
        elif selected_action == remove_action:
            self._remove_item_from_view(item)

    def _show_export_dialog(self, item_info):
        if not EXPORT_DIALOG_AVAILABLE or ExportDialog is None:
            QMessageBox.warning(self, "Ошибка", "Диалог экспорта недоступен.")
            return
        item_type = item_info['type']
        item_data = item_info['data']
        if item_type == 'study':
            desc = item_data.get('description', item_data.get('id', 'unknown_study'))
        else:
            desc = item_data.get('description', item_data.get('id', 'unknown_series'))
        dialog = ExportDialog(item_type, desc, self)
        dialog.export_settings_confirmed.connect(lambda settings: self._start_export(item_info, settings))
        dialog.exec_()

    def _start_export(self, item_info, settings):
        """ Запускает экспорт в фоновом потоке. """
        if not EXPORTER_AVAILABLE or ExportWorker is None:
            QMessageBox.critical(self, "Ошибка", "Модуль экспорта недоступен.")
            return
        if self.export_thread is not None and self.export_thread.isRunning():
             QMessageBox.warning(self, "Экспорт", "Процесс экспорта уже запущен.")
             return

        source_files = []
        item_type = item_info['type']
        item_data = item_info['data']
        if item_type == 'study':
            for series in item_data.get('series', []):
                for file_meta in series.get('files', []):
                    source_files.append(file_meta.get('file_path'))
        elif item_type == 'series':
            for file_meta in item_data.get('files', []):
                source_files.append(file_meta.get('file_path'))
        source_files = [f for f in source_files if f and os.path.exists(f)]
        if not source_files:
            QMessageBox.warning(self, "Экспорт невозможен", "Не найдены файлы для экспорта.")
            return

        mask_volume = None
        if self.viewer_panel and settings.get('include_mask'): 
             current_series_id = self.viewer_panel.current_series.get('id') if self.viewer_panel.current_series else None
             exporting_series_id = item_data.get('id') if item_type == 'series' else None

             # Определяем ID исследования экспортируемого элемента
             exporting_study_id = None
             if item_type == 'study':
                  exporting_study_id = item_data.get('id')
             elif item_type == 'series':
                  study_idx = item_info.get('study_index')
                  if study_idx is not None and study_idx < len(self.studies):
                      exporting_study_id = self.studies[study_idx].get('id')

             # Определяем ID исследования текущей серии в просмотрщике
             current_study_id = None
             if self.viewer_panel.current_series:
                  for study in self.studies:
                       for series in study.get('series', []):
                            if series.get('id') == current_series_id:
                                 current_study_id = study.get('id')
                                 break
                       if current_study_id: break

             ids_match = False
             if item_type == 'study' and exporting_study_id == current_study_id:
                  ids_match = True
             elif item_type == 'series' and exporting_series_id == current_series_id:
                  ids_match = True

             if ids_match and self.viewer_panel.full_segmentation_mask_volume is not None:
                  mask_volume = self.viewer_panel.full_segmentation_mask_volume
                  logger.info("Объем маски сегментации будет передан для экспорта.")
             elif settings.get('include_mask'): # Если маска запрошена, но условия не выполнены
                  logger.warning("Запрошен экспорт с маской, но она недоступна для данного элемента или не рассчитана.")
                  settings['include_mask'] = False # Сбрасываем флаг

        logger.info(f"Запуск экспорта {len(source_files)} файлов с настройками: {settings}")
        self.export_status_update.emit(f"Экспорт {item_type}...")

        self.export_thread = QThread(self)
        self.export_worker = ExportWorker(source_files, settings, mask_volume=mask_volume)
        self.export_worker.moveToThread(self.export_thread)
        self.export_worker.progress.connect(self.export_progress)
        self.export_worker.finished.connect(self._on_export_finished)
        self.export_worker.error.connect(self._on_export_error)
        self.export_thread.started.connect(self.export_worker.run)
        self.export_thread.finished.connect(self.export_thread.deleteLater)
        self.export_thread.start()

    @pyqtSlot()
    def _on_export_finished(self):
        """ Обработчик завершения экспорта. """
        logger.info("Поток экспорта завершен (сигнал от воркера).")
        if self.export_worker and not self.export_worker.is_cancelled:
             self.export_status_update.emit("Экспорт завершен.")
        else:
             self.export_status_update.emit("Экспорт отменен или завершен с ошибкой.")
        self._clear_export_thread_refs()

    @pyqtSlot(str)
    def _on_export_error(self, error_message):
        """ Обработчик ошибки экспорта. """
        logger.error(f"Ошибка экспорта: {error_message}")
        QMessageBox.critical(self, "Ошибка экспорта", f"Произошла ошибка:\n{error_message}")
        self.export_status_update.emit("Ошибка экспорта.")
        self._clear_export_thread_refs() 

    def _clear_export_thread_refs(self):
        """ Очищает ссылки на поток и воркер экспорта. """
        logger.debug("Очистка ссылок на поток и воркер экспорта.")
        if self.export_worker:
             try: self.export_worker.finished.disconnect(self._on_export_finished)
             except TypeError: pass
             try: self.export_worker.error.disconnect(self._on_export_error)
             except TypeError: pass
             try: self.export_worker.progress.disconnect(self.export_progress)
             except TypeError: pass
        if self.export_thread:
            try: self.export_thread.started.disconnect(self.export_worker.run)
            except TypeError: pass
            try: self.export_thread.finished.disconnect(self.export_thread.deleteLater)
            except TypeError: pass
            if self.export_thread.isRunning():
                self.export_thread.quit()
                self.export_thread.wait(500)
        self.export_thread = None
        self.export_worker = None
        logger.debug("Ссылки на поток и воркер экспорта очищены.")


    def _remove_item_from_view(self, item: QTreeWidgetItem):
        item_info = item.data(0, Qt.UserRole)
        if item_info is None or item_info['type'] != 'study':
            logger.warning("Попытка удалить не исследование.")
            return
        study_data = item_info['data']
        study_id = study_data.get('id', 'unknown')
        study_desc = study_data.get('description', study_id)
        reply = QMessageBox.question(self, "Удалить из списка?",
                                     f"Вы уверены, что хотите убрать исследование '{study_desc}' из текущего списка?\n"
                                     f"(Файлы на диске останутся без изменений)",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            logger.info(f"Удаление исследования '{study_desc}' (ID: {study_id}) из списка.")
            parent_item = item.parent()
            if parent_item:
                 parent_item.removeChild(item)
            else:
                 index = self.study_tree.indexOfTopLevelItem(item)
                 if index != -1:
                      self.study_tree.takeTopLevelItem(index)
            original_len = len(self.studies)
            self.studies = [s for s in self.studies if s.get('id') != study_id]
            if len(self.studies) < original_len:
                 logger.info("Исследование удалено из внутреннего списка.")
                 self.study_removed_from_view.emit(study_id)
            else:
                 logger.warning("Не удалось найти исследование для удаления во внутреннем списке.")


    def _show_study_details(self):
        selected_items = self.study_tree.selectedItems()
        if not selected_items: return
        selected_item = selected_items[0]
        logger.info(f"Показываем детали для: {selected_item.text(0).strip()} (не реализовано)")
        QMessageBox.information(self, "Детали", "Функция отображения деталей пока не реализована.")

