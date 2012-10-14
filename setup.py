from setuptools import setup

setup(
    name='m9utils',
    version='1.0',
    packages=['mdng'],
    scripts=['bin/pixelfix'],
    install_requires=[],

    # meta
    author='Ben Dyer',
    author_email='ben_dyer@mac.com',
    url='https://github.com/bendyer/m9util',
    license='LICENSE',
    description='Image metadata processing scripts for the Leica M9[-P]',
    long_description=open('README.md').read(),
)
