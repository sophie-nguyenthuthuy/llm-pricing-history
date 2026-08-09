.PHONY: refresh test

refresh:
	python3 refresh.py

test:
	python3 -m unittest discover -s tests -v
