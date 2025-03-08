from setuptools import setup, find_packages

setup(
    name="ipc-ynab-lambda",
    version="0.1.0",
    packages=find_packages(include=['src', 'src.*']),
    install_requires=[
        "requests==2.31.0",
        "boto3==1.34.34"
    ],
) 