#!/usr/bin/env python3
from setuptools import setup
from os.path import abspath, dirname

root = abspath(dirname(__file__))

# Get the long description from the README.md file.
with open(root + "/README.md", "r", encoding="utf-8") as fd:
    long_description = fd.read()

setup(
    name="kanga-cdaudio",
    version="0.1.0",
    description="Python 3 routines for handling CD audio",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/dacut/kanga-cdaudio",
    author="David Cuthbert",
    author_email="dacut@kanga.org",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Multimedia :: Sound/Audio :: CD Audio",
        "Topic :: Software Development :: Libraries :: Python Modules"
    ],
    keywords='compact-disc musicbrainz',
    packages=['kanga.cdaudio'],
    python_requires='>=3.6',
    install_requires=["musicbrainzngs", "requests"],
    setup_requires=["nose>=1.0"],
    tests_require=["coverage>=4.0", "nose>=1.0"],
)