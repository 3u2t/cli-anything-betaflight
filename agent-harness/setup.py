from setuptools import find_namespace_packages, setup

setup(
    name="cli-anything-betaflight",
    version="1.0.0",
    description=(
        "Agent-native CLI for the Betaflight source tree: inspect boards, "
        "build and flash firmware, and talk MSP to a flight controller"
    ),
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "pyserial>=3.5",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-betaflight=cli_anything.betaflight.betaflight_cli:main",
        ],
    },
    package_data={
        "cli_anything.betaflight": ["skills/*.md"],
    },
    include_package_data=True,
    python_requires=">=3.10",
)
