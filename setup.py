from setuptools import setup, find_packages

setup(
    name='cbpi4-ActorTempStep',
    version='1.0.0',
    description='CBPI4 plugin: Actor control step triggered by temperature with optional delay',
    author='Sammy Brewery',
    author_email='',
    url='https://github.com/zzorrro/cbpi4-ActorTempStep',
    license='MIT',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        # Если бы у нас были статические файлы (HTML, CSS, JS), мы бы указали их здесь
        # 'cbpi4-ActorTempStep': ['static/*']
    },
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Topic :: Home Automation',
    ],
    python_requires='>=3.7',
)