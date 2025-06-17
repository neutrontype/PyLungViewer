"""
Модуль для экспорта DICOM данных в различных форматах.
(Версия с выборочным смешиванием для полупрозрачной маски на PNG)
"""

import logging
import os
import shutil
import pydicom
import numpy as np
import cv2 
from PyQt5.QtCore import QObject, pyqtSignal, QThread
from PyQt5.QtGui import QColor
from .dicom_loader import DicomLoader
from ..utils.window_presets import WindowPresets

logger = logging.getLogger(__name__)

MASK_ALPHA_FOR_PNG = 80 / 255.0 
MASK_COLOR_BGR = (0, 0, 255)

class ExportWorker(QObject):
    """Воркер для выполнения экспорта в фоновом потоке."""
    finished = pyqtSignal()
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int) 

    def __init__(self, files_to_export, settings, mask_volume=None):
        super().__init__()
        self.files_to_export = files_to_export
        self.settings = settings
        self.mask_volume = mask_volume
        self.is_cancelled = False
        self.loader = DicomLoader()

    def run(self):
        """Запускает процесс экспорта."""
        try:
            export_format = self.settings.get('format', 'dicom')
            dest_dir = self.settings.get('dest_dir')
            anonymize = self.settings.get('anonymize', False)
            apply_window = self.settings.get('apply_window', False)
            include_mask = self.settings.get('include_mask', False)

            if not dest_dir or not os.path.isdir(dest_dir):
                raise ValueError("Папка назначения не существует или не указана.")
            if not self.files_to_export:
                raise ValueError("Нет файлов для экспорта.")

            mask_available_for_export = False
            if export_format == 'png' and include_mask:
                if self.mask_volume is None:
                     logger.warning("Запрошен экспорт PNG с маской, но объем маски недоступен. Маска не будет добавлена.")
                elif len(self.files_to_export) != self.mask_volume.shape[0]:
                    logger.error(f"Количество файлов ({len(self.files_to_export)}) не совпадает с количеством срезов маски ({self.mask_volume.shape[0]}). Экспорт PNG с маской невозможен.")
                else:
                    mask_available_for_export = True

            total_files = len(self.files_to_export)
            logger.info(f"Начало экспорта {total_files} файлов в формате {export_format} в {dest_dir}")
            logger.info(f"Настройки: Анонимизация={anonymize}, Применить окно={apply_window}, Включить маску={include_mask} (Реально доступна: {mask_available_for_export})")

            exported_count = 0
            errors = 0

            for i, src_path in enumerate(self.files_to_export):
                if self.is_cancelled:
                    logger.info("Экспорт прерван пользователем.")
                    break
                if not os.path.exists(src_path):
                    logger.warning(f"Исходный файл не найден, пропуск: {src_path}")
                    errors += 1
                    continue

                base_filename = os.path.basename(src_path)
                base_name_no_ext = os.path.splitext(base_filename)[0]

                try:
                    if export_format == 'dicom':
                        dest_path = os.path.join(dest_dir, base_filename)
                        self._export_dicom_file(src_path, dest_path, anonymize)
                    elif export_format == 'png':
                        png_filename = f"{base_name_no_ext}_slice{i:04d}.png"
                        dest_path_png = os.path.join(dest_dir, png_filename)
                        current_mask_slice = self.mask_volume[i] if mask_available_for_export else None
                        self._export_png_file(src_path, dest_path_png, apply_window, current_mask_slice)
                    else:
                        logger.warning(f"Неподдерживаемый формат экспорта: {export_format}")
                        errors += 1
                        continue

                    exported_count += 1
                except Exception as e:
                    logger.error(f"Ошибка при экспорте файла {src_path}: {e}", exc_info=True)
                    errors += 1

                self.progress.emit(i + 1, total_files)

            logger.info(f"Экспорт завершен. Успешно: {exported_count}, Ошибки: {errors}")

        except Exception as e:
            logger.error(f"Критическая ошибка в процессе экспорта: {e}", exc_info=True)
            self.error.emit(f"Ошибка экспорта: {e}")
        finally:
            self.finished.emit()

    def _export_dicom_file(self, src_path, dest_path, anonymize):
        if not anonymize:
            shutil.copy2(src_path, dest_path)
        else:
            try:
                ds = pydicom.dcmread(src_path)
                tags_to_remove = [
                    (0x0010, 0x0010), (0x0010, 0x0020), (0x0010, 0x0030), (0x0010, 0x0032),
                    (0x0010, 0x1000), (0x0010, 0x1001), (0x0010, 0x2160), (0x0010, 0x2180),
                    (0x0010, 0x4000),
                ]
                for group, element in tags_to_remove:
                    if (group, element) in ds:
                        ds[group, element].value = ""
                if hasattr(ds, 'PatientName'): ds.PatientName = ""
                if hasattr(ds, 'PatientID'): ds.PatientID = ""
                ds.save_as(dest_path)
            except Exception as e:
                logger.error(f"Ошибка анонимизации и сохранения {src_path}: {e}")
                raise

    def _export_png_file(self, src_path, dest_path_png, apply_window, mask_slice=None):
        """
        Экспортирует один DICOM срез как PNG, опционально накладывая маску.
        """
        try:
            pixel_data_hu = self.loader.load_pixel_data({'file_path': src_path})
            if pixel_data_hu is None:
                raise ValueError("Не удалось загрузить пиксельные данные.")

            if apply_window:
                window_center, window_width = WindowPresets.get_preset("Легочное")
                img_gray = WindowPresets.apply_window(pixel_data_hu, window_center, window_width)
            else:
                min_hu, max_hu = np.min(pixel_data_hu), np.max(pixel_data_hu)
                if max_hu > min_hu:
                     img_normalized = 255 * (pixel_data_hu - min_hu) / (max_hu - min_hu)
                     img_gray = np.clip(img_normalized, 0, 255).astype(np.uint8)
                else:
                     img_gray = np.zeros_like(pixel_data_hu, dtype=np.uint8)

            if mask_slice is not None:
                logger.info(f"Накладываем маску на PNG: {os.path.basename(dest_path_png)}")
                if img_gray.shape != mask_slice.shape:
                    logger.warning(f"Размер маски {mask_slice.shape} не совпадает с изображением {img_gray.shape}. Маска не будет наложена.")
                    img_to_save = img_gray
                else:
                    img_bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
                    mask_color_layer = np.zeros_like(img_bgr, dtype=np.uint8)
                    mask_color_layer[:] = MASK_COLOR_BGR

                    alpha = MASK_ALPHA_FOR_PNG
                    beta = 1.0 - alpha
                    blended_overlay = cv2.addWeighted(img_bgr, beta, mask_color_layer, alpha, 0.0)

                    mask_bool = mask_slice > 0
                    img_to_save = img_bgr.copy() 
                    img_to_save[mask_bool] = blended_overlay[mask_bool]
            else:
                img_to_save = img_gray
                logger.debug(f"Сохраняем PNG без маски: {os.path.basename(dest_path_png)}")

            # 3. Сохраняем результат
            success = cv2.imwrite(dest_path_png, img_to_save)
            if not success:
                raise IOError(f"Не удалось сохранить PNG файл: {dest_path_png}")

        except Exception as e:
            logger.error(f"Ошибка при экспорте PNG из {src_path}: {e}")
            raise

    def cancel(self):
        """Устанавливает флаг отмены."""
        logger.info("Получен запрос на отмену экспорта.")
        self.is_cancelled = True

