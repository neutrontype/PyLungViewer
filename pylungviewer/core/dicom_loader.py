
"""
Модуль для загрузки и обработки DICOM файлов.
"""

import os
import logging
import pydicom
import numpy as np
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtCore import QObject, pyqtSignal, QSettings

logger = logging.getLogger(__name__)


class DicomLoader(QObject):
    """Загрузчик DICOM файлов."""
    
    loading_progress = pyqtSignal(int, int) 
    loading_complete = pyqtSignal(list)      
    loading_error = pyqtSignal(str)          
    
    def __init__(self, settings=None, parent=None):
        """
        Инициализация загрузчика DICOM.
        
        Args:
            settings: Настройки приложения.
            parent: Родительский объект.
        """
        super().__init__(parent)
        self.settings = settings or QSettings()
        
        self._studies_cache = {}
        
        self.dicom_extensions = ['.dcm', '.dicom', '.dic', '']
        
        logger.info("Инициализирован загрузчик DICOM")
    
    def load_files(self, file_paths, recursive=False):
        """
        Загрузка DICOM файлов из указанных путей.
        
        Args:
            file_paths: Список путей к файлам или директориям.
            recursive: Флаг для рекурсивного поиска файлов в директориях.
            
        Returns:
            list: Список загруженных исследований.
        """
        dicomdir_files = []
        other_files = []
        
        for path in file_paths:
            if os.path.isfile(path) and os.path.basename(path).upper() == "DICOMDIR":
                dicomdir_files.append(path)
            else:
                other_files.append(path)
        
        # Расширяем список файлов, если переданы директории
        all_files = self._expand_file_paths(other_files, recursive)
        
        # Добавляем DICOMDIR файлы в общий список
        all_files.extend(dicomdir_files)
        
        logger.info(f"Начата загрузка {len(all_files)} файлов")
        
        # Отправляем сигнал о начале загрузки
        self.loading_progress.emit(0, len(all_files))
        
        try:
            # Загружаем метаданные DICOM из файлов
            dicom_data = self._load_dicom_metadata(all_files)
            
            # Проверяем, что данные получены
            if not dicom_data:
                logger.warning("Не удалось загрузить DICOM данные")
                self.loading_error.emit("Не удалось загрузить DICOM данные из выбранных файлов")
                return []
            
            # Группируем файлы по исследованиям и сериям
            studies = self._organize_by_study(dicom_data)
            
            # Кэшируем результаты
            for study in studies:
                study_id = study.get('id', 'unknown')
                self._studies_cache[study_id] = study
            
            # Отправляем сигнал о завершении загрузки
            self.loading_complete.emit(studies)
            
            logger.info(f"Загрузка завершена. Загружено {len(studies)} исследований")
            return studies
            
        except Exception as e:
            error_msg = f"Ошибка при загрузке DICOM файлов: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self.loading_error.emit(error_msg)
            return []
    
    def _expand_file_paths(self, file_paths, recursive=False):
        """
        Расширение списка путей, включая файлы из директорий.
        
        Args:
            file_paths: Список путей к файлам или директориям.
            recursive: Флаг для рекурсивного поиска файлов в директориях.
            
        Returns:
            list: Расширенный список путей к файлам.
        """
        all_files = []
        
        for path in file_paths:
            if os.path.isfile(path):
                # Проверяем расширение файла
                _, ext = os.path.splitext(path.lower())
                if ext in self.dicom_extensions or self._is_dicom_file(path):
                    all_files.append(path)
            elif os.path.isdir(path):
                # Рекурсивно добавляем файлы из директории
                for root, _, files in os.walk(path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        _, ext = os.path.splitext(file_path.lower())
                        if ext in self.dicom_extensions or self._is_dicom_file(file_path):
                            all_files.append(file_path)
                    
                    # Прекращаем рекурсию, если не задан флаг recursive
                    if not recursive:
                        break
        
        return all_files
    
    def _is_dicom_file(self, file_path):
        """
        Проверка, является ли файл DICOM.
        
        Args:
            file_path: Путь к файлу.
            
        Returns:
            bool: True, если файл является DICOM.
        """
        try:
            # Пытаемся прочитать заголовок DICOM
            with open(file_path, 'rb') as f:
                # DICOM файлы начинаются с 128 байт преамбулы, затем идет "DICM"
                f.seek(128)
                return f.read(4) == b'DICM'
        except Exception:
            return False
    
    def _load_dicom_metadata(self, file_paths):
        """
        Загрузка метаданных DICOM из файлов.
        
        Args:
            file_paths: Список путей к файлам.
            
        Returns:
            list: Список с метаданными DICOM.
        """
        dicom_data = []
        total_files = len(file_paths)
        current_progress = 0
        
        # Используем многопоточность для ускорения загрузки
        with ThreadPoolExecutor() as executor:
            for i, result in enumerate(executor.map(self._load_dicom_file, file_paths)):
                if result:
                    # Проверяем, вернулся ли список (при обработке DICOMDIR) или одиночный элемент
                    if isinstance(result, list):
                        dicom_data.extend(result)
                    else:
                        dicom_data.append(result)
                
                # Обновляем прогресс
                current_progress += 1
                self.loading_progress.emit(current_progress, total_files)
        
        logger.info(f"Загружены метаданные для {len(dicom_data)} DICOM объектов")
        return dicom_data
    
    def _load_dicom_file(self, file_path):
        """
        Загрузка отдельного DICOM файла.
        
        Args:
            file_path: Путь к файлу.
            
        Returns:
            dict: Метаданные DICOM или None в случае ошибки.
        """
        try:
            # Проверяем, является ли файл DICOMDIR
            basename = os.path.basename(file_path).upper()
            if basename == "DICOMDIR":
                return self._process_dicomdir(file_path)
            
            # Загружаем только метаданные для экономии памяти
            ds = pydicom.dcmread(file_path, force=True, stop_before_pixels=True)
            
            # Извлекаем необходимые теги
            metadata = {
                'file_path': file_path,
                'study_instance_uid': getattr(ds, 'StudyInstanceUID', 'unknown'),
                'series_instance_uid': getattr(ds, 'SeriesInstanceUID', 'unknown'),
                'instance_number': getattr(ds, 'InstanceNumber', 0),
                'study_date': getattr(ds, 'StudyDate', ''),
                'study_time': getattr(ds, 'StudyTime', ''),
                'study_description': getattr(ds, 'StudyDescription', ''),
                'series_description': getattr(ds, 'SeriesDescription', ''),
                'modality': getattr(ds, 'Modality', ''),
                'patient_id': getattr(ds, 'PatientID', 'unknown'),
                'patient_name': str(getattr(ds, 'PatientName', '')),
                'slice_location': getattr(ds, 'SliceLocation', 0),
                'ds': ds  
            }
            
            return metadata
        except Exception as e:
            logger.warning(f"Не удалось загрузить DICOM файл {file_path}: {str(e)}")
            return None
            
    def _process_dicomdir(self, dicomdir_path):
        """
        Обработка файла DICOMDIR и извлечение связанных изображений.
        
        Args:
            dicomdir_path: Путь к файлу DICOMDIR.
            
        Returns:
            list: Список метаданных всех найденных DICOM файлов.
        """
        try:
            logger.info(f"Обработка DICOMDIR файла: {dicomdir_path}")
            dicomdir = pydicom.dcmread(dicomdir_path)
            
            logger.info(f"DICOMDIR содержит следующие теги верхнего уровня: {[elem.name for elem in dicomdir]}")
            
            base_dir = os.path.dirname(dicomdir_path)
            logger.info(f"Базовая директория для DICOM файлов: {base_dir}")
            
            result = []
            dicom_files = []
            
            for root, _, files in os.walk(base_dir):
                for file in files:
                    if file.lower().endswith(('.dcm', '.ima', '.img')) or file == "DICOMDIR":
                        continue 
                    
                    file_path = os.path.join(root, file)
                    try:
                        if self._is_dicom_file(file_path):
                            dicom_files.append(file_path)
                    except Exception as e:
                        logger.debug(f"Ошибка при проверке файла {file_path}: {str(e)}")
            
            logger.info(f"Найдено {len(dicom_files)} потенциальных DICOM файлов")
            
            # Загружаем каждый найденный DICOM файл
            for file_path in dicom_files:
                try:
                    ds = pydicom.dcmread(file_path, force=True, stop_before_pixels=True)
                    
                    # Создаем метаданные
                    metadata = {
                        'file_path': file_path,
                        'study_instance_uid': getattr(ds, 'StudyInstanceUID', 'unknown'),
                        'series_instance_uid': getattr(ds, 'SeriesInstanceUID', 'unknown'),
                        'instance_number': getattr(ds, 'InstanceNumber', 0),
                        'study_date': getattr(ds, 'StudyDate', ''),
                        'study_time': getattr(ds, 'StudyTime', ''),
                        'study_description': getattr(ds, 'StudyDescription', ''),
                        'series_description': getattr(ds, 'SeriesDescription', ''),
                        'modality': getattr(ds, 'Modality', ''),
                        'patient_id': getattr(ds, 'PatientID', 'unknown'),
                        'patient_name': str(getattr(ds, 'PatientName', '')),
                        'slice_location': getattr(ds, 'SliceLocation', 0),
                        'ds': ds
                    }
                    
                    result.append(metadata)
                except Exception as e:
                    logger.warning(f"Ошибка при загрузке файла {file_path}: {str(e)}")
            
            logger.info(f"Успешно загружено {len(result)} DICOM файлов")
            return result
        except Exception as e:
            logger.error(f"Ошибка при обработке DICOMDIR {dicomdir_path}: {str(e)}", exc_info=True)
            return []
    
    def _organize_by_study(self, dicom_data):
        """
        Группировка DICOM файлов по исследованиям и сериям.
        
        Args:
            dicom_data: Список с метаданными DICOM.
            
        Returns:
            list: Список исследований с вложенными сериями.
        """
        studies_dict = defaultdict(lambda: {
            'id': '',
            'date': '',
            'time': '',
            'description': '',
            'patient_id': '',
            'patient_name': '',
            'series': defaultdict(list)
        })
        
        for item in dicom_data:
            study_uid = item['study_instance_uid']
            series_uid = item['series_instance_uid']
            
            studies_dict[study_uid]['id'] = study_uid
            studies_dict[study_uid]['date'] = item['study_date']
            studies_dict[study_uid]['time'] = item['study_time']
            studies_dict[study_uid]['description'] = item['study_description']
            studies_dict[study_uid]['patient_id'] = item['patient_id']
            studies_dict[study_uid]['patient_name'] = item['patient_name']
            
            studies_dict[study_uid]['series'][series_uid].append(item)
        
        # Преобразуем словарь в список исследований
        studies = []
        for study_uid, study_data in studies_dict.items():
            # Преобразуем серии из словаря в список и сортируем файлы в каждой серии
            series_list = []
            for series_uid, files in study_data['series'].items():
                # Сортируем файлы по номеру экземпляра или положению среза
                sorted_files = sorted(files, key=lambda x: (x['instance_number'], x['slice_location']))
                
                # Собираем информацию о серии
                if sorted_files:
                    first_file = sorted_files[0]
                    series_info = {
                        'id': series_uid,
                        'description': first_file['series_description'],
                        'modality': first_file['modality'],
                        'files': sorted_files
                    }
                    series_list.append(series_info)
            
            # Добавляем список серий в исследование
            study_data['series'] = sorted(series_list, key=lambda x: x['description'])
            studies.append(study_data)
        
        # Сортируем исследования по дате
        return sorted(studies, key=lambda x: x['date'], reverse=True)
    
    def get_study(self, study_id):
        """
        Получение исследования по ID.
        
        Args:
            study_id: ID исследования.
            
        Returns:
            dict: Данные исследования или None, если не найдено.
        """
        return self._studies_cache.get(study_id)
    
    def get_series(self, study_id, series_id):
        """
        Получение серии по ID исследования и ID серии.
        
        Args:
            study_id: ID исследования.
            series_id: ID серии.
            
        Returns:
            dict: Данные серии или None, если не найдено.
        """
        study = self.get_study(study_id)
        if not study:
            return None
        
        for series in study['series']:
            if series['id'] == series_id:
                return series
        
        return None
    
    def load_pixel_data(self, file_metadata):
        """
        Загрузка пиксельных данных из DICOM файла.
        
        Args:
            file_metadata: Метаданные файла.
            
        Returns:
            numpy.ndarray: Пиксельные данные или None в случае ошибки.
        """
        try:
            file_path = file_metadata['file_path']
            
            # Загружаем полный DICOM файл, если до этого загружали только метаданные
            if 'PixelData' not in file_metadata.get('ds', {}):
                ds = pydicom.dcmread(file_path)
            else:
                ds = file_metadata['ds']
            
            pixel_data = ds.pixel_array
            
            if hasattr(ds, 'RescaleSlope') and hasattr(ds, 'RescaleIntercept'):
                pixel_data = pixel_data * ds.RescaleSlope + ds.RescaleIntercept
            
            return pixel_data
        except Exception as e:
            logger.error(f"Ошибка при загрузке пиксельных данных из {file_path}: {str(e)}")
            return None
    
    def clear_cache(self):
        """Очистка кэша загруженных исследований."""
        self._studies_cache.clear()
        logger.info("Кэш загруженных исследований очищен")