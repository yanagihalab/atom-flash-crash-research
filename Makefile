.PHONY: validate verify figures

validate:
	python3 scripts/validate_release.py --deep

verify:
	python3 scripts/verify_publication_extensions.py
	python3 scripts/verify_wallet_coordination.py

figures:
	python3 scripts/build_figures.py
