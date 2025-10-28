"""
Setup script for identity_interpreter package
"""

from setuptools import find_packages, setup


with open("requirements.txt") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="identity_interpreter",
    version="0.1.0",
    description="Identity Interpreter for Bartholomew AI",
    author="Taylor Paul",
    author_email="tpaul733@gmail.com",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "barth=identity_interpreter.cli:main",
        ],
    },
    include_package_data=True,
    package_data={
        "identity_interpreter": ["schema/*.json"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
