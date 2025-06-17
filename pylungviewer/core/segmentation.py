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
from PyQt5.QtCore import QObject, pyqtSignal 

logger = logging.getLogger(__name__)

IMG_SIZE = 256
WINDOW_LEVEL = -600
WINDOW_WIDTH = 1500
ENCODER = 'resnet34' 
ENCODER_WEIGHTS = None 
CLASSES = 1
ACTIVATION = None 

class SegmentationSignals(QObject):
    progress = pyqtSignal(int, int) 

class LungSegmenter:
    """Класс для выполнения сегментации легких."""

    def __init__(self, model_path=None):
        """
        Инициализация сегментатора.

        Args:
            model_path (str, optional): Путь к файлу модели (.pth). Defaults to None.
        """
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.model_path = None
        self.signals = SegmentationSignals() 
        self._is_cancelled = False 
        logger.info(f"Используемое устройство для сегментации: {self.device}")


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
            self.model = smp.Unet(
                encoder_name=ENCODER,
                encoder_weights=ENCODER_WEIGHTS,
                in_channels=1,
                classes=CLASSES,
                activation=ACTIVATION,
            )

            self.model.load_state_dict(torch.load(model_path, map_location=self.device))
            self.model.to(self.device)
            self.model.eval() 
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
            min_val = WINDOW_LEVEL - WINDOW_WIDTH / 2.0 
            max_val = WINDOW_LEVEL + WINDOW_WIDTH / 2.0
            slice_windowed = np.clip(slice_hu.astype(np.float32), min_val, max_val)
            if WINDOW_WIDTH == 0: width = 1.0
            else: width = float(WINDOW_WIDTH)
            slice_normalized = (slice_windowed - min_val) / width
            slice_normalized = np.clip(slice_normalized, 0.0, 1.0)
            slice_normalized = slice_normalized.astype(np.float32)

            # 2. Ресайз до IMG_SIZE x IMG_SIZE с помощью OpenCV
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
            return None
        if slice_hu is None:
            logger.warning("Входной срез для предсказания пуст (None).")
            return None
        if slice_hu.ndim != 2:
             logger.error(f"Неверная размерность входного среза для predict: {slice_hu.ndim}")
             return None

        original_shape = slice_hu.shape 

        input_tensor = self._preprocess_slice(slice_hu)
        if input_tensor is None:
            return None

        try:
            with torch.no_grad(): # Отключаем расчет градиентов
                output_logits = self.model(input_tensor) # [1, 1, IMG_SIZE, IMG_SIZE]

            predicted_mask = (output_logits > 0.0).squeeze().cpu().numpy().astype(np.uint8) # [IMG_SIZE, IMG_SIZE]

            mask_resized = cv2.resize(
                predicted_mask,
                (original_shape[1], original_shape[0]), # (width, height)
                interpolation=cv2.INTER_NEAREST # Ближайший сосед для маски (для бинарных масок)
            )

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
            if is_cancelled and is_cancelled():
                 logger.info(f"Сегментация объема отменена на срезе {i}/{num_slices}.")
                 return None 

            slice_hu = volume_hu[i]
            mask = self.predict(slice_hu)

            if mask is not None:

                if mask.shape == (height, width):
                    volume_mask[i] = mask
                else:
                    logger.warning(f"Размер маски ({mask.shape}) не совпадает с размером среза ({height}, {width}) для индекса {i}. Пропуск.")
            else:
                logger.warning(f"Не удалось сегментировать срез {i}. Маска будет пустой.")
                error_occurred = True

            if signals_available and ((i + 1) % 5 == 0 or i == num_slices - 1):
                 self.signals.progress.emit(i + 1, num_slices)

        if error_occurred:
            logger.warning("Во время сегментации объема возникли ошибки для некоторых срезов.")

        logger.info(f"Сегментация объема завершена. Возвращается массив масок формы {volume_mask.shape}")
        return volume_mask

    def cancel(self):
        """ Устанавливает флаг отмены для сегментации объема. """
        self._is_cancelled = True

