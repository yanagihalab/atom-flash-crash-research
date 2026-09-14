.PHONY: validate verify verify-raw figures

validate:
	python3 scripts/validate_release.py --deep

verify:
	python3 scripts/verify_publication_extensions.py
	python3 scripts/verify_wallet_coordination.py --processed-only

verify-raw:
	python3 scripts/verify_control_windows.py
	python3 scripts/verify_cosmos_baseline_indexed.py
	python3 scripts/verify_wallet_coordination.py
	python3 scripts/verify_publication_circular_shift.py

figures:
	python3 scripts/build_figures.py
