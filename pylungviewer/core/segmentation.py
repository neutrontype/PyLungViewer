#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Модуль для сегментации легких с использованием обученной модели PyTorch.
(Версия с сегментацией всего объема)
"""

import logging
import os
import torch
import segmentation_models_pytorch as smp
import numpy as np
import cv2
# Убрали skimage, так как cv2.resize достаточно
# from skimage.transform import resize
from PyQt5.QtCore import QObject, pyqtSignal # Добавлено для сигналов прогресса

logger = logging.getLogger(__name__)

# --- Параметры, соответствующие обучению ---
# !!! Убедитесь, что эти параметры ТОЧНО совпадают с параметрами из скрипта обучения !!!
IMG_SIZE = 256
WINDOW_LEVEL = -600
WINDOW_WIDTH = 1500
ENCODER = 'resnet34' # Должен совпадать с моделью в .pth файле
ENCODER_WEIGHTS = None # Веса не нужны для загрузки state_dict
CLASSES = 1
ACTIVATION = None # Модель выдает логиты

# --- Класс-сигнальщик для передачи прогресса из predict_volume ---
class SegmentationSignals(QObject):
    progress = pyqtSignal(int, int) # current_slice, total_slices

# --- Класс сегментатора ---
class LungSegmenter:
    """Класс для выполнения сегментации легких."""

    def __init__(self, model_path=None):
        """
        Инициализация сегментатора.

        Args:
            model_path (str, optional): Путь к файлу модели (.pth). Defaults to None.
        """
        # Проверяем наличие CUDA
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.model_path = None
        self.signals = SegmentationSignals() # Создаем экземпляр сигналов
        self._is_cancelled = False # Флаг для отмены сегментации объема
        logger.info(f"Используемое устройство для сегментации: {self.device}")
        # Автоматическая загрузка модели теперь происходит в ViewerPanel
        # if model_path:
        #     self.load_model(model_path)

    def load_model(self, model_path):
        """
        Загрузка обученной модели U-Net.

        Args:
            model_path (str): Путь к файлу .pth.

        Returns:
            bool: True, если модель успешно загружена, иначе False.
        """
        if not os.path.exists(model_path):
            logger.error(f"Файл модели не найден: {model_path}")
            self.model = None
            self.model_path = None
            return False

        try:
            logger.info(f"Загрузка модели из: {model_path}")
            # Создаем архитектуру модели (должна совпадать с обученной)
            self.model = smp.Unet(
                encoder_name=ENCODER,
                encoder_weights=ENCODER_WEIGHTS,
                in_channels=1,
                classes=CLASSES,
                activation=ACTIVATION,
            )
            # Загружаем веса
            # Добавляем map_location для корректной загрузки между CPU/GPU
            self.model.load_state_dict(torch.load(model_path, map_location=self.device))
            self.model.to(self.device)
            self.model.eval() # Переводим модель в режим оценки
            self.model_path = model_path
            logger.info("Модель успешно загружена и переведена в режим оценки.")
            return True
        except ImportError:
             logger.error("Библиотека segmentation-models-pytorch не найдена. Установите ее: pip install segmentation-models-pytorch")
             self.model = None
             self.model_path = None
             return False
        except Exception as e:
            logger.error(f"Ошибка при загрузке модели: {e}", exc_info=True)
            self.model = None
            self.model_path = None
            return False

    def _preprocess_slice(self, slice_hu):
        """
        Предобработка одного среза КТ (в единицах Хаунсфилда) перед подачей в модель.
        Эта функция должна ТОЧНО повторять предобработку из скрипта обучения.

        Args:
            slice_hu (np.ndarray): 2D массив среза в HU.

        Returns:
            torch.Tensor: Обработанный тензор среза [1, 1, IMG_SIZE, IMG_SIZE]
                          или None в случае ошибки.
        """
        try:
            # 1. Применение легочного окна -> [0, 1] float
            min_val = WINDOW_LEVEL - WINDOW_WIDTH / 2.0 # Используем float деление
            max_val = WINDOW_LEVEL + WINDOW_WIDTH / 2.0
            # Приводим к float32 перед clip, чтобы избежать ошибок типа данных
            slice_windowed = np.clip(slice_hu.astype(np.float32), min_val, max_val)
            if WINDOW_WIDTH == 0: width = 1.0 # Избегаем деления на ноль
            else: width = float(WINDOW_WIDTH)
            # Нормализация
            slice_normalized = (slice_windowed - min_val) / width
            # Убедимся, что результат в [0, 1] после нормализации
            slice_normalized = np.clip(slice_normalized, 0.0, 1.0)
            slice_normalized = slice_normalized.astype(np.float32) # Убедимся, что тип float32

            # 2. Ресайз до IMG_SIZE x IMG_SIZE с помощью OpenCV
            # Убедимся, что входные данные для cv2.resize имеют правильный тип и размерность
            if slice_normalized.ndim != 2:
                 logger.error(f"Неверная размерность входного среза для ресайза: {slice_normalized.ndim}")
                 return None

            slice_resized = cv2.resize(
                slice_normalized,
                (IMG_SIZE, IMG_SIZE),
                interpolation=cv2.INTER_LINEAR # Линейная интерполяция для изображения
            )

            # 3. Конвертация в тензор PyTorch [C, H, W]
            # Добавляем канал (C=1)
            slice_tensor = torch.from_numpy(slice_resized).float().unsqueeze(0) # [1, H, W]

            # 4. Добавление батча -> [1, 1, H, W]
            slice_tensor = slice_tensor.unsqueeze(0)

            # 5. Перенос на нужное устройство
            slice_tensor = slice_tensor.to(self.device)

            return slice_tensor

        except Exception as e:
            logger.error(f"Ошибка при предобработке среза: {e}", exc_info=True)
            return None

    def predict(self, slice_hu):
        """
        Выполнение предсказания (сегментации) для одного среза КТ.

        Args:
            slice_hu (np.ndarray): 2D массив среза в единицах Хаунсфилда.

        Returns:
            np.ndarray: Бинарная маска сегментации [H, W] того же размера, что и входной срез,
                        или None, если модель не загружена или произошла ошибка.
        """
        if self.model is None:
            # logger.warning("Модель сегментации не загружена.") # Убрали, т.к. проверяется перед вызовом
            return None
        if slice_hu is None:
            logger.warning("Входной срез для предсказания пуст (None).")
            return None
        if slice_hu.ndim != 2:
             logger.error(f"Неверная размерность входного среза для predict: {slice_hu.ndim}")
             return None

        original_shape = slice_hu.shape # Сохраняем исходный размер (height, width)

        # 1. Предобработка среза
        input_tensor = self._preprocess_slice(slice_hu)
        if input_tensor is None:
            return None

        # 2. Предсказание (inference)
        try:
            with torch.no_grad(): # Отключаем расчет градиентов
                output_logits = self.model(input_tensor) # [1, 1, IMG_SIZE, IMG_SIZE]

            # 3. Постобработка
            # Применяем порог 0.0 для логитов
            predicted_mask = (output_logits > 0.0).squeeze().cpu().numpy().astype(np.uint8) # [IMG_SIZE, IMG_SIZE]

            # 4. Ресайз маски до оригинального размера с помощью OpenCV
            # cv2.resize ожидает (width, height)
            mask_resized = cv2.resize(
                predicted_mask,
                (original_shape[1], original_shape[0]), # (width, height)
                interpolation=cv2.INTER_NEAREST # Ближайший сосед для маски (для бинарных масок)
            )

            # Убрали лог отсюда, чтобы не засорять при обработке объема
            # logger.info(f"Сегментация для среза выполнена. Размер маски: {mask_resized.shape}")
            return mask_resized

        except Exception as e:
            logger.error(f"Ошибка во время предсказания для одного среза: {e}", exc_info=True)
            return None

    def predict_volume(self, volume_hu, is_cancelled=None):
        """
        Выполнение предсказания (сегментации) для всего 3D объема КТ.

        Args:
            volume_hu (np.ndarray): 3D массив объема в единицах Хаунсфилда [Z, H, W].
            is_cancelled (callable, optional): Функция, возвращающая True, если процесс отменен.

        Returns:
            np.ndarray: 3D массив бинарных масок сегментации [Z, H, W]
                        или None, если модель не загружена или произошла ошибка/отмена.
        """
        if self.model is None:
            logger.error("Модель сегментации не загружена для обработки объема.")
            return None
        if volume_hu is None or volume_hu.ndim != 3:
            logger.error("Некорректные входные данные для predict_volume.")
            return None

        num_slices, height, width = volume_hu.shape
        logger.info(f"Начало сегментации объема из {num_slices} срезов...")

        # Создаем пустой массив для хранения масок
        volume_mask = np.zeros_like(volume_hu, dtype=np.uint8)
        error_occurred = False

        # Проверяем наличие атрибута signals перед использованием
        signals_available = hasattr(self, 'signals') and self.signals is not None

        for i in range(num_slices):
            # Проверяем флаг отмены
            if is_cancelled and is_cancelled():
                 logger.info(f"Сегментация объема отменена на срезе {i}/{num_slices}.")
                 return None # Возвращаем None при отмене

            slice_hu = volume_hu[i]
            # Выполняем предсказание для текущего среза
            mask = self.predict(slice_hu)

            if mask is not None:
                # Проверяем совпадение размеров на всякий случай
                if mask.shape == (height, width):
                    volume_mask[i] = mask
                else:
                    logger.warning(f"Размер маски ({mask.shape}) не совпадает с размером среза ({height}, {width}) для индекса {i}. Пропуск.")
            else:
                logger.warning(f"Не удалось сегментировать срез {i}. Маска будет пустой.")
                error_occurred = True

            # Отправляем сигнал о прогрессе каждые 5 срезов или на последнем
            if signals_available and ((i + 1) % 5 == 0 or i == num_slices - 1):
                 self.signals.progress.emit(i + 1, num_slices)

        if error_occurred:
            logger.warning("Во время сегментации объема возникли ошибки для некоторых срезов.")

        logger.info(f"Сегментация объема завершена. Возвращается массив масок формы {volume_mask.shape}")
        return volume_mask

    def cancel(self):
        """ Устанавливает флаг отмены для сегментации объема. """
        self._is_cancelled = True

