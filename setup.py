import codecs

from setuptools import setup, find_packages

with open("requirements.txt", "rb") as f:
    bruto = f.read()
if bruto.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
    texto = bruto.decode("utf-16")
else:
    texto = bruto.decode("utf-8-sig")
requirements = [l.strip() for l in texto.splitlines() if l.strip() and not l.strip().startswith("#")]

setup(
    name="sfcc-srv-tagstudio",
    version="0.1.0",
    packages=find_packages(exclude=["tests*"]),
    include_package_data=True,
    install_requires=requirements,
    python_requires=">=3.12",
)
