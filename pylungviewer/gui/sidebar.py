#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Боковая панель с списком исследований для приложения PyLungViewer.
"""

import logging
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QHBoxLayout, QLineEdit, 
    QComboBox, QMenu, QAction
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QIcon, QContextMenuEvent

logger = logging.getLogger(__name__)


class SidebarPanel(QWidget):
    """Боковая панель с списком исследований."""
    
    # Сигналы для коммуникации с другими компонентами
    study_selected = pyqtSignal(object)  # Сигнал выбора исследования
    series_selected = pyqtSignal(object)  # Сигнал выбора серии
    
    def __init__(self, parent=None):
        """
        Инициализация боковой панели.
        
        Args:
            parent: Родительский виджет.
        """
        super().__init__(parent)
        
        # Список исследований
        self.studies = []
        
        # Инициализация UI
        self._init_ui()
        
        logger.info("Боковая панель инициализирована")
    
    def _init_ui(self):
        """Инициализация пользовательского интерфейса."""
        # Главный layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        
        # Заголовок панели
        header_layout = QHBoxLayout()
        header_label = QLabel("Исследования")
        header_label.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(header_label)
        
        # Строка поиска
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск...")
        self.search_input.textChanged.connect(self._on_search_changed)
        
        main_layout.addLayout(header_layout)
        main_layout.addWidget(self.search_input)
        
        # Дерево исследований
        self.study_tree = QTreeWidget()
        self.study_tree.setHeaderHidden(True)
        self.study_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.study_tree.customContextMenuRequested.connect(self._show_context_menu)
        self.study_tree.itemClicked.connect(self._on_item_clicked)
        self.study_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        
        main_layout.addWidget(self.study_tree, 1)  # 1 - растягивается по вертикали
        
        # Кнопки действий
        button_layout = QHBoxLayout()
        
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.update_study_list)
        button_layout.addWidget(self.refresh_btn)
        
        self.details_btn = QPushButton("Детали")
        self.details_btn.clicked.connect(self._show_study_details)
        button_layout.addWidget(self.details_btn)
        
        main_layout.addLayout(button_layout)
    
    def set_studies(self, studies):
        """
        Установка списка исследований.
        
        Args:
            studies: Список с данными исследований.
        """
        self.studies = studies
        self.update_study_list()
    
    def update_study_list(self):
        """Обновление списка исследований."""
        # Очищаем текущее дерево
        self.study_tree.clear()
        
        if not self.studies:
            logger.info("Нет данных для отображения")
            return
        
        # Заполняем дерево исследованиями
        for study in self.studies:
            study_item = QTreeWidgetItem(self.study_tree)
            
            # Формируем строку для отображения
            study_date = study.get('date', '')
            study_desc = study.get('description', 'Неизвестно')
            patient_name = study.get('patient_name', '')
            
            # Форматируем дату (если она в формате YYYYMMDD)
            if study_date and len(study_date) == 8:
                study_date = f"{study_date[6:8]}.{study_date[4:6]}.{study_date[0:4]}"
            
            study_label = f"{patient_name} - {study_desc}"
            if study_date:
                study_label += f" ({study_date})"
            
            study_item.setText(0, study_label)
            study_item.setData(0, Qt.UserRole, study)
            
            # Добавляем серии как дочерние элементы
            for series in study.get('series', []):
                series_item = QTreeWidgetItem(study_item)
                
                series_desc = series.get('description', 'Неизвестно')
                modality = series.get('modality', '')
                
                series_label = f"{series_desc}"
                if modality:
                    series_label += f" ({modality})"
                
                series_item.setText(0, series_label)
                series_item.setData(0, Qt.UserRole, series)
        
        # Разворачиваем первое исследование, если оно есть
        if self.study_tree.topLevelItemCount() > 0:
            self.study_tree.topLevelItem(0).setExpanded(True)
        
        logger.info(f"Отображено {len(self.studies)} исследований")
    
    def _on_search_changed(self, text):
        """
        Обработчик изменения текста в строке поиска.
        
        Args:
            text: Текст для поиска.
        """
        # Реализация фильтрации исследований по тексту
        # Перебираем все элементы верхнего уровня (исследования)
        for i in range(self.study_tree.topLevelItemCount()):
            study_item = self.study_tree.topLevelItem(i)
            study_visible = text.lower() in study_item.text(0).lower()
            
            # Проверяем дочерние элементы (серии)
            series_visible = False
            for j in range(study_item.childCount()):
                series_item = study_item.child(j)
                if text.lower() in series_item.text(0).lower():
                    series_visible = True
                    series_item.setHidden(False)
                else:
                    series_item.setHidden(not study_visible)
            
            # Элемент виден, если либо он сам содержит текст, либо его дочерние элементы
            study_item.setHidden(not (study_visible or series_visible))
    
    def _on_item_clicked(self, item, column):
        """
        Обработчик клика по элементу дерева.
        
        Args:
            item: Выбранный элемент.
            column: Номер колонки.
        """
        # Получаем данные элемента
        item_data = item.data(0, Qt.UserRole)
        if item_data is None:
            return
        
        # Определяем тип элемента (исследование или серия)
        if item.parent() is None:  # Это исследование (корневой элемент)
            logger.info(f"Выбрано исследование: {item.text(0)}")
            self.study_selected.emit(item_data)
        else:  # Это серия
            logger.info(f"Выбрана серия: {item.text(0)}")
            self.series_selected.emit(item_data)
    
    def _on_item_double_clicked(self, item, column):
        """
        Обработчик двойного клика по элементу дерева.
        
        Args:
            item: Выбранный элемент.
            column: Номер колонки.
        """
        # При двойном клике по исследованию раскрываем/скрываем его
        if item.parent() is None:  # Это исследование (корневой элемент)
            if item.isExpanded():
                item.setExpanded(False)
            else:
                item.setExpanded(True)
        else:  # Это серия, загружаем для просмотра
            item_data = item.data(0, Qt.UserRole)
            if item_data:
                logger.info(f"Загружаем серию для просмотра: {item.text(0)}")
                self.series_selected.emit(item_data)
    
    def _show_context_menu(self, position):
        """
        Отображение контекстного меню для элемента дерева.
        
        Args:
            position: Позиция курсора.
        """
        # Получаем элемент, на котором был клик
        item = self.study_tree.itemAt(position)
        if not item:
            return
        
        # Создаем контекстное меню
        context_menu = QMenu(self)
        
        # Определяем тип элемента
        if item.parent() is None:  # Это исследование
            view_action = QAction("Просмотреть все серии", self)
            context_menu.addAction(view_action)
            
            export_action = QAction("Экспортировать исследование", self)
            context_menu.addAction(export_action)
            
            delete_action = QAction("Удалить исследование", self)
            context_menu.addAction(delete_action)
        else:  # Это серия
            view_action = QAction("Просмотреть серию", self)
            context_menu.addAction(view_action)
            
            export_action = QAction("Экспортировать серию", self)
            context_menu.addAction(export_action)
        
        # Показываем контекстное меню
        selected_action = context_menu.exec_(self.study_tree.mapToGlobal(position))
        
        # Обрабатываем выбранное действие
        if selected_action == view_action:
            self._on_item_clicked(item, 0)
    
    def _show_study_details(self):
        """Отображение детальной информации о выбранном исследовании."""
        # Получаем выбранный элемент
        selected_items = self.study_tree.selectedItems()
        if not selected_items:
            return
        
        selected_item = selected_items[0]
        item_data = selected_item.data(0, Qt.UserRole)
        
        if not item_data:
            return
        
        # Здесь будет код для отображения детальной информации
        # Например, в отдельном диалоговом окне
        logger.info(f"Показываем детали для: {selected_item.text(0)}")