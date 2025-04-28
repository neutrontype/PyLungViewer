#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Диалог настроек экспорта DICOM/PNG.
(Версия с опцией включения маски в PNG)
"""

import os
import logging
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLabel,
    QCheckBox, QRadioButton, QButtonGroup, QGroupBox, QDialogButtonBox,
    QSpacerItem, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)

class ExportDialog(QDialog):
    """Диалог настроек экспорта."""

    export_settings_confirmed = pyqtSignal(dict)

    def __init__(self, item_type, item_desc, parent=None):
        """
        Инициализация диалога.
        Args:
            item_type (str): 'study' или 'series'.
            item_desc (str): Описание элемента для заголовка.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self.item_type = item_type
        self.settings = {}
        self.setWindowTitle(f"Экспорт: {item_desc}")
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)

        format_group = QGroupBox("Формат экспорта")
        format_layout = QVBoxLayout(format_group)
        self.format_dicom_radio = QRadioButton("Оригинальные DICOM файлы")
        self.format_png_radio = QRadioButton("Последовательность PNG изображений")
        self.format_dicom_radio.setChecked(True)
        self.format_dicom_radio.toggled.connect(self._update_options_state)
        format_layout.addWidget(self.format_dicom_radio)
        format_layout.addWidget(self.format_png_radio)
        layout.addWidget(format_group)

        self.dicom_options_group = QGroupBox("Опции DICOM")
        dicom_options_layout = QVBoxLayout(self.dicom_options_group)
        self.anonymize_checkbox = QCheckBox("Анонимизировать (удалить имя/ID пациента)")
        self.anonymize_checkbox.setToolTip("Удаляет теги PatientName и PatientID из DICOM файлов.")
        dicom_options_layout.addWidget(self.anonymize_checkbox)
        layout.addWidget(self.dicom_options_group)

        self.png_options_group = QGroupBox("Опции PNG")
        png_options_layout = QVBoxLayout(self.png_options_group)
        self.apply_window_checkbox = QCheckBox("Применить текущее окно яркости/контраста")
        self.apply_window_checkbox.setToolTip("Сохраняет PNG с примененными настройками окна (Легочное), иначе - нормализованные данные.")
        self.apply_window_checkbox.setEnabled(True)
        png_options_layout.addWidget(self.apply_window_checkbox)

        # --- Добавлен чекбокс для маски ---
        self.include_mask_checkbox = QCheckBox("Включить маску сегментации (если доступна)")
        self.include_mask_checkbox.setToolTip("Наложить рассчитанную маску сегментации на изображение PNG.")
        # Доступность этого чекбокса может зависеть от того, была ли выполнена сегментация
        # Пока оставим активным, логика будет в экспортере
        self.include_mask_checkbox.setEnabled(True)
        png_options_layout.addWidget(self.include_mask_checkbox)
        # ---------------------------------

        layout.addWidget(self.png_options_group)
        self.png_options_group.setVisible(False)

        dest_layout = QHBoxLayout()
        dest_layout.addWidget(QLabel("Папка назначения:"))
        self.dest_folder_label = QLabel("<i>Не выбрана</i>")
        self.dest_folder_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.dest_folder_button = QPushButton("Обзор...")
        self.dest_folder_button.clicked.connect(self._select_dest_folder)
        dest_layout.addWidget(self.dest_folder_label)
        dest_layout.addWidget(self.dest_folder_button)
        layout.addLayout(dest_layout)

        layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Minimum, QSizePolicy.Expanding))

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Ok).setEnabled(False)
        layout.addWidget(button_box)

        self._update_options_state()

    def _update_options_state(self):
        is_dicom = self.format_dicom_radio.isChecked()
        self.dicom_options_group.setVisible(is_dicom)
        self.png_options_group.setVisible(not is_dicom)

    def _select_dest_folder(self):
        parent_settings = self.parent().settings if self.parent() and hasattr(self.parent(), 'settings') else None
        last_dir = parent_settings.value("Paths/last_export_dir", os.path.expanduser("~")) if parent_settings else os.path.expanduser("~")
        dest_dir = QFileDialog.getExistingDirectory(
            self, f"Выберите папку для экспорта",
            last_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if dest_dir:
            self.settings['dest_dir'] = dest_dir
            self.dest_folder_label.setText(f"<i>{dest_dir}</i>")
            self.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).setEnabled(True)
            if parent_settings:
                parent_settings.setValue("Paths/last_export_dir", dest_dir)

    def _on_accept(self):
        if 'dest_dir' not in self.settings or not self.settings['dest_dir']:
            QMessageBox.warning(self, "Папка не выбрана", "Пожалуйста, выберите папку назначения.")
            return
        self.settings['format'] = 'dicom' if self.format_dicom_radio.isChecked() else 'png'
        if self.settings['format'] == 'dicom':
            self.settings['anonymize'] = self.anonymize_checkbox.isChecked()
            self.settings['apply_window'] = False
            self.settings['include_mask'] = False # Маска не применяется к DICOM
        else: # png
            self.settings['anonymize'] = False
            self.settings['apply_window'] = self.apply_window_checkbox.isChecked()
            # --- Считываем состояние чекбокса маски ---
            self.settings['include_mask'] = self.include_mask_checkbox.isChecked()
            # -----------------------------------------
        logger.info(f"Настройки экспорта подтверждены: {self.settings}")
        self.export_settings_confirmed.emit(self.settings)
        self.accept()

    def get_export_settings(self):
        return self.settings

