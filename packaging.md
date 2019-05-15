# Packaging Hints

## Define the release

	RELEASE=1.1.4

## PyPI upload

Build and upload::

	python setup.py sdist bdist_wheel
	twine upload dist/apstools-${RELEASE}*

## Conda upload

Build and upload::

	conda build ./conda-recipe/
	anaconda upload -u aps-anl-tag /home/mintadmin/Apps/anaconda/conda-bld/noarch/apstools-${RELEASE}-py_0.tar.bz2

### Conda channels

* `aps-anl-tag` production releases
* `aps-anl-dev` anything else, such as: pre-release, release candidates, or testing purposes