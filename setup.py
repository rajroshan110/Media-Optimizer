from setuptools import setup, find_packages

setup(
    name="media-optimizer",
    version="1.0.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "media-optimizer = media_optimizer.cli:main",
            "media-optimizer-gui = media_optimizer.gui:launch_gui",
        ],
    },
    python_requires=">=3.9",
    install_requires=[
        "pillow>=10.0.0",
    ],
    extras_require={
        "benchmark": ["numpy>=1.20.0"],
    },
)
