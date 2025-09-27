
all: flake8 pylint

flake8:
	flake8

pylint:
	pylint --recursive=y .

run-dev-docker:
	docker run -ti --rm -v $$PWD:/app -w /app python:3 /bin/bash

prep-dev-docker:
	python3 -m pip install -r requirements.txt
	test -d edmc || git clone https://github.com/EDCD/EDMarketConnector.git edmc
