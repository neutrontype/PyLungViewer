#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import os

# Получаем текущую директорию
here = os.path.abspath(os.path.dirname(__file__))

# Получаем содержимое README.md для длинного описания
with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

# Получаем зависимости из requirements.txt
with open(os.path.join(here, 'requirements.txt'), encoding='utf-8') as f:
    requirements = f.read().splitlines()

setup(
    name="pylungviewer",
    version="0.1.0",
    description="Просмотрщик и анализатор КТ снимков лёгких",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/pylungviewer",
    author="Your Name",
    author_email="your.email@example.com",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Healthcare Industry",
        "Topic :: Scientific/Engineering :: Medical Science Apps.",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    keywords="dicom, medical imaging, CT scans, lungs",
    packages=find_packages(include=["pylungviewer", "pylungviewer.*"]),
    python_requires=">=3.8",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "pylungviewer=pylungviewer.main:main",
        ],
    },
    include_package_data=True,
    package_data={
        "pylungviewer": ["resources/**/*"],
    },
    project_urls={
        "Bug Reports": "https://github.com/yourusername/pylungviewer/issues",
        "Source": "https://github.com/yourusername/pylungviewer",
    },
)